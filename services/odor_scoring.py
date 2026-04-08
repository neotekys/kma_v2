# -*- coding: utf-8 -*-
"""
양산시 악취 예보 시스템 - 점수 계산 모듈
"""
import math
from typing import Optional, Tuple, Dict, Any
import datetime as dt
from utils.data_models import KimPoint
# ============================================================
# 기본 유틸리티 함수
# ============================================================
def calc_ws(u: float, v: float) -> float:
    """풍속 계산: WS = sqrt(u^2 + v^2)"""
    return float(math.sqrt(u * u + v * v))


def calc_wd(u: float, v: float) -> float:
    """
    풍향 계산 (바람이 불어오는 방향, 0~360도)
    WD = (atan2(-u, -v) * 180/pi + 360) % 360
    """
    return float((math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0)


def circular_diff_deg(a: float, b: float) -> float:
    """풍향 차이 계산 (-180~+180도 범위로 정규화)"""
    d = (a - b + 180.0) % 360.0 - 180.0
    return d


def get_season(month: int) -> str:
    """
    월로부터 계절 판정
    PDF 기준:
    - 겨울철: 1~3월, 11~12월
    - 봄가을철: 4~5월, 9~10월
    - 여름철: 6~8월
    """
    if month in (11, 12, 1, 2, 3):
        return "WINTER"
    if month in (4, 5, 9, 10):
        return "SPRING_FALL"
    return "SUMMER"


def pick_code(season: str, dh500: float) -> Optional[str]:
    """
    코드 선택 (필수조건 우선) - 모델요약_0106정리중_V1.pdf 기준
    
    겨울철 (1~3월, 11~12월):
    - W1: Δhgt ≤ -50 gpm
    - W2: -10 ≤ Δhgt ≤ +10 gpm
    - W3: 그 외의 경우
    
    봄가을철 (4~5월, 9~10월):
    - SF1: +15 ≤ Δhgt ≤ +25 gpm
    - SF2: 그 외의 경우
    
    여름철 (6~8월):
    - S1: -5 ≤ Δhgt ≤ +5 gpm
    - S2: Δhgt ≥ +20 gpm
    - S3: 그 외의 경우
    """
    if season == "WINTER":
        if dh500 <= -50:
            return "W1"
        if -10 <= dh500 <= 10:
            return "W2"
        return "W3"
    
    if season == "SPRING_FALL":
        if +15 <= dh500 <= +25:
            return "SF1"
        return "SF2"
    
    # SUMMER
    if -5 <= dh500 <= 5:
        return "S1"
    if dh500 >= 20:
        return "S2"
    return "S3"


def judge_odor_level(final_score: float) -> str:
    """
    최종 점수로 악취 레벨 판정
    PDF 기준:
    - ≥ 80: 악취 발생 높음
    - 60 ~ 79: 악취 발생 가능
    - < 60: 악취 발생 낮음
    """
    if final_score >= 80:
        return "악취 발생 높음"
    if final_score >= 60:
        return "악취 발생 가능"
    return "악취 발생 낮음"


def get_code_name(season: str, code: Optional[str]) -> str:
    """코드 이름 반환"""
    if not code:
        return "조건 없음"
    
    code_names = {
        "WINTER": {
            "W1": "겨울철 코드 1",
            "W2": "겨울철 코드 2",
            "W3": "겨울철 코드 3",
        },
        "SPRING_FALL": {
            "SF1": "봄가을 코드 1",
            "SF2": "봄가을 코드 2",
        },
        "SUMMER": {
            "S1": "여름철 코드 1",
            "S2": "여름철 코드 2",
            "S3": "여름철 코드 3",
        },
    }
    
    return code_names.get(season, {}).get(code, f"{season} {code}")


# ============================================================
# 역전층 높이 계산
# ============================================================
def calculate_inversion_height(
    t2m: float,
    tmp975: Optional[float],
    tmp950: Optional[float],
    tmp925: Optional[float],
) -> float:
    """
    역전층 높이를 hPa로 계산
    역전층의 높이는 상층으로 올라갈수록 기온이 상승하다가 하강하는 시점까지의 높이
    t2m < tmp975/950/925 중 첫 번째가 되는 높이가 역전층 높이
    """
    if tmp975 is not None and tmp975 > t2m:
        return 975.0
    if tmp950 is not None and tmp950 > t2m:
        return 950.0
    if tmp925 is not None and tmp925 > t2m:
        return 925.0
    # 역전층이 없으면 기본값
    return 1000.0


# ============================================================
# 약풍 개수 계산
# ============================================================
def count_weak_winds(
    ws100: float,  # WS80 또는 WS100
    ws200: float,  # WS975
    ws400: float,  # WS950
    threshold100: float,
    threshold200: float,
    threshold400: float,
) -> int:
    """
    약풍 개수 계산 (3개 층 중 기준 미만인 개수)
    WS100은 u80m, v80m에서 변형하여 풍속 계산
    WS200은 levs=975에서 U, V 변형하여 풍속 계산
    WS400은 levs=950에서 U, V 변형하여 풍속 계산
    """
    count = 0
    if ws100 < threshold100:
        count += 1
    if ws200 < threshold200:
        count += 1
    if ws400 < threshold400:
        count += 1
    return count


# ============================================================
# 겨울철 코드별 상층 점수 (1~3월, 11~12월)
# ============================================================
def winter_code1_upper_score(
    ddir500: float,
    dh850: float,
    dt850: float,
    ddir850: float,
    dspd850: float,
) -> Tuple[int, Dict[str, Any]]:
    """
    겨울철 코드1 상층 점수 (최대 20점)
    PDF: ① 5개 경우의 수, 개당 4점(최대 20점)
    
    조건식:
    1) 500hPa 풍향: (-35 ≤ Δ풍향 ≤ -20) OR (Δ풍향 ≥ +40) → 4점
    2) 850hPa 고도: Δhgt ≤ -10 OR Δhgt ≥ +30 → 4점
    3) 850hPa 기온: -1.0 ≤ ΔT ≤ +1.0 → 4점
    4) 850hPa 풍향: (Δ풍향 ≤ -40) OR (Δ풍향 ≥ +40) → 4점
    5) 850hPa 풍속: (-4.9 ≤ Δ풍속 ≤ 0) OR (Δ풍속 ≥ +0.1) → 4점
    """
    conds = []
    details = {}
    
    # 1) 500hPa 풍향: (반전: -35 ≤ ΔDir500 ≤ -20) OR (순전: ΔDir500 ≥ +40)
    cond1 = (-35 <= ddir500 <= -20) or (ddir500 >= 40)
    conds.append(cond1)
    details["ddir500"] = {"value": ddir500, "passed": cond1, "score": 4 if cond1 else 0}
    
    # 2) 850hPa 고도: ΔH850 ≤ -10 OR ΔH850 ≥ +30
    cond2 = (dh850 <= -10) or (dh850 >= 30)
    conds.append(cond2)
    details["dh850"] = {"value": dh850, "passed": cond2, "score": 4 if cond2 else 0}
    
    # 3) 850hPa 기온: -1.0 ≤ ΔT850 ≤ +1.0
    cond3 = -1.0 <= dt850 <= 1.0
    conds.append(cond3)
    details["dt850"] = {"value": dt850, "passed": cond3, "score": 4 if cond3 else 0}
    
    # 4) 850hPa 풍향: (Δ풍향 ≤ -40) OR (Δ풍향 ≥ +40)
    cond4 = (ddir850 <= -40) or (ddir850 >= 40)
    conds.append(cond4)
    details["ddir850"] = {"value": ddir850, "passed": cond4, "score": 4 if cond4 else 0}
    
    # 5) 850hPa 풍속: (-4.9 ≤ ΔSpd850 ≤ 0) OR (ΔSpd850 ≥ +0.1)
    cond5 = (-4.9 <= dspd850 <= 0) or (dspd850 >= 0.1)
    conds.append(cond5)
    details["dspd850"] = {"value": dspd850, "passed": cond5, "score": 4 if cond5 else 0}
    
    score = 4 * sum(conds)
    details["total"] = score
    details["max"] = 20
    return score, details


def winter_code2_upper_score(dt500: float) -> int:
    """
    겨울철 코드2 상층 점수 (최대 10점)
    중부 지점이 아래 조건식을 만족하면 10점(최대 10점) 아니면 0점
    
    조건식:
    - 500hPa 기온: -3.9 ≤ ΔT ≤ +3.9 → 10점
    """
    if -3.9 <= dt500 <= 3.9:
        return 10
    return 0


# ============================================================
# 겨울철 코드별 지표면 점수 (1~3월, 11~12월)
# ============================================================
def winter_code1_surface_score(ws10: float) -> Tuple[int, Dict[str, Any]]:
    """
    겨울철 코드1 지표면 점수 (최대 20점)
    한국형수치예보모델 단일(지상)면 정체 점수, '2개 지점' 평균 (최대 20점)
    
    조건:
    - WS10 < 1.0 m/s → 20점
    - 1.0 ≤ WS10 < 3.0 → 10점
    - WS10 ≥ 3.0 → 0점
    """
    if ws10 < 1.0:
        score = 20
    elif ws10 < 3.0:
        score = 10
    else:
        score = 0
    details = {"value": ws10, "score": score, "max": 20}
    return score, details


def winter_code2_surface_score(ws10: float) -> int:
    """
    겨울철 코드2 지표면 점수 (최대 25점)
    한국형수치예보모델 단일(지상)면 점수 '2개 지점' 평균(최대 25점)
    
    조건:
    - WS10 < 1.0 m/s → 25점
    - 1.0 ≤ WS10 < 3.0 → 15점
    - WS10 ≥ 3.0 → 0점
    """
    if ws10 < 1.0:
        return 25
    if ws10 < 3.0:
        return 15
    return 0


def winter_code3_surface_score(ws10: float) -> int:
    """
    겨울철 코드3 지표면 점수 (최대 25점)
     한국형수치예보모델 단일(지상)면 점수 '2개 지점' 평균(최대 25점)
    
    조건:
    - WS10 < 1.0 m/s → 25점
    - 1.0 ≤ WS10 < 3.0 → 15점
    - WS10 ≥ 3.0 → 0점
    """
    if ws10 < 1.0:
        return 25
    if ws10 < 3.0:
        return 15
    return 0


# ============================================================
# 겨울철 코드별 역전층 점수 (1~3월, 11~12월)
# ============================================================
def winter_code1_inversion_score(
    dt_inv: float,
    inv_height_hpa: float,
) -> Tuple[int, Dict[str, Any]]:
    """
    겨울철 코드1 역전층 점수 (최대 35점)
     ③ 역전층 점수 (최대 35점)
    
    (A) 역전 강도(dT) 점수 (최대 15점):
    - ≥ 2.0℃ → 15점
    - 1.0~2.0℃ → 8점
    - < 1.0℃ → 0점
    
    (B) 역전층의 높이 점수 (최대 20점):
    - ≤ 975hpa → 20점
    - 975hpa~950hpa → 10점
    - > 950hpa → 0점
    
    주의: (A) 조건이 만족하면 (B)조건 계산, (A) 조건이 만족하지 않으면 (A), (B)조건은 0점
    현재 구현은 항상 계산하지만, PDF 조건에 따라 (A)가 0점이면 전체 0점 처리 필요
    """
    # Base Height 점수
    if inv_height_hpa <= 975:
        base_score = 20
    elif 950 <= inv_height_hpa <= 975:
        base_score = 10
    else:
        base_score = 0
    
    # 역전 강도 점수
    if dt_inv >= 2.0:
        strength_score = 15
    elif dt_inv >= 1.0:
        strength_score = 8
    else:
        strength_score = 0
    
    # PDF 조건: (A) 조건이 만족하지 않으면 (A), (B)조건은 0점
    if strength_score == 0:
        base_score = 0
    
    total_score = base_score + strength_score
    details = {
        "base_height_hpa": inv_height_hpa,
        "base_score": base_score,
        "dt_inv": dt_inv,
        "strength_score": strength_score,
        "total": total_score,
        "max": 35,
    }
    return total_score, details


def winter_code2_inversion_score(
    dt_inv: float,
    inv_height_hpa: float,
) -> Tuple[int, Dict[str, Any]]:
    """
    겨울철 코드2 역전층 점수 (최대 35점)
     ③ 역전층 점수 (최대 35점) - 코드1과 동일
    """
    return winter_code1_inversion_score(dt_inv, inv_height_hpa)


def winter_code3_inversion_score(
    dt_inv: float,
    inv_height_hpa: float,
) -> Tuple[int, Dict[str, Any]]:
    """
    겨울철 코드3 역전층 점수 (최대 35점)
     역전층 점수 (최대 35점) - 코드1과 동일
    """
    return winter_code1_inversion_score(dt_inv, inv_height_hpa)


# ============================================================
# 겨울철 코드별 하층확산 점수 (1~3월, 11~12월)
# ============================================================
def winter_code1_diffusion_score(weak_wind_count: int) -> Tuple[int, Dict[str, Any]]:
    """
    겨울철 코드1 하층확산 점수 (최대 25점)
     '중부 지점'의 한국형수치예보모델 단일(지상)면 확산 점수 (최대 25점)
    
    약풍 개수('2개 지점'*3개층=총 6개):
    - 6개 → 25점
    - 5개 → 15점
    - 4개 → 10점
    - 3개 → 7점
    - 2개 → 5점
    - 1 이하 → 0점
    
    약풍 기준 (겨울철):
    - WS100 < 2.0 m/s → 1점
    - WS200 < 3.0 m/s → 1점
    - WS400 < 4.0 m/s → 1점
    """
    if weak_wind_count >= 6:
        score = 25
    elif weak_wind_count >= 5:
        score = 15
    elif weak_wind_count >= 4:
        score = 10
    elif weak_wind_count >= 3:
        score = 7
    elif weak_wind_count >= 2:
        score = 5
    else:
        score = 0
    details = {"weak_wind_count": weak_wind_count, "score": score, "max": 25}
    return score, details


def winter_code2_diffusion_score(weak_wind_count: int) -> int:
    """
    겨울철 코드2 하층확산 점수 (최대 30점)
     ④ 하층 확산 점수 (최대 30점)
    
    약풍 개수('2개 지점'*3개층=총 6개):
    - 6개 → 30점
    - 5개 → 25점
    - 4개 → 15점
    - 3개 → 10점
    - 2개 → 5점
    - 2 미만 → 0점
    """
    if weak_wind_count >= 6:
        return 30
    if weak_wind_count >= 5:
        return 25
    if weak_wind_count >= 4:
        return 15
    if weak_wind_count >= 3:
        return 10
    if weak_wind_count >= 2:
        return 5
    return 0


def winter_code3_diffusion_score(weak_wind_count: int) -> int:
    """
    겨울철 코드3 하층확산 점수 (최대 35점)
     ④ 하층 확산 점수 (최대 35점)
    
    약풍 개수('2개 지점'*3개층=총 6개):
    - 6개 → 35점
    - 5개 → 30점
    - 4개 → 20점
    - 3개 → 15점
    - 2개 → 10점
    - 2 미만 → 0점
    """
    if weak_wind_count >= 6:
        return 35
    if weak_wind_count >= 5:
        return 30
    if weak_wind_count >= 4:
        return 20
    if weak_wind_count >= 3:
        return 15
    if weak_wind_count >= 2:
        return 10
    return 0


# ============================================================
# 봄가을 코드별 상층 점수 (4~5월, 9~10월)
# ============================================================
def spring_fall_code1_upper_score(
    dt500: float,
    ddir500: float,
    dspd500: float,
    dh850: float,
    dt850: float,
    ddir850: float,
    dspd850: float,
) -> Tuple[int, Dict[str, Any]]:
    """
    봄가을 코드1 상층 점수 (최대 14점)
    ① 7개 경우의 수, 개당 2점(최대 14점)
    
    조건식:
    1) 500hPa 기온: -2.9 ≤ ΔT ≤ +2.9 → 2점
    2) 500hPa 풍향: △풍향 ≤ -15 OR △풍향 ≥ +15 → 2점
    3) 500hPa 풍속: △풍속 ≤ -10 OR △풍속 ≥ +10 → 2점
    4) 850hPa 고도: -10 ≤ Δhgt ≤ +15 → 2점
    5) 850hPa 기온: ΔT ≥ -1.0 → 2점
    6) 850hPa 풍향: △풍향 ≤ -15 OR △풍향 ≥ +15 → 2점
    7) 850hPa 풍속: △풍속 ≤ +4.9 → 2점
    """
    conds = []
    details = {}
    
    # 1) 500hPa 기온: -2.9 ≤ ΔT ≤ +2.9
    cond1 = -2.9 <= dt500 <= 2.9
    conds.append(cond1)
    details["dt500"] = {"value": dt500, "passed": cond1, "score": 2 if cond1 else 0}
    
    # 2) 500hPa 풍향: △풍향 ≤ -15 OR △풍향 ≥ +15
    cond2 = (ddir500 <= -15) or (ddir500 >= 15)
    conds.append(cond2)
    details["ddir500"] = {"value": ddir500, "passed": cond2, "score": 2 if cond2 else 0}
    
    # 3) 500hPa 풍속: △풍속 ≤ -10 OR △풍속 ≥ +10
    cond3 = (dspd500 <= -10) or (dspd500 >= 10)
    conds.append(cond3)
    details["dspd500"] = {"value": dspd500, "passed": cond3, "score": 2 if cond3 else 0}
    
    # 4) 850hPa 고도: -10 ≤ Δhgt ≤ +15
    cond4 = -10 <= dh850 <= 15
    conds.append(cond4)
    details["dh850"] = {"value": dh850, "passed": cond4, "score": 2 if cond4 else 0}
    
    # 5) 850hPa 기온: ΔT ≥ -1.0
    cond5 = dt850 >= -1.0
    conds.append(cond5)
    details["dt850"] = {"value": dt850, "passed": cond5, "score": 2 if cond5 else 0}
    
    # 6) 850hPa 풍향: △풍향 ≤ -15 OR △풍향 ≥ +15
    cond6 = (ddir850 <= -15) or (ddir850 >= 15)
    conds.append(cond6)
    details["ddir850"] = {"value": ddir850, "passed": cond6, "score": 2 if cond6 else 0}
    
    # 7) 850hPa 풍속: △풍속 ≤ +4.9
    cond7 = dspd850 <= 4.9
    conds.append(cond7)
    details["dspd850"] = {"value": dspd850, "passed": cond7, "score": 2 if cond7 else 0}
    
    score = 2 * sum(conds)
    details["total"] = score
    details["max"] = 14
    return score, details




# ============================================================
# 봄가을 코드별 지표면 점수 (4~5월, 9~10월)
# ============================================================
def spring_fall_code1_surface_score(ws10: float) -> Tuple[int, Dict[str, Any]]:
    """
    봄가을 코드1 지표면 점수 (최대 25점)
     ② 지표면 정체 점수, '2개 지점' 평균 (최대 25점)
    
    조건:
    - WS10 < 1.0 m/s → 25점
    - 1.0 ≤ WS10 < 3.0 → 15점
    - WS10 ≥ 3.0 → 0점
    """
    if ws10 < 1.0:
        score = 25
    elif ws10 < 3.0:
        score = 15
    else:
        score = 0
    details = {"value": ws10, "score": score, "max": 25}
    return score, details


def spring_fall_code2_surface_score(ws10: float) -> Tuple[int, Dict[str, Any]]:
    """
    봄가을 코드2 지표면 점수 (최대 30점)
    ① 지표면 정체 점수, '2개 지점' 평균 (최대 30점)
    
    조건:
    - WS10 < 1.0 m/s → 30점
    - 1.0 ≤ WS10 < 3.0 → 15점
    - WS10 ≥ 3.0 → 0점
    """
    if ws10 < 1.0:
        score = 30
    elif ws10 < 3.0:
        score = 15
    else:
        score = 0
    details = {"value": ws10, "score": score, "max": 30}
    return score, details


# ============================================================
# 봄가을 코드별 역전층 점수 (4~5월, 9~10월)
# ============================================================
def spring_fall_code1_inversion_score(
    dt_inv: float,
    inv_height_hpa: float,
) -> Tuple[int, Dict[str, Any]]:
    """
    봄가을 코드1 역전층 점수 (최대 30점)
     ③ 역전층 점수(최대 30점)
    
    (A) 역전층 높이 점수 (최대 20점):
    - ≤ 975hpa → 15점
    - 975hpa~950hpa → 8점
    - > 950hpa → 0점
    
    (B) 역전 강도(dT) 점수 (최대 15점):
    - ≥ 2.0 ℃ → 15점
    - 1.0~2.0 ℃ → 8점
    - < 1.0 ℃ → 0점
    """
    # Base Height 점수
    if inv_height_hpa <= 975:
        base_score = 15
    elif 950 <= inv_height_hpa <= 975:
        base_score = 8
    else:
        base_score = 0
    
    # 역전 강도 점수
    if dt_inv >= 2.0:
        strength_score = 15
    elif dt_inv >= 1.0:
        strength_score = 8
    else:
        strength_score = 0
    
    total_score = base_score + strength_score
    details = {
        "base_height_hpa": inv_height_hpa,
        "base_score": base_score,
        "dt_inv": dt_inv,
        "strength_score": strength_score,
        "total": total_score,
        "max": 30,
    }
    return total_score, details


def spring_fall_code2_inversion_score(
    dt_inv: float,
    inv_height_hpa: float,
) -> Tuple[int, Dict[str, Any]]:
    """
    봄가을 코드2 역전층 점수 (최대 35점)
     ② 역전층 점수(최대 35점)
    
    (A) 역전층 높이 점수 (최대 20점):
    - ≤ 975hpa → 20점
    - 975hpa~950hpa → 10점
    - > 950hpa → 0점
    
    (B) 역전 강도(dT) 점수 (최대 15점):
    - ≥ 2.0 ℃ → 15점
    - 1.0~2.0 ℃ → 8점
    - < 1.0 ℃ → 0점
    """
    # Base Height 점수
    if inv_height_hpa <= 975:
        base_score = 20
    elif 950 <= inv_height_hpa <= 975:
        base_score = 10
    else:
        base_score = 0
    
    # 역전 강도 점수
    if dt_inv >= 2.0:
        strength_score = 15
    elif dt_inv >= 1.0:
        strength_score = 8
    else:
        strength_score = 0
    
    total_score = base_score + strength_score
    details = {
        "base_height_hpa": inv_height_hpa,
        "base_score": base_score,
        "dt_inv": dt_inv,
        "strength_score": strength_score,
        "total": total_score,
        "max": 35,
    }
    return total_score, details


# ============================================================
# 봄가을 코드별 하층확산 점수 (4~5월, 9~10월)
# ============================================================
def spring_fall_code1_diffusion_score(weak_wind_count: int) -> Tuple[int, Dict[str, Any]]:
    """
    봄가을 코드1 하층확산 점수 (최대 32점)
     ④ 하층 확산 점수 (최대 32점)
    
    약풍 개수('2개 지점'*3개층=총 6개):
    - 6개 → 32점
    - 5개 → 27점
    - 4개 → 17점
    - 3개 → 12점
    - 2개 → 7점
    - 2 미만 → 0점
    
    약풍 기준 (봄가을철):
    - WS100 < 2.0 m/s → 1점
    - WS200 < 3.0 m/s → 1점
    - WS400 < 4.0 m/s → 1점
    """
    if weak_wind_count >= 6:
        score = 32
    elif weak_wind_count >= 5:
        score = 27
    elif weak_wind_count >= 4:
        score = 17
    elif weak_wind_count >= 3:
        score = 12
    elif weak_wind_count >= 2:
        score = 7
    else:
        score = 0
    details = {"weak_wind_count": weak_wind_count, "score": score, "max": 32}
    return score, details


def spring_fall_code2_diffusion_score(weak_wind_count: int) -> Tuple[int, Dict[str, Any]]:
    """
    봄가을 코드2 하층확산 점수 (최대 35점)
    ③ 하층 확산 점수 (최대 35점)
    
    약풍 개수('2개 지점'*3개층=총 6개):
    - 6개 → 35점
    - 5개 → 30점
    - 4개 → 20점
    - 3개 → 15점
    - 2개 → 10점
    - 2 미만 → 0점
    """
    if weak_wind_count >= 6:
        score = 35
    elif weak_wind_count >= 5:
        score = 30
    elif weak_wind_count >= 4:
        score = 20
    elif weak_wind_count >= 3:
        score = 15
    elif weak_wind_count >= 2:
        score = 10
    else:
        score = 0
    details = {"weak_wind_count": weak_wind_count, "score": score, "max": 35}
    return score, details


# ============================================================
# 여름철 코드별 상층 점수 (6~8월)
# ============================================================
def summer_code1_upper_score(dt500: float, ddir500: float) -> int:
    """
    여름철 코드1 상층 점수 (최대 10점)
    PDF: ① 2개 경우의 수, 개당 5점(최대 10점)
    
    조건식:
    1) 500hPa 기온: -3.9 ≤ ΔT ≤ +3.9 → 5점
    2) 500hPa 풍향: Δ풍향 ≥ +20 → 5점
    """
    score = 0
    
    # 1) 500hPa 기온: -3.9 ≤ ΔT ≤ +3.9 → 5점
    if -3.9 <= dt500 <= 3.9:
        score += 5
    
    # 2) 500hPa 풍향: Δ풍향 ≥ +20 → 5점
    if ddir500 >= 20:
        score += 5
    
    return score


def summer_code2_upper_score(
    ddir500: float,
    dspd500: float,
    dh850: float,
    dt850: float,
) -> int:
    """
    여름철 코드2 상층 점수 (최대 12점)
    ① 4개 경우의 수, 개당 3점(최대 12점)
    
    조건식:
    1) 500hPa 풍향: (-5 ≤ Δ풍향 ≤ +5) OR (+10 ≤ Δ풍향 ≤ +15) OR (Δ풍향 ≤ -20) → 3점
    2) 500hPa 풍속: (-2.9 ≤ Δ풍속 ≤ +2.9) OR (Δ풍속 ≤ -5) → 3점
    3) 850hPa 고도: Δhgt < 0 → 3점
    4) 850hPa 기온: -0.7 ≤ ΔT ≤ +1.4 → 3점
    """
    conds = []
    
    # 1) 500hPa 풍향: (-5 ≤ ΔDir500 ≤ +5) OR (+10 ≤ ΔDir500 ≤ +15) OR (ΔDir500 ≤ -20)
    cond1 = (-5 <= ddir500 <= 5) or (10 <= ddir500 <= 15) or (ddir500 <= -20)
    conds.append(cond1)
    
    # 2) 500hPa 풍속: (-2.9 ≤ ΔSpd500 ≤ +2.9) OR (ΔSpd500 ≤ -5)
    cond2 = (-2.9 <= dspd500 <= 2.9) or (dspd500 <= -5)
    conds.append(cond2)
    
    # 3) 850hPa 고도: ΔH850 < 0
    cond3 = dh850 < 0
    conds.append(cond3)
    
    # 4) 850hPa 기온: -0.7 ≤ ΔT850 ≤ +1.4
    cond4 = -0.7 <= dt850 <= 1.4
    conds.append(cond4)
    
    return 3 * sum(conds)


# ============================================================
# 여름철 코드별 지표면 점수 (6~8월)
# ============================================================
def summer_code1_surface_score(ws10: float) -> int:
    """
    여름철 코드1 지표면 점수 (최대 20점)
     ② 지표면 정체 점수, '2개 지점' 평균 (최대 20점)
    
    조건:
    - WS10 < 1.0 m/s → 20점
    - 1.0 ≤ WS10 < 3.0 → 10점
    - WS10 ≥ 3.0 → 0점
    """
    if ws10 < 1.0:
        return 20
    if ws10 < 3.0:
        return 10
    return 0


def summer_code2_surface_score(ws10: float) -> int:
    """
    여름철 코드2 지표면 점수 (최대 20점)
     ② 지표면 정체 점수, '2개 지점' 평균 (최대 20점)
    
    조건:
    - WS10 < 1.0 m/s → 20점
    - 1.0 ≤ WS10 < 3.0 → 10점
    - WS10 ≥ 3.0 → 0점
    """
    if ws10 < 1.0:
        return 20
    if ws10 < 3.0:
        return 10
    return 0


def summer_code3_surface_score(ws10: float) -> int:
    """
    여름철 코드3 지표면 점수 (최대 30점)
    ① 지표면 정체 점수, '2개 지점' 평균 (최대 30점)
    
    조건:
    - WS10 < 1.0 m/s → 30점
    - 1.0 ≤ WS10 < 3.0 → 15점
    - WS10 ≥ 3.0 → 0점
    """
    if ws10 < 1.0:
        return 30
    if ws10 < 3.0:
        return 15
    return 0


# ============================================================
# 여름철 코드별 역전층 점수 (6~8월)
# ============================================================
def summer_code1_inversion_score(
    dt_inv: float,
    inv_height_hpa: float,
) -> Tuple[int, Dict[str, Any]]:
    """
    여름철 코드1 역전층 점수 (최대 35점)
     ③ 역전층 점수 (최대 35점)
    
    (A) 역전층 높이 점수 (최대 20점):
    - ≤ 950hpa → 20점
    - 975hpa~925hpa → 10점
    - > 925hpa → 0점
    
    (B) 역전 강도(dT) 점수 (최대 15점):
    - ≥ 1.5 ℃ → 15점
    - 0.7~1.5 ℃ → 8점
    - < 0.7 ℃ → 0점
    """
    # Base Height 점수
    if inv_height_hpa <= 950:
        base_score = 20
    elif 925 <= inv_height_hpa <= 975:
        base_score = 10
    else:
        base_score = 0
    
    # 역전 강도 점수
    if dt_inv >= 1.5:
        strength_score = 15
    elif dt_inv >= 0.7:
        strength_score = 8
    else:
        strength_score = 0
    
    total_score = base_score + strength_score
    details = {
        "base_height_hpa": inv_height_hpa,
        "base_score": base_score,
        "dt_inv": dt_inv,
        "strength_score": strength_score,
        "total": total_score,
        "max": 35,
    }
    return total_score, details


def summer_code2_inversion_score(
    dt_inv: float,
    inv_height_hpa: float,
) -> Tuple[int, Dict[str, Any]]:
    """
    여름철 코드2 역전층 점수 (최대 35점)
     ③ 역전층 점수 (최대 35점) - 코드1과 동일
    """
    return summer_code1_inversion_score(dt_inv, inv_height_hpa)


def summer_code3_inversion_score(
    dt_inv: float,
    inv_height_hpa: float,
) -> Tuple[int, Dict[str, Any]]:
    """
    여름철 코드3 역전층 점수 (최대 35점)
     ② 역전층 점수 (최대 35점)
    
    (A) 역전층 높이 점수 (최대 20점):
    - ≤ 950hpa → 20점
    - 950hpa~925hpa → 10점
    - > 925hpa → 0점
    
    (B) 역전 강도(dT) 점수 (최대 15점):
    - ≥ 1.5 ℃ → 15점
    - 0.7~1.5 ℃ → 8점
    - < 0.7 ℃ → 0점
    
    주의: 코드1, 2와 달리 높이 범위가 950hpa~925hpa (975hpa가 아님)
    """
    # Base Height 점수
    if inv_height_hpa <= 950:
        base_score = 20
    elif 925 <= inv_height_hpa <= 950:  # PDF: 950hpa~925hpa
        base_score = 10
    else:
        base_score = 0
    
    # 역전 강도 점수
    if dt_inv >= 1.5:
        strength_score = 15
    elif dt_inv >= 0.7:
        strength_score = 8
    else:
        strength_score = 0
    
    total_score = base_score + strength_score
    details = {
        "base_height_hpa": inv_height_hpa,
        "base_score": base_score,
        "dt_inv": dt_inv,
        "strength_score": strength_score,
        "total": total_score,
        "max": 35,
    }
    return total_score, details


# ============================================================
# 여름철 코드별 하층확산 점수 (6~8월)
# ============================================================
def summer_code1_diffusion_score(weak_wind_count: int) -> int:
    """
    여름철 코드1 하층확산 점수 (최대 25점)
     ④ 하층 확산 점수 (최대 25점)
    
    약풍 개수('2개 지점'*3개층=총 6개):
    - 6개 → 25점
    - 5개 → 20점
    - 4개 → 15점
    - 3개 → 10점
    - 2개 → 5점
    - 2 미만 → 0점
    
    약풍 기준 (여름철):
    - WS100 < 1.5 m/s → 1점
    - WS200 < 2.5 m/s → 1점
    - WS400 < 3.5 m/s → 1점
    """
    if weak_wind_count >= 6:
        return 25
    if weak_wind_count >= 5:
        return 20
    if weak_wind_count >= 4:
        return 15
    if weak_wind_count >= 3:
        return 10
    if weak_wind_count >= 2:
        return 5
    return 0


def summer_code2_diffusion_score(weak_wind_count: int) -> int:
    """
    여름철 코드2 하층확산 점수 (최대 35점)
     ④ 하층 확산 점수 (최대 35점)
    
    약풍 개수('2개 지점'*3개층=총 6개):
    - 6개 → 35점
    - 5개 → 30점
    - 4개 → 20점
    - 3개 → 15점
    - 2개 → 10점
    - 2 미만 → 0점
    """
    if weak_wind_count >= 6:
        return 35
    if weak_wind_count >= 5:
        return 30
    if weak_wind_count >= 4:
        return 20
    if weak_wind_count >= 3:
        return 15
    if weak_wind_count >= 2:
        return 10
    return 0


def summer_code3_diffusion_score(weak_wind_count: int) -> int:
    """
    여름철 코드3 하층확산 점수 (최대 35점)
    ③ 하층 확산 점수 (최대 35점)
    
    약풍 개수('2개 지점'*3개층=총 6개):
    - 6개 → 35점
    - 5개 → 30점
    - 4개 → 20점
    - 3개 → 15점
    - 2개 → 10점
    - 2 미만 → 0점
    """
    if weak_wind_count >= 6:
        return 35
    if weak_wind_count >= 5:
        return 30
    if weak_wind_count >= 4:
        return 20
    if weak_wind_count >= 3:
        return 15
    if weak_wind_count >= 2:
        return 10
    return 0


def calculate_odor_score(
    current: Dict[str, KimPoint],  # 현재 시간 (중부, 동부)
    previous: Optional[Dict[str, KimPoint]],  # 12시간 전 (중부, 동부)
    month: int,
) -> Dict[str, Any]:
    """
    악취 점수 계산 (PDF 기준)
    current, previous는 {"중부": KimPoint, "동부": KimPoint} 형태
    """
    season = get_season(month)
    
    # 중부 지점 데이터
    center = current["중부"]
    center_prev = previous["중부"] if previous else None
    
    # 동부 지점 데이터
    east = current["동부"]
    east_prev = previous["동부"] if previous else None
    
    # 12시간 후 변화량 계산 (중부 지점 기준)
    # current는 예보 시간대, previous는 12시간 후 시간대
    # 변화량 = 12시간 후 값 - 현재 값
    if center_prev:
        dh500 = center_prev.hgt500 - center.hgt500  # 12시간 후 - 현재
        dt500 = center_prev.tmp500 - center.tmp500
        ddir500 = circular_diff_deg(center_prev.wd500, center.wd500)  # 12시간 후 - 현재
        dspd500 = center_prev.ws500 - center.ws500
        dh850 = center_prev.hgt850 - center.hgt850
        dt850 = center_prev.tmp850 - center.tmp850
        ddir850 = circular_diff_deg(center_prev.wd850, center.wd850)
        dspd850 = center_prev.ws850 - center.ws850
    else:
        dh500 = dt500 = ddir500 = dspd500 = dh850 = dt850 = ddir850 = dspd850 = 0.0
    
    # 코드 선택 및 필수조건 확인
    code = pick_code(season, dh500)
    
    # 필수조건 확인
    mandatory_condition_met = False
    mandatory_condition_desc = ""
    mandatory_condition_value = None
    
    if season == "WINTER":
        if code == "W1":
            mandatory_condition_value = dh500
            mandatory_condition_met = dh500 <= -50
            mandatory_condition_desc = "500hPa 고도가 12시간 전 대비 50gpm 이하로 하강 (Δhgt ≤ -50 gpm)"
        elif code == "W2":
            mandatory_condition_value = dh500
            mandatory_condition_met = -10 <= dh500 <= 10
            mandatory_condition_desc = "500hPa 고도가 -10gpm ~ +10gpm 범위 (-10 ≤ Δhgt ≤ +10)"
        else:  # W3
            mandatory_condition_met = True  # 그 외의 경우는 필수조건 없음
            mandatory_condition_desc = "겨울철 코드 1, 2 외의 경우"
    elif season == "SPRING_FALL":
        if code == "SF1":
            mandatory_condition_value = dh500
            mandatory_condition_met = +15 <= dh500 <= +25
            mandatory_condition_desc = "500hPa 고도가 +15gpm ~ +25gpm 범위 (+15 ≤ Δhgt ≤ +25)"
        elif code == "SF2":
            mandatory_condition_value = dh500
            mandatory_condition_met = -10 <= dh500 <= 10
            mandatory_condition_desc = "500hPa 고도가 -10gpm ~ +10gpm 범위 (-10 ≤ Δhgt ≤ +10)"
        else:
            mandatory_condition_met = True
            mandatory_condition_desc = "봄가을 코드 외의 경우"
    else:  # SUMMER
        if code == "S1":
            mandatory_condition_value = dh500
            mandatory_condition_met = -5 <= dh500 <= 5
            mandatory_condition_desc = "500hPa 고도가 -5gpm ~ +5gpm 범위 (-5 ≤ Δhgt ≤ +5)"
        elif code == "S2":
            mandatory_condition_value = dh500
            mandatory_condition_met = dh500 >= 20
            mandatory_condition_desc = "500hPa 고도가 12시간 전 대비 20gpm 이상 상승 (Δhgt ≥ +20 gpm)"
        else:  # S3
            mandatory_condition_met = True
            mandatory_condition_desc = "여름철 코드 1, 2 외의 경우"
    
    # 상세 점수 정보 수집
    score_details: Dict[str, Any] = {
        "code": code,
        "code_name": get_code_name(season, code),
        "mandatory_condition": {
            "description": mandatory_condition_desc,
            "value": mandatory_condition_value,
            "met": mandatory_condition_met,
        }
    }
    
    # 필수조건이 만족하지 않으면 모든 점수 0점 처리
    if not mandatory_condition_met:
        return {
            "final_score": 0.0,
            "upper_score": 0,
            "surface_score": 0,
            "inversion_score": 0,
            "diffusion_score": 0,
            "code": code,
            "season": season,
            "judge": "악취 없음",
            "score_details": score_details,
            "input_values": {
                # 12시간 전 변화량
                "dh500": dh500,
                "dt500": dt500,
                "ddir500": ddir500,
                "dspd500": dspd500,
                "dh850": dh850,
                "dt850": dt850,
                "ddir850": ddir850,
                "dspd850": dspd850,
                "ws10_avg": (center.ws10 + east.ws10) / 2.0,
                "dt_inv": center.tmp850 - center.t2m,
                "inv_height_hpa": calculate_inversion_height(
                    center.t2m,
                    center.tmp975 if center.tmp975 != 0.0 else None,
                    center.tmp950 if center.tmp950 != 0.0 else None,
                    center.tmp925 if center.tmp925 != 0.0 else None,
                ),
                "weak_wind_count": 0,
                # 원시 데이터: 현재 값
                "current": {
                    "hgt500": center.hgt500,
                    "tmp500": center.tmp500,
                    "wd500": center.wd500,
                    "ws500": center.ws500,
                    "hgt850": center.hgt850,
                    "tmp850": center.tmp850,
                    "wd850": center.wd850,
                    "ws850": center.ws850,
                    "t2m": center.t2m,
                    "ws10_center": center.ws10,
                    "ws10_east": east.ws10,
                },
                # 원시 데이터: 12시간 전 값
                "previous_12h": {
                    "hgt500": center_prev.hgt500 if center_prev else None,
                    "tmp500": center_prev.tmp500 if center_prev else None,
                    "wd500": center_prev.wd500 if center_prev else None,
                    "ws500": center_prev.ws500 if center_prev else None,
                    "hgt850": center_prev.hgt850 if center_prev else None,
                    "tmp850": center_prev.tmp850 if center_prev else None,
                    "wd850": center_prev.wd850 if center_prev else None,
                    "ws850": center_prev.ws850 if center_prev else None,
                    "t2m": center_prev.t2m if center_prev else None,
                    "ws10_center": center_prev.ws10 if center_prev else None,
                    "ws10_east": east_prev.ws10 if east_prev else None,
                },
            },
        }
    
    # 필수조건 확인 (봄가을 코드1의 경우)
    sf1_mandatory_failed = False
    if season == "SPRING_FALL" and code == "SF1":
        if not (+15 <= dh500 <= +25):
            sf1_mandatory_failed = True
    
    # 상층 점수
    upper_score = 0
    upper_details = None
    if season == "WINTER":
        if code == "W1":
            upper_score, upper_details = winter_code1_upper_score(ddir500, dh850, dt850, ddir850, dspd850)
        elif code == "W2":
            upper_score = winter_code2_upper_score(dt500)
            upper_details = {"value": dt500, "score": upper_score, "max": 10}
    elif season == "SPRING_FALL":
        if code == "SF1":
            if sf1_mandatory_failed:
                # 필수조건 불만족 시 점수 계산 없이 0점 처리
                upper_score = 0
                upper_details = {
                    "value": dh500,
                    "score": 0,
                    "max": 14,
                    "mandatory_condition_failed": True,
                    "message": "필수조건 불만족: 500hPa 고도가 +15~+25gpm 범위를 벗어남"
                }
            else:
                upper_score, upper_details = spring_fall_code1_upper_score(
                    dt500, ddir500, dspd500, dh850, dt850, ddir850, dspd850
                )
        elif code == "SF2":
            # 봄가을 코드2 상층 점수: PDF에 명시 없음 (0점)
            upper_score = 0
            upper_details = {"score": 0, "max": 10}
    else:  # SUMMER
        if code == "S1":
            upper_score = summer_code1_upper_score(dt500, ddir500)
            upper_details = {"score": upper_score, "max": 10}
        elif code == "S2":
            if dh500 >= 20:  # 필수조건 확인
                upper_score = summer_code2_upper_score(ddir500, dspd500, dh850, dt850)
                upper_details = {"score": upper_score, "max": 12}
    
    score_details["upper"] = upper_details or {"score": upper_score, "max": 20}
    
    # 지표면 정체 점수 (2개 지점 평균)
    ws10_avg = (center.ws10 + east.ws10) / 2.0
    surface_score = 0
    surface_details = None
    
    if season == "WINTER":
        if code == "W1":
            surface_score, surface_details = winter_code1_surface_score(ws10_avg)
        elif code == "W2":
            surface_score = winter_code2_surface_score(ws10_avg)
            surface_details = {"value": ws10_avg, "score": surface_score, "max": 25}
        else:  # W3
            surface_score = winter_code3_surface_score(ws10_avg)
            surface_details = {"value": ws10_avg, "score": surface_score, "max": 25}
    elif season == "SPRING_FALL":
        if code == "SF1":
            if sf1_mandatory_failed:
                # 필수조건 불만족 시 점수 계산 없이 0점 처리
                surface_score = 0
                surface_details = {
                    "value": ws10_avg,
                    "score": 0,
                    "max": 25,
                    "mandatory_condition_failed": True,
                    "message": "필수조건 불만족으로 인한 0점 처리"
                }
            else:
                surface_score, surface_details = spring_fall_code1_surface_score(ws10_avg)
        else:  # SF2
            surface_score, surface_details = spring_fall_code2_surface_score(ws10_avg)
    else:  # SUMMER
        if code == "S1":
            surface_score = summer_code1_surface_score(ws10_avg)
            surface_details = {"value": ws10_avg, "score": surface_score, "max": 20}
        elif code == "S2":
            surface_score = summer_code2_surface_score(ws10_avg)
            surface_details = {"value": ws10_avg, "score": surface_score, "max": 20}
        else:  # S3
            surface_score = summer_code3_surface_score(ws10_avg)
            surface_details = {"value": ws10_avg, "score": surface_score, "max": 30}
    
    score_details["surface"] = surface_details or {"value": ws10_avg, "score": surface_score, "max": 20}
    
    # 역전층 점수 (중부 지점 기준)
    # 역전 강도: 850hPa 기온 - 지상 기온
    dt_inv = center.tmp850 - center.t2m
    
    # 역전층 높이 계산
    inv_height_hpa = calculate_inversion_height(
        center.t2m,
        center.tmp975 if center.tmp975 != 0.0 else None,
        center.tmp950 if center.tmp950 != 0.0 else None,
        center.tmp925 if center.tmp925 != 0.0 else None,
    )
    
    inversion_score = 0
    inversion_details = None
    
    if season == "WINTER":
        if code == "W1":
            inversion_score, inversion_details = winter_code1_inversion_score(dt_inv, inv_height_hpa)
        elif code == "W2":
            inversion_score, inversion_details = winter_code2_inversion_score(dt_inv, inv_height_hpa)
        else:  # W3
            inversion_score, inversion_details = winter_code3_inversion_score(dt_inv, inv_height_hpa)
    elif season == "SPRING_FALL":
        if code == "SF1":
            if sf1_mandatory_failed:
                # 필수조건 불만족 시 점수 계산 없이 0점 처리
                inversion_score = 0
                inversion_details = {
                    "base_height_hpa": inv_height_hpa,
                    "base_score": 0,
                    "dt_inv": dt_inv,
                    "strength_score": 0,
                    "total": 0,
                    "max": 30,
                    "mandatory_condition_failed": True,
                    "message": "필수조건 불만족으로 인한 0점 처리"
                }
            else:
                inversion_score, inversion_details = spring_fall_code1_inversion_score(dt_inv, inv_height_hpa)
        else:  # SF2
            inversion_score, inversion_details = spring_fall_code2_inversion_score(dt_inv, inv_height_hpa)
    else:  # SUMMER
        if code == "S1":
            inversion_score, inversion_details = summer_code1_inversion_score(dt_inv, inv_height_hpa)
        elif code == "S2":
            inversion_score, inversion_details = summer_code2_inversion_score(dt_inv, inv_height_hpa)
        else:  # S3
            # 여름철 코드3은 역전층 높이 범위가 다름 (950hpa~925hpa, 975hpa 아님)
            inversion_score, inversion_details = summer_code3_inversion_score(dt_inv, inv_height_hpa)
    
    score_details["inversion"] = inversion_details or {"score": inversion_score, "max": 35}
    
    # 하층 확산 점수 (2개 지점 * 3개층 = 6개 약풍 개수)
    # WS100 = u80m, v80m (WS80)
    # WS200 = 975hPa u, v
    # WS400 = 950hPa u, v
    
    weak_wind_count = 0
    
    # 겨울/봄가을 기준
    if season in ["WINTER", "SPRING_FALL"]:
        threshold100, threshold200, threshold400 = 2.0, 3.0, 4.0
    else:  # SUMMER
        threshold100, threshold200, threshold400 = 1.5, 2.5, 3.5
    
    # 중부 지점
    weak_wind_count += count_weak_winds(
        center.ws80, center.ws975, center.ws950,
        threshold100, threshold200, threshold400
    )
    
    # 동부 지점
    weak_wind_count += count_weak_winds(
        east.ws80, east.ws975, east.ws950,
        threshold100, threshold200, threshold400
    )
    
    # 하층 확산 점수
    diffusion_score = 0
    diffusion_details = None
    
    if season == "WINTER":
        if code == "W1":
            diffusion_score, diffusion_details = winter_code1_diffusion_score(weak_wind_count)
        elif code == "W2":
            diffusion_score = winter_code2_diffusion_score(weak_wind_count)
            diffusion_details = {"weak_wind_count": weak_wind_count, "score": diffusion_score, "max": 30}
        else:  # W3
            diffusion_score = winter_code3_diffusion_score(weak_wind_count)
            diffusion_details = {"weak_wind_count": weak_wind_count, "score": diffusion_score, "max": 35}
    elif season == "SPRING_FALL":
        if code == "SF1":
            if sf1_mandatory_failed:
                # 필수조건 불만족 시 점수 계산 없이 0점 처리
                diffusion_score = 0
                diffusion_details = {
                    "weak_wind_count": weak_wind_count,
                    "score": 0,
                    "max": 32,
                    "mandatory_condition_failed": True,
                    "message": "필수조건 불만족으로 인한 0점 처리"
                }
            else:
                diffusion_score, diffusion_details = spring_fall_code1_diffusion_score(weak_wind_count)
        else:  # SF2
            diffusion_score, diffusion_details = spring_fall_code2_diffusion_score(weak_wind_count)
    else:  # SUMMER
        if code == "S1":
            diffusion_score = summer_code1_diffusion_score(weak_wind_count)
            diffusion_details = {"weak_wind_count": weak_wind_count, "score": diffusion_score, "max": 25}
        elif code == "S2":
            diffusion_score = summer_code2_diffusion_score(weak_wind_count)
            diffusion_details = {"weak_wind_count": weak_wind_count, "score": diffusion_score, "max": 25}
        else:  # S3
            diffusion_score = summer_code3_diffusion_score(weak_wind_count)
            diffusion_details = {"weak_wind_count": weak_wind_count, "score": diffusion_score, "max": 25}
    
    score_details["diffusion"] = diffusion_details or {"weak_wind_count": weak_wind_count, "score": diffusion_score, "max": 25}
    
    # 최종 점수
    final_score = upper_score + surface_score + inversion_score + diffusion_score
    final_score = max(0.0, min(100.0, final_score))
    
    return {
        "final_score": final_score,
        "upper_score": upper_score,
        "surface_score": surface_score,
        "inversion_score": inversion_score,
        "diffusion_score": diffusion_score,
        "code": code,
        "season": season,
        "judge": judge_odor_level(final_score),
        "score_details": score_details,  # 상세 계산 과정
        "input_values": {  # 입력값 (검증용)
            # 12시간 전 변화량
            "dh500": dh500,
            "dt500": dt500,
            "ddir500": ddir500,
            "dspd500": dspd500,
            "dh850": dh850,
            "dt850": dt850,
            "ddir850": ddir850,
            "dspd850": dspd850,
            "ws10_avg": ws10_avg,
            "dt_inv": dt_inv,
            "inv_height_hpa": inv_height_hpa,
            "weak_wind_count": weak_wind_count
            ,
            # 원시 데이터: 현재 값 (실제 API에서 가져온 값 - 항상 포함)
            "current": {
                "hgt500": center.hgt500,
                "tmp500": center.tmp500,
                "wd500": center.wd500,
                "ws500": center.ws500,
                "hgt850": center.hgt850,
                "tmp850": center.tmp850,
                "wd850": center.wd850,
                "ws850": center.ws850,
                "t2m": center.t2m,
                "ws10_center": center.ws10,
                "ws10_east": east.ws10,
            },
            # 원시 데이터: 12시간 후 값 (예보 시간대 기준)
            "next_12h": {
                "hgt500": center_prev.hgt500 if center_prev else None,
                "tmp500": center_prev.tmp500 if center_prev else None,
                "wd500": center_prev.wd500 if center_prev else None,
                "ws500": center_prev.ws500 if center_prev else None,
                "hgt850": center_prev.hgt850 if center_prev else None,
                "tmp850": center_prev.tmp850 if center_prev else None,
                "wd850": center_prev.wd850 if center_prev else None,
                "ws850": center_prev.ws850 if center_prev else None,
                "t2m": center_prev.t2m if center_prev else None,
                "ws10_center": center_prev.ws10 if center_prev else None,
                "ws10_east": east_prev.ws10 if east_prev else None,
            },
        },
    }