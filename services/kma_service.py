# -*- coding: utf-8 -*-
import asyncio
import datetime as dt
import httpx
import logging
import math
import json
from services.adjustment import adjust_weather
from typing import Dict, Tuple, Any, Optional
from core.config import *
from api.database import get_latest_tmfc
from utils.data_models import KimPoint, calc_wd
from services.odor_scoring import calculate_odor_score, circular_diff_deg

logger = logging.getLogger("odor")

semaphore = asyncio.Semaphore(MAX_CONCURRENCY)


# ============================================================
# 파싱
# ============================================================

def parse_kma_pt_text(text: str) -> Dict[str, float]:
    """기상청 텍스트 응답 파싱"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    name_to_var = {}
    for ln in lines:
        if not ln or ln.startswith("#") or not ln[0].isdigit():
            continue
        toks = ln.split()
        if len(toks) >= 6:
            raw_name = toks[-1].split('(')[0].strip().lower()
            try:
                val = float(toks[4])
                name_to_var[raw_name] = val
            except ValueError:
                continue
    return name_to_var


# ============================================================
# 상층 데이터 수집 
# ============================================================

async def _fetch_pressure_vals(client: httpx.AsyncClient, tmfc: str, hf: int, lat: float, lon: float) -> Dict[str, Dict[str, float]]:
    p_params = {
        "group": KIM_GROUP, "nwp": KIM_NWP, "data": "P",
        "name": "hgt,T,u,v", "level": "975,950,925,850,500",
        "tmfc": tmfc, "hf": str(hf),
        "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "disp": "A",
        "authKey": KMA_AUTH_KEY,
    }
    for attempt in range(RETRY):
        async with semaphore:
            try:
                resp = await client.get(KMA_BASE_URL, params=p_params)
                if resp.status_code != 200:
                    if attempt < RETRY - 1:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    return {}
                if "file is not exist" in resp.text:
                    return {}
                logger.debug(f"상층 응답: {resp.text[:200]}")
                lines = [ln.strip() for ln in resp.text.splitlines() if ln.strip()]
                all_data = {}
                for ln in lines:
                    if not ln or ln.startswith("#") or not ln[0].isdigit():
                        continue
                    toks = ln.split()
                    if len(toks) >= 6:
                        raw_level = toks[3]
                        level_val = int(raw_level)
                        hpa_level = str(level_val // 100) if level_val >= 10000 else str(level_val)
                        val = float(toks[4])
                        raw_name = toks[5].split('(')[0].strip().lower()
                        name_map = {"t": "tmp", "hgt": "hgt", "gh": "hgt", "u": "u", "v": "v"}
                        var_key = name_map.get(raw_name, raw_name)
                        if hpa_level not in all_data:
                            all_data[hpa_level] = {"hgt": 0.0, "tmp": 0.0, "u": 0.0, "v": 0.0}
                        if var_key == "tmp" and val > 100:
                            val = round(val - 273.15, 2)
                        all_data[hpa_level][var_key] = val
                return all_data
            except Exception as e:
                logger.warning(f"통신 오류 {e} | HF {hf}, 재시도 {attempt+1}/{RETRY}")
                if attempt < RETRY - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                return {}
    return {}


# ============================================================
# 단일 격자 수집 (실행부분)
# ============================================================

async def worker(hf: int, site: str, lat: float, lon: float, tmfc: str, client: httpx.AsyncClient) -> Tuple[int, str, Optional[KimPoint], Optional[dict], Optional[dict]]:
    async with semaphore:
        u_params = {
            "group": KIM_GROUP, "nwp": KIM_NWP, "data": "U",
            "name": "t2m,rh2m,u10m,v10m,hpbl,u80m,v80m",
            "tmfc": tmfc, "hf": str(hf),
            "lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
            "disp": "A", "authKey": KMA_AUTH_KEY
        }
        try:
            u_res = await client.get(KMA_BASE_URL, params=u_params)
            if "file is not exist" in u_res.text:
                return hf, site, None, None, None
            u_parsed = parse_kma_pt_text(u_res.text)
            if u_parsed.get("t2m", 0.0) == 0.0 and u_parsed.get("rh2m", 70) == 70:
                logger.warning(f"HF {hf} {site}: t2m=0 응답: {u_res.text[:300]}")
        except Exception as e:
            logger.warning(f"{tmfc} HF {hf} {site}: 하층 데이터 누락 {e}")
            return hf, site, None, None, None

    all_p_data = await _fetch_pressure_vals(client, tmfc, hf, lat, lon)
    if not all_p_data:
        logger.warning(f"{tmfc} HF {hf} {site}: 상층 데이터 누락")
        return hf, site, None, None, None

    p_data = {}
    for lvl in ["975", "950", "925", "850", "500"]:
        level_val = all_p_data.get(lvl)
        if not level_val:
            return hf, site, None, None, None
        p_data[lvl] = level_val

    try:
        ws10_raw = math.hypot(u_parsed.get("u10m", 0.0), u_parsed.get("v10m", 0.0))
        wd10_raw = calc_wd(u_parsed.get("u10m", 0.0), u_parsed.get("v10m", 0.0))
        base_time = dt.datetime.strptime(tmfc, "%Y%m%d%H")
        target_time_kst = (base_time + dt.timedelta(hours=hf + 9)).strftime("%Y-%m-%d %H:00")

        adj = adjust_weather(
            site=site,
            t2m=u_parsed.get("t2m", 0.0),
            rh2m=u_parsed.get("rh2m", 70.0),
            wind_speed_10m_ms=ws10_raw,
            wind_dir_10m_deg=wd10_raw,
            time_kst=target_time_kst,
        )
        kp = KimPoint(
            tmfc=tmfc, hf=hf, site=site, lat=lat, lon=lon,
            t2m=u_parsed.get("t2m", 0.0),
            rh2m=u_parsed.get("rh2m", 70.0),
            u10m=u_parsed.get("u10m", 0.0),
            v10m=u_parsed.get("v10m", 0.0),
            hpbl=u_parsed.get("hpbl", 500.0),
            u80m=u_parsed.get("u80m", 0.0),
            v80m=u_parsed.get("v80m", 0.0),
            ugrd500=p_data["500"]["u"], vgrd500=p_data["500"]["v"],
            ugrd850=p_data["850"]["u"], vgrd850=p_data["850"]["v"],
            ugrd975=p_data["975"]["u"], vgrd975=p_data["975"]["v"],
            ugrd950=p_data["950"]["u"], vgrd950=p_data["950"]["v"],
            tmp975=p_data["975"]["tmp"], tmp950=p_data["950"]["tmp"], tmp925=p_data["925"]["tmp"],
            hgt850=p_data["850"]["hgt"], tmp850=p_data["850"]["tmp"],
            hgt500=p_data["500"]["hgt"], tmp500=p_data["500"]["tmp"],
            p_data=all_p_data
        )
        return hf, site, kp, adj, u_parsed
    except Exception as e:
        logger.error(f"기상데이터 생성 실패 {e}")
        return hf, site, None, None, None


# ============================================================
# tmfc별 데이터 수집 (재시도)
# ============================================================

async def _collect_dataset(tmfc: str, max_hf: int, client: httpx.AsyncClient) -> Dict[int, Dict[str, Any]]:
    """특정 tmfc의 hf 0~max_hf 데이터 수집"""
    target_count = (max_hf + 1) * len(MAIN_SITES)
    for attempt in range(RETRY):
        dataset, tasks = {}, []
        for hf in range(0, max_hf + 1):
            for site in MAIN_SITES:
                loc = LOCATIONS[site]
                tasks.append(worker(hf, site, loc["lat"], loc["lon"], tmfc, client))
        results = await asyncio.gather(*tasks)
        current_valid_count = 0
        for hf_val, site_val, kp_obj, adj_obj, u_parsed_obj in results:
            if kp_obj:
                current_valid_count += 1
                if hf_val not in dataset:
                    dataset[hf_val] = {}
                dataset[hf_val][site_val] = (kp_obj, adj_obj, u_parsed_obj)
        if current_valid_count >= target_count:
            logger.info(f"데이터 완성: tmfc={tmfc}, count={current_valid_count}")
            return dataset
        else:
            if attempt < RETRY - 1:
                wait_sec = 300
                logger.warning(f"데이터 미완성 {current_valid_count}/{target_count}. {wait_sec}초 후 재시도")
                await asyncio.sleep(wait_sec)
            else:
                logger.error(f"최대 재시도 후에도 데이터 미완성. 수집된 {len(dataset)}시간 분량만 사용")
                return dataset
    return {}


# ============================================================
# 메인 수집 (kim_forecast_api_data)
# ============================================================

async def get_raw_dataset(hours: int, client: httpx.AsyncClient, database: Any) -> Tuple[Dict, str, Dict]:
    while True:
        now_utc = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)
        safe_now = now_utc - dt.timedelta(hours=5)
        base_tmfc = safe_now.replace(hour=(safe_now.hour // 6) * 6, minute=0, second=0, microsecond=0)
        tmfc_candidates = [(base_tmfc - dt.timedelta(hours=i*6)).strftime("%Y%m%d%H") for i in range(4)]
        wait_min = 30
        db_tmfc = get_latest_tmfc(database)
        processed_in_this_loop = False

        for tmfc in tmfc_candidates:
            if db_tmfc == tmfc:
                logger.info(f"조회하려는 {tmfc}가 DB 적재된 발표시각과 같아 30분 대기")
                processed_in_this_loop = True
                break

            check_loc = LOCATIONS[MAIN_SITES[0]]
            check_params = {
                "group": KIM_GROUP, "nwp": KIM_NWP, "data": "U",
                "name": "t2m", "tmfc": tmfc, "hf": "0",
                "lat": f"{check_loc['lat']:.6f}", "lon": f"{check_loc['lon']:.6f}",
                "authKey": KMA_AUTH_KEY, "disp": "A"
            }
            async with semaphore:
                try:
                    check_res = await client.get(KMA_BASE_URL, params=check_params)
                    if "file is not exist" in check_res.text:
                        logger.info(f"{tmfc} 데이터 미생성, 다음 시간 조회")
                        continue
                except Exception:
                    continue

            processed_in_this_loop = True

            # 현재 tmfc 수집
            logger.info(f"현재 tmfc({tmfc}) 데이터 수집 시작")
            dataset = await _collect_dataset(tmfc, hours, client)

            # 이전 tmfc 데이터 - DB 우선 조회
            prev_tmfc = (dt.datetime.strptime(tmfc, "%Y%m%d%H") - dt.timedelta(hours=6)).strftime("%Y%m%d%H")
            
            from api.database import OdorDBManager
            db_manager = OdorDBManager()
            prev_dataset = db_manager.get_prev_tmfc_dataset(prev_tmfc)

            if prev_dataset:
                logger.info(f"이전 tmfc({prev_tmfc}) DB에서 로드 완료")
            else:
                logger.info(f"이전 tmfc({prev_tmfc}) DB 없음, API 수집 시작")
                prev_dataset = await _collect_dataset(prev_tmfc, 17, client)   ## 0 ~ 17 까지 만약 api수집한다면 18 * 5 * 2의 추가 api 조회
                logger.info(f"이전 tmfc({prev_tmfc}) API 수집 완료")

            if dataset:
                return dataset, tmfc, prev_dataset
            break

        if not processed_in_this_loop or (db_tmfc in tmfc_candidates):
            await asyncio.sleep(wait_min * 60)


# ============================================================
# 점수 계산 및 가공 (odor_forecast_history)
# ============================================================

async def get_processed_data(dataset: Dict, used_tmfc: str, hours: int, prev_dataset: Dict = None) -> Dict[str, Any]:
    if not dataset:
        logger.info("수집할 신규 데이터가 없음")
        return {"history_list": [], "used_tmfc": used_tmfc, "summary": {}}

    history_list = []
    base_time = dt.datetime.strptime(used_tmfc, "%Y%m%d%H")
    sorted_hf = sorted([h for h in dataset.keys() if h <= hours])

    for hf in sorted_hf:
        current_map = dataset.get(hf)
        if not current_map:
            continue

        # 12시간 전 데이터 결정
        if hf >= 12:
            # 현재 tmfc 내에서 hf - 12
            prev_map = dataset.get(hf - 12)
        else:
            # hf 0~11: 이전 tmfc의 hf + 6 사용
            # tmfc 간격 6시간 → 이전 tmfc hf+6 = 현재 tmfc hf의 12시간 전
            prev_hf = hf + 6
            prev_map = prev_dataset.get(prev_hf) if prev_dataset else None
            if prev_map is None:
                logger.warning(f"HF {hf}: 이전 tmfc 데이터 없음, 변화량 0으로 처리")

        current_kp_map = {site: kp for site, (kp, _, _) in current_map.items()}
        prev_kp_map = {site: kp for site, (kp, _, _) in prev_map.items()} if prev_map else None

        for site_name, kp in current_kp_map.items():
            prev_kp = prev_kp_map.get(site_name) if prev_kp_map else None
            kp.compute_derived(prev_kp=prev_kp)

        try:
            score_result = calculate_odor_score(
                current=current_kp_map,
                previous=prev_kp_map,
                month=base_time.month
            )

            inputs = score_result.get("input_values", {})
            ddir500 = float(inputs.get("ddir500", 0.0))
            ddir850 = float(inputs.get("ddir850", 0.0))
            dspd500 = float(inputs.get("dspd500", 0.0))
            dspd850 = float(inputs.get("dspd850", 0.0))

            for loc_name in current_map.keys():
                site_kp = current_kp_map[loc_name]
                target_time_kst = (base_time + dt.timedelta(hours=hf + 9)).strftime("%Y-%m-%d %H:00")
                fcst_time_utc = base_time.strftime("%Y-%m-%d %H:00")

                data_tuple = (
                    used_tmfc, hf, loc_name, target_time_kst, fcst_time_utc,
                    score_result.get("final_score", 0.0),
                    score_result.get("judge", "정상"),
                    score_result.get("code", ""),
                    score_result.get("upper_score", 0.0),
                    score_result.get("surface_score", 0.0),
                    score_result.get("inversion_score", 0.0),
                    score_result.get("diffusion_score", 0.0),
                    score_result.get("season", ""),
                    site_kp.lat, site_kp.lon,
                    site_kp.t2m, site_kp.ws10, site_kp.rh2m, site_kp.hpbl, site_kp.wd10,
                    site_kp.wd500, site_kp.ws500, site_kp.wd850, site_kp.ws850,
                    ddir500, ddir850, dspd500, dspd850,
                    json.dumps(score_result.get("score_details", {}), ensure_ascii=False),
                    json.dumps(score_result.get("input_values", {}), ensure_ascii=False),
                    dt.datetime.now()
                )
                history_list.append(data_tuple)

        except Exception as e:
            logger.error(f"HF {hf} 분석 실패: {e}")
            continue

    if not history_list:
        return {"history_list": [], "used_tmfc": used_tmfc, "summary": {}}

    scores = [row[5] for row in history_list]
    max_score = max(scores)
    max_idx = scores.index(max_score)
    peak_point = history_list[max_idx]
    high_risk_count = sum(1 for s in scores if s >= 60)

    return {
        "history_list": history_list,
        "used_tmfc": used_tmfc,
        "summary": {
            "peak_score": max_score,
            "peak_time_kst": peak_point[3],
            "peak_level": peak_point[6],
            "high_risk_count": high_risk_count,
        }
    }