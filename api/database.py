# -*- coding: utf-8 -*-
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
import logging
import json
from datetime import datetime
from collections import Counter
from typing import Dict, List, Any
from core.db_config import DATABASE
from utils.data_models import KimPoint
from core.config import LOCATIONS

def get_latest_tmfc(db_config: dict):
        """db에 적재된 마지막 tmfc"""
        conn = None
        cur = None
        try:
            conn = psycopg2.connect(**db_config)
            cur = conn.cursor()
            query = "SELECT MAX(tmfc) FROM odor_forecast_history;"
            cur.execute(query)
            result = cur.fetchone()

            return result[0] if result and result[0] else None

        except Exception as e:
            logging.error(f"DB 조회 오류: {e}")
            return None

        finally:
            if conn:
                cur.close()
                conn.close()

class OdorDBManager:
    def __init__(self):
        self.db_config = DATABASE

    def calculate_summary(self, rows):
        """DB에서 조회된 데이터(리스트)를 기반으로 요약 통계 계산"""
        if not rows:
            return None
        
        scores = [r.get('total_score', 0) for r in rows]
        grades = [r.get('judge', '데이터 없음') for r in rows]
        
        grade_counts = Counter(grades)
        most_common_grade = grade_counts.most_common(1)[0][0] if grades else "데이터 없음"
        
        return {
            "max_score": round(max(scores), 1) if scores else 0,
            "min_score": round(min(scores), 1) if scores else 0,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "most_common_grade": most_common_grade,
            "total_steps": len(rows)
        }
    
    def migrate_location_names(self):
        """지점명 일괄 변경 - 최초 1회만 실행"""
        conn = None
        try:
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor()
            cur.execute("UPDATE odor_forecast_history SET location = '중부' WHERE location = '중심';")
            cur.execute("UPDATE odor_forecast_history SET location = '동부' WHERE location = '동측';")
            cur.execute("UPDATE odor_forecast_history SET location = '서부' WHERE location = '서측';")
            cur.execute("UPDATE odor_forecast_history SET location = '남부' WHERE location = '남측';")
            cur.execute("UPDATE odor_forecast_history SET location = '북부' WHERE location = '북측';")
            cur.execute("UPDATE kim_forecast_api_data SET location_name = '중부' WHERE location_name = '중심';")
            cur.execute("UPDATE kim_forecast_api_data SET location_name = '동부' WHERE location_name = '동측';")
            cur.execute("UPDATE kim_forecast_api_data SET location_name = '서부' WHERE location_name = '서측';")
            cur.execute("UPDATE kim_forecast_api_data SET location_name = '남부' WHERE location_name = '남측';")
            cur.execute("UPDATE kim_forecast_api_data SET location_name = '북부' WHERE location_name = '북측';")
            conn.commit()
            logging.info("지점명 마이그레이션 완료")
        except Exception as e:
            if conn: conn.rollback()
            logging.error(f"마이그레이션 오류: {e}")
        finally:
            if conn:
                cur.close()
                conn.close()
    
    def insert_odor_raw_data(self, data_list):
            """원시데이터 적재"""
            if not data_list:
                logging.info("적재할 데이터가 없습니다.")
                return
            conn = None
            try:
                conn = psycopg2.connect(**self.db_config)
                cur = conn.cursor()
                
                # 순서: tmfc, hf, location_name, t2m, rh2m, u10m, v10m, hpbl, hgt500, tmp500, hgt850, tmp850, u80m, v80m, created_at
                query = """
                    INSERT INTO kim_forecast_api_data (
                        tmfc, hf, location_name, t2m, rh2m, u10m, v10m, 
                        hpbl, hgt500, tmp500, hgt850, tmp850, u80m, v80m, ugrd500, ugrd850, vgrd500, vgrd850, created_at
                    )
                    VALUES %s
                    ON CONFLICT (tmfc, hf, location_name) 
                    DO UPDATE SET 
                        t2m = EXCLUDED.t2m, rh2m = EXCLUDED.rh2m, 
                        u10m = EXCLUDED.u10m, v10m = EXCLUDED.v10m, 
                        hpbl = EXCLUDED.hpbl, 
                        hgt500 = EXCLUDED.hgt500, tmp500 = EXCLUDED.tmp500, 
                        hgt850 = EXCLUDED.hgt850, tmp850 = EXCLUDED.tmp850, u80m = EXCLUDED.u80m, v80m = EXCLUDED.v80m, ugrd500 = EXCLUDED.ugrd500, ugrd850 = EXCLUDED.ugrd850,
                        vgrd500 = EXCLUDED.vgrd500, vgrd850 = EXCLUDED.vgrd850,
                        created_at = EXCLUDED.created_at;
                """
                execute_values(cur, query, data_list)
                conn.commit()
                logging.info(f"DB 데이터 적재 완료: {len(data_list)}건")
            except Exception as e:
                if conn: conn.rollback()
                logging.error(f"DB 연동 오류: {e}")
            finally:
                if conn:
                    cur.close()
                    conn.close()

    def insert_odor_processed_data(self, data_list,summary_dict):
        """가공된 데이터 적재"""
        if not data_list: return
        conn = None
        try:
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor()
            
            # 테이블 컬럼 순서: tmfc, hf, location_name, fcst_time_kst, total_score, judge, odor_code, t2m, ws10, score_details, input_values, created_at, rhm, pblh
            # total_score -> odor_risk_score, rhm -> rh2m, odor_code -> code, pblh -> pbld, wind_speed_10m -> ws10, location_name -> location
            query = """
            INSERT INTO odor_forecast_history (
                tmfc, location, lat, lon, hf, fcst_time_kst, fcst_time_utc, 
                code, season, odor_risk_score, judge, upper_score, surface_score, inversion_score,
                diffusion_score, pbld, rh2m, t2m, wind_dir_10m, wind_speed_10m,
                input_values, score_details, summary, created_at
            )
            VALUES %s
            ON CONFLICT (tmfc, hf, location) 
            DO UPDATE SET 
                odor_risk_score = EXCLUDED.odor_risk_score,
                judge = EXCLUDED.judge,
                code = EXCLUDED.code,
                season = EXCLUDED.season,
                t2m = EXCLUDED.t2m,
                wind_speed_10m = EXCLUDED.wind_speed_10m,
                rh2m = EXCLUDED.rh2m,
                pbld = EXCLUDED.pbld,
                input_values = EXCLUDED.input_values,
                score_details = EXCLUDED.score_details,
                summary = EXCLUDED.summary,
                created_at = EXCLUDED.created_at;
            """
            optimized_data = []
            summary_json = json.dumps(summary_dict, ensure_ascii=False)
            for d in data_list:
                filtered_row = (
                d[0],   # tmfc
                d[2],   # location (loc_name)
                d[13],  # lat
                d[14],  # lon
                d[1],   # hf
                d[3],   # fcst_time_kst
                d[4],   # fcst_time_utc
                d[7],   # code
                d[12],  # season
                d[5],   # odor_risk_score (final_score)
                d[6],   # judge
                d[8],   # upper_score
                d[9],   # surface_score
                d[10],  # inversion_score
                d[11],  # diffusion_score
                d[18],  # pbld (hpbl)
                d[17],  # rh2m
                d[15],  # t2m
                d[19],  # wind_dir_10m (wd10)
                d[16],  # wind_speed_10m (ws10)
                d[29],  # input_values (JSON)
                d[28],  # score_details (JSON)
                summary_json, # summary (전체 공통 요약 JSON)
                d[30]   # created_at (datetime.now)                  
                )
                optimized_data.append(filtered_row)

            execute_values(cur, query, optimized_data)
            conn.commit()
            logging.info(f"History 적재 완료: {len(optimized_data)}건")
        except Exception as e:
            if conn: conn.rollback()
            logging.error(f"History 적재 오류: {e}")
        finally:
            if conn:
                cur.close()
                conn.close()

    
    def insert_odor_dispersion_forecast(self, data_list):
        """확산 예보 데이터 적재"""
        if not data_list: return
        conn = None
        try:
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor()
            query = """
                INSERT INTO odor_dispersion_forecast (
                    tmfc, hf, fcst_time_kst, site,
                    site_lat, site_lon,
                    wind_dir_eff, wind_speed_eff,
                    odor_risk_score, judge,
                    created_at
                )
                VALUES %s
                ON CONFLICT (tmfc, hf, site)
                DO UPDATE SET
                    wind_dir_eff = EXCLUDED.wind_dir_eff,
                    wind_speed_eff = EXCLUDED.wind_speed_eff,
                    odor_risk_score = EXCLUDED.odor_risk_score,
                    judge = EXCLUDED.judge,
                    created_at = EXCLUDED.created_at;
            """
            execute_values(cur, query, data_list)
            conn.commit()
            logging.info(f"확산 예보 데이터 적재 완료: {len(data_list)}건")
        except Exception as e:
            if conn: conn.rollback()
            logging.error(f"확산 예보 적재 오류: {e}")
        finally:
            if conn:
                cur.close()
                conn.close()
    
    def get_prev_tmfc_dataset(self, prev_tmfc: str) -> Dict[int, Dict[str, Any]]:
        """이전 tmfc 데이터를 DB에서 조회 (hf 6~17)"""
        conn = None
        try:
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT hf, location_name AS site,
                    t2m, rh2m, u10m, v10m, u80m, v80m, hpbl,
                    hgt500, tmp500, hgt850, tmp850,
                    ugrd500, vgrd500, ugrd850, vgrd850
                FROM kim_forecast_api_data
                WHERE tmfc = %s AND hf BETWEEN 6 AND 17
                ORDER BY hf, location_name;
            """, (prev_tmfc,))
            rows = cur.fetchall()
            if not rows:
                return {}

            dataset = {}
            for r in rows:
                hf = r["hf"]
                site = r["site"]
                if hf not in dataset:
                    dataset[hf] = {}
                # KimPoint 생성 (상층 데이터 없이 지표면만)
                kp = KimPoint(
                    tmfc=prev_tmfc, hf=hf, site=site,
                    lat=0.0, lon=0.0,  # 좌표는 LOCATIONS에서 가져와도 됨
                    t2m=r["t2m"], rh2m=r["rh2m"],
                    u10m=r["u10m"], v10m=r["v10m"],
                    hpbl=r["hpbl"],
                    hgt500=r["hgt500"], tmp500=r["tmp500"],
                    hgt850=r["hgt850"], tmp850=r["tmp850"],
                    u80m=r["u80m"], v80m=r["v80m"],
                    ugrd500=r["ugrd500"] or 0.0,
                    vgrd500=r["vgrd500"] or 0.0,
                    ugrd850=r["ugrd850"] or 0.0,
                    vgrd850=r["vgrd850"] or 0.0, 
                    p_data={}
                )
                dataset[hf][site] = (kp, None, {
                    "t2m": r["t2m"], "rh2m": r["rh2m"],
                    "u10m": r["u10m"], "v10m": r["v10m"],
                    "u80m": r["u80m"], "v80m": r["v80m"],
                    "hpbl": r["hpbl"],
                })
            return dataset

        except Exception as e:
            logging.error(f"이전 tmfc DB 조회 오류: {e}")
            return {}
        finally:
            if conn:
                cur.close()
                conn.close()
