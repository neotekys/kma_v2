# -*- coding: utf-8 -*-
import math

REAL_HEIGHT = {
    "중부": 22.0, "동부": 110.0, "서부": 470.1, "남부": 5.0, "북부": 80.0
}
MODEL_HEIGHT = {
    "중부": 149.4, "동부": 307.57, "서부": 470.1, "남부": 61.95, "북부": 282.37
}
LAPSE_RATE = 0.0065
ALPHA = 0.18
SITES = ["중부", "동부", "서부", "남부", "북부"]

def es_hpa(tc):
    return 6.112 * math.exp((17.67 * tc) / (tc + 243.5))

def adjust_temp_rh(temp_c, rh_pct, z_model, z_real):
    dz = z_real - z_model
    temp_adj = temp_c - dz * LAPSE_RATE
    e = (rh_pct / 100.0) * es_hpa(temp_c)
    rh_adj = 100.0 * e / es_hpa(temp_adj)
    rh_adj = max(0.0, min(100.0, rh_adj))
    return temp_adj, rh_adj, dz

def adjust_wind_speed(wind_speed, z_model, z_real, target_agl=10.0):
    ratio = ((z_real + target_agl) / (z_model + 10.0)) ** ALPHA
    return wind_speed * ratio, ratio

def adjust_weather(site, t2m, rh2m, wind_speed_10m_ms, wind_dir_10m_deg=0.0, time_kst=None):
    if site not in SITES:
        raise ValueError(f"알 수 없는 지점: {site}")
    z_model = MODEL_HEIGHT[site]
    z_real = REAL_HEIGHT[site]

    temp_adj, rh_adj, dz = adjust_temp_rh(t2m, rh2m, z_model, z_real)
    wind_10m_adj, ratio10 = adjust_wind_speed(wind_speed_10m_ms, z_model, z_real, target_agl=10.0)
    wind_20m_adj, _ = adjust_wind_speed(wind_speed_10m_ms, z_model, z_real, target_agl=20.0)

    return {
        "time_kst": time_kst,
        "site": site,
        "model_height_m": z_model,
        "real_height_m": z_real,
        "dz_m": round(dz, 2),
        "temp_c_raw": round(float(t2m), 2),
        "temp_c_adj": round(float(temp_adj), 2),
        "rh_pct_raw": round(float(rh2m), 2),
        "rh_pct_adj": round(float(rh_adj), 2),
        "wind_dir_10m_deg": round(float(wind_dir_10m_deg), 2),
        "wind_speed_10m_raw": round(float(wind_speed_10m_ms), 2),
        "wind_speed_10m_adj": round(float(wind_10m_adj), 2),
        "wind_speed_20m_adj": round(float(wind_20m_adj), 2),
        "wind_ratio_10m": round(float(ratio10), 4),
    }