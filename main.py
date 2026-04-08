# -*- coding: utf-8 -*-
import asyncio
import datetime as dt
import logging
import httpx
import pandas as pd
import schedule
from core.test_db_config import DATABASE
from logging.handlers import TimedRotatingFileHandler
from services.kma_service import get_raw_dataset, get_processed_data
from services.dispersion import simulate_source, SOURCES
from api.database import OdorDBManager
from core.config import HTTP_TIMEOUT, LOCATIONS

logger = logging.getLogger("odor")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')

if not logger.handlers:
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    
    file_handler = TimedRotatingFileHandler(
        'odor_forecast.log', when='midnight', interval=1, backupCount=30, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
logger.propagate = False

is_running = False
db_manager = OdorDBManager()

async def job(client: httpx.AsyncClient):
    """기상 데이터 수집, 가공 및 DB 적재 메인 작업"""
    global is_running
    if is_running:
        logger.warning("이전 작업이 아직 완료되지 않았습니다.")
        return
    
    is_running = True
    logger.info(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 데이터 수집 및 가공 시작")
    hours = 48
    try:
        dataset, tmfc, prev_dataset = await get_raw_dataset(hours, client, DATABASE)
        if not dataset:
            logging.info("데이터가 없습니다.")
            return
        
        # -----------------------------------------------
        # 1. 원본 데이터 적재
        # -----------------------------------------------
        raw_data_list = []
        created_at = dt.datetime.now()

        for hf, sites in dataset.items():
            for site_name, (kp, adj, u_parsed) in sites.items():
                raw_data_list.append((
                    tmfc, hf, site_name,
                    kp.t2m,
                    kp.rh2m,
                    u_parsed.get("u10m", 0.0),
                    u_parsed.get("v10m", 0.0),
                    u_parsed.get("hpbl", 0.0),
                    kp.hgt500, kp.tmp500,
                    kp.hgt850, kp.tmp850,
                    u_parsed.get("u80m", 0.0),
                    u_parsed.get("v80m", 0.0),
                    kp.ugrd500, kp.ugrd850,
                    kp.vgrd500, kp.vgrd850,
                    created_at
                ))

        if raw_data_list:
            db_manager.insert_odor_raw_data(raw_data_list)
            logger.info(f"원시 데이터(KIM Raw) {len(raw_data_list)}건 적재 완료.")

        # -----------------------------------------------
        # 2. 가공 데이터 (악취점수) 적재
        # -----------------------------------------------
        result_dict = await get_processed_data(dataset, tmfc, hours, prev_dataset)

        history_list = []
        if result_dict and result_dict.get("history_list"):
            history_list = result_dict["history_list"]
            summary = result_dict["summary"]
            db_manager.insert_odor_processed_data(history_list, summary)
            logger.info(f"가공 데이터(Odor History) {len(history_list)}건 적재 완료.")
        else:
            logger.info("적재할 가공 데이터가 없습니다.")

        # -----------------------------------------------
        # 3. 확산 예보 데이터 적재
        # -----------------------------------------------
        base_time = dt.datetime.strptime(tmfc, "%Y%m%d%H")

        # dataset에서 plume용 DataFrame 구성
        plume_rows = []
        for hf, sites in dataset.items():
            if hf > hours:
                continue
            for site_name, (kp, adj, u_parsed) in sites.items():
                time_kst = (base_time + dt.timedelta(hours=hf + 9)).strftime("%Y-%m-%d %H:00")
                plume_rows.append({
                    "site": site_name,
                    "time_kst": time_kst,
                    "dir10": adj["wind_dir_10m_deg"],
                    "spd10_adj": adj["wind_speed_10m_adj"],
                    "spd20_adj": adj["wind_speed_20m_adj"],
                    "u80m": u_parsed.get("u80m", 0.0),
                    "v80m": u_parsed.get("v80m", 0.0),
                })
        df_plume = pd.DataFrame(plume_rows)

        dispersion_list = []
        created_at = dt.datetime.now()

        for src_name, src_cfg in SOURCES.items():
            hourly_rows, _, _, _, _ = simulate_source(df_plume, src_name, src_cfg)

            for row in hourly_rows:
                site = row["site_for_wind"]
                time_kst = pd.Timestamp(row["time_kst"]).strftime("%Y-%m-%d %H:00")

                # hf 역산
                try:
                    row_time = dt.datetime.strptime(time_kst, "%Y-%m-%d %H:%M")
                except ValueError:
                    row_time = dt.datetime.strptime(time_kst, "%Y-%m-%d %H:%M:%S")
                hf = int((row_time - base_time).total_seconds() / 3600) - 9

                # odor_risk_score, judge 매칭
                matched_score = next(
                    (h for h in history_list if h[3] == time_kst and h[2] == site),
                    None
                )
                odor_risk_score = matched_score[5] if matched_score else 0.0
                judge = matched_score[6] if matched_score else "데이터 없음"

                dispersion_list.append((
                    tmfc,
                    hf,
                    time_kst,
                    site,
                    LOCATIONS[site]["lat"],
                    LOCATIONS[site]["lon"],
                    row["dir_eff_to_deg"],
                    row["spd_eff"],
                    odor_risk_score,
                    judge,
                    created_at
                ))

        if dispersion_list:
            db_manager.insert_odor_dispersion_forecast(dispersion_list)
            logger.info(f"확산 예보 데이터 적재 완료: {len(dispersion_list)}건")

    except Exception as e:
        logger.error(f"작업 중 오류 발생: {e}", exc_info=True)
    finally:
        is_running = False

def run_job_async(client: httpx.AsyncClient):
    """스케줄러에서 비동기 작업을 호출하기 위한 래퍼"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(job(client))
    except RuntimeError:
        asyncio.run(job(client))

async def scheduler_loop(client: httpx.AsyncClient):
    """KMA KIM 모델 업데이트 주기에 맞춘 스케줄러 루프"""
    await job(client)

    schedule.every().day.at("01:05").do(run_job_async, client=client)
    schedule.every().day.at("07:05").do(run_job_async, client=client)
    schedule.every().day.at("13:05").do(run_job_async, client=client)
    schedule.every().day.at("19:05").do(run_job_async, client=client)

    logger.info("스케줄러 루프가 가동되었습니다.")
    while True:
        schedule.run_pending()
        await asyncio.sleep(1)

async def main():
    # db_manager.migrate_location_names()  
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        await asyncio.gather(scheduler_loop(client))

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("시스템이 사용자에 의해 종료되었습니다.")