# -*- coding: utf-8 -*-
import math
import numpy as np
import pandas as pd
from shapely.geometry import Point, box
from shapely import affinity
from shapely.ops import unary_union

# =========================
# 기본 설정
# =========================
    # "북부": {"lat": 35.40, "lon": 129.04},
    # "서부": {"lat": 35.33, "lon": 128.95},
    # "중부": {"lat": 35.335, "lon": 129.037},  # PDF 기준 지점
    # "동부": {"lat": 35.33, "lon": 129.10},    # PDF 기준 지점
    # "남부": {"lat": 35.28, "lon": 129.04},
# 발원지 2개
SOURCES = {
    "산막유산공단": {
        "lat": 35.3650056,
        "lon": 129.0431500,
        "site_for_wind": "중부",
    },
    "덕계소주공단": {
        "lat": 35.4161444,
        "lon": 129.1580250,
        "site_for_wind": "동부",
    },
    "양산CC":{
        "lat": 35.40, "lon": 129.04,
        "site_for_wind": "북부"
    },
    "아일랜드 카페":{
        "lat": 35.33, "lon": 128.95,
        "site_for_wind": "서부"
    },
    "금정산 고당봉":{
        "lat": 35.28, "lon": 129.04,
        "site_for_wind": "남부"
    },  
}

# 도메인
DOMAIN_BOUNDS = {
    "min_lon": 128.99,
    "min_lat": 35.30,
    "max_lon": 129.18,
    "max_lat": 35.44,
}

# 시간 필터
START_HOUR = 0
END_HOUR = 23

# 확산 파라미터
DT_SEC = 3600.0
B_RATIO = 0.35
INNER_RATIO = 0.70
K1 = 1.0
K2 = 1.0


# =========================
# 보조 함수
# =========================

def met_dir_speed_to_uv(dir_deg_from, speed):
    """기상 풍향(FROM, 북=0 시계방향) → 이동 성분(u: 동, v: 북)"""
    rad = math.radians(dir_deg_from)
    u = -speed * math.sin(rad)
    v = -speed * math.cos(rad)
    return u, v


def uv_to_speed_dir(u, v):
    """u, v 성분 → 풍속, 이동방향(TO, 북기준 시계방향)"""
    speed = math.hypot(u, v)
    bearing_to = (math.degrees(math.atan2(u, v)) + 360.0) % 360.0
    return speed, bearing_to


def lonlat_to_local_xy(lon, lat, lon0, lat0):
    """경위도 → 국지 평면 좌표(m)"""
    x = (lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 110540.0
    return x, y


def local_xy_to_lonlat(x, y, lon0, lat0):
    """국지 평면 좌표(m) → 경위도"""
    lon = lon0 + x / (111320.0 * math.cos(math.radians(lat0)))
    lat = lat0 + y / 110540.0
    return lon, lat


def make_ellipse(center_x, center_y, a, b, angle_deg_to):
    """
    타원 생성
    center_x, center_y : m
    a, b : 장축/단축 반경 (m)
    angle_deg_to : 이동 방향(TO), 북기준 시계방향
    """
    circ = Point(0, 0).buffer(1.0, resolution=64)
    ell = affinity.scale(circ, a, b)
    shapely_angle = 90.0 - angle_deg_to
    ell = affinity.rotate(ell, shapely_angle, origin=(0, 0), use_radians=False)
    ell = affinity.translate(ell, center_x, center_y)
    return ell


def build_domain_polygon(lon0, lat0):
    """도메인 박스 생성"""
    min_x, min_y = lonlat_to_local_xy(
        DOMAIN_BOUNDS["min_lon"], DOMAIN_BOUNDS["min_lat"], lon0, lat0
    )
    max_x, max_y = lonlat_to_local_xy(
        DOMAIN_BOUNDS["max_lon"], DOMAIN_BOUNDS["max_lat"], lon0, lat0
    )
    return box(min_x, min_y, max_x, max_y)


def stability_factor(row):
    """
    안정도 계수 - 추후 역전층 강도/경계층 높이 연결 예정
    현재는 1.0 반환
    """
    return 1.0


# =========================
# 시간 누적 plume 계산
# =========================

def simulate_source(df, source_name, source_cfg):
    """
    발원지별 시간 누적 plume 계산
    df 컬럼: site, time_kst, dir10, spd10_adj, spd20_adj, u80m, v80m
    """
    site = source_cfg["site_for_wind"]
    src_lat = source_cfg["lat"]
    src_lon = source_cfg["lon"]

    part = df[df["site"] == site].copy()
    part["time_kst"] = pd.to_datetime(part["time_kst"])
    part = part.sort_values("time_kst")

    part = part[
        (part["time_kst"].dt.hour >= START_HOUR) &
        (part["time_kst"].dt.hour <= END_HOUR)
    ].copy()

    if part.empty:
        return [], [], [], None, None

    cur_x, cur_y = 0.0, 0.0
    domain_poly = build_domain_polygon(src_lon, src_lat)

    hourly_rows = []
    polys60 = []
    polys80 = []

    for _, row in part.iterrows():
        dir10 = row.get("dir10", np.nan)
        spd10 = row.get("spd10_adj", np.nan)
        spd20 = row.get("spd20_adj", np.nan)
        u80m  = row.get("u80m", 0.0)
        v80m  = row.get("v80m", 0.0)

        if pd.isna(dir10) or pd.isna(spd10) or pd.isna(spd20):
            continue

        u10, v10 = met_dir_speed_to_uv(float(dir10), float(spd10))
        u20, v20 = met_dir_speed_to_uv(float(dir10), float(spd20))

        # 80m: u80m, v80m으로 풍속/풍향 계산
        if pd.notna(u80m) and pd.notna(v80m) and (u80m != 0.0 or v80m != 0.0):
            spd80 = math.hypot(float(u80m), float(v80m))
            dir80 = (math.degrees(math.atan2(-float(u80m), -float(v80m))) + 360.0) % 360.0
            u80, v80 = met_dir_speed_to_uv(dir80, spd80)
        else:
            u80, v80 = 0.0, 0.0

        u_eff = 0.7 * u10 + 0.2 * u20 + 0.1 * u80
        v_eff = 0.7 * v10 + 0.2 * v20 + 0.1 * v80

        spd_eff, dir_eff_to = uv_to_speed_dir(u_eff, v_eff)

        cur_x += u_eff * DT_SEC
        cur_y += v_eff * DT_SEC

        f_stab = stability_factor(row)

        a60 = spd_eff * DT_SEC * K1
        b60 = a60 * B_RATIO * K2 * f_stab
        a80 = a60 * INNER_RATIO
        b80 = b60 * INNER_RATIO

        poly60 = make_ellipse(cur_x, cur_y, a60, b60, dir_eff_to).intersection(domain_poly)
        poly80 = make_ellipse(cur_x, cur_y, a80, b80, dir_eff_to).intersection(domain_poly)

        polys60.append(poly60)
        polys80.append(poly80)

        center_lon, center_lat = local_xy_to_lonlat(cur_x, cur_y, src_lon, src_lat)

        hourly_rows.append({
            "source": source_name,
            "site_for_wind": site,
            "time_kst": row["time_kst"],
            "spd_eff": round(spd_eff, 3),
            "dir_eff_to_deg": round(dir_eff_to, 1),
            "center_lon": center_lon,
            "center_lat": center_lat,
            "a60_m": round(a60, 1),
            "b60_m": round(b60, 1),
            "area60_m2": round(poly60.area, 1),
            "a80_m": round(a80, 1),
            "b80_m": round(b80, 1),
            "area80_m2": round(poly80.area, 1),
        })

    union60 = unary_union(polys60).intersection(domain_poly) if polys60 else None
    union80 = unary_union(polys80).intersection(domain_poly) if polys80 else None

    return hourly_rows, polys60, polys80, union60, union80