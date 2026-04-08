# -*- coding: utf-8 -*-
from dataclasses import dataclass, fields
import math
from typing import Optional, Dict, Any


try:
    from core.config import KELVIN_THRESHOLD, KELVIN_TO_C
except ImportError:
    KELVIN_THRESHOLD, KELVIN_TO_C = 100.0, 273.15

def calc_ws(u: float, v: float) -> float:
    return float(math.hypot(u, v))

def calc_wd(u: float, v: float) -> float:
    if math.isclose(u, 0.0, abs_tol=1e-5) and math.isclose(v, 0.0, abs_tol=1e-5):
        return 0.0
    return float((math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0)

@dataclass
class KimPoint:
    """KIM 모델 데이터 포인트"""
    # 필수 필드 (기본값 없음)
    tmfc: str
    hf: int
    site: str
    lat: float
    lon: float
    t2m: float
    rh2m: float
    u10m: float
    v10m: float
    hpbl: float
    p_data: Dict[str, Any]

    # 선택적 필드 (기본값 있음)
    u80m: float = 0.0
    v80m: float = 0.0
    hgt500: float = 0.0
    tmp500: float = 0.0
    ugrd500: float = 0.0
    vgrd500: float = 0.0
    hgt850: float = 0.0
    tmp850: float = 0.0
    ugrd850: float = 0.0
    vgrd850: float = 0.0
    tmp975: float = 0.0
    ugrd975: float = 0.0
    vgrd975: float = 0.0
    tmp950: float = 0.0
    ugrd950: float = 0.0
    vgrd950: float = 0.0
    tmp925: float = 0.0
    
    # 파생값 필드
    ws10: float = 0.0
    wd10: float = 0.0
    ws80: float = 0.0
    ws500: float = 0.0
    wd500: float = 0.0
    ws850: float = 0.0
    wd850: float = 0.0
    ws975: float = 0.0
    ws950: float = 0.0
    
    # 변화량 필드 (서비스 레이어 저장용)
    ddir500: float = 0.0
    ddir850: float = 0.0
    dspd500: float = 0.0
    dspd850: float = 0.0

    def __post_init__(self):
        # 1. 온도 변환 (K -> ℃)
        for field in fields(self):
            if any(key in field.name for key in ['tmp', 't2m']):
                val = getattr(self, field.name)
                if isinstance(val, (int, float)) and val > KELVIN_THRESHOLD:
                    setattr(self, field.name, round(val - KELVIN_TO_C, 2))
        
        # 2. 초기 계산 (객체 생성 시점의 u, v 기반)
        self.compute_derived()

    def compute_derived(self, prev_kp: Optional['KimPoint'] = None) -> None:
        """파생값 및 과거 데이터 대비 변화량 계산"""
        # 현재 성분 기반 풍속/풍향 갱신
        self.ws10, self.wd10 = calc_ws(self.u10m, self.v10m), calc_wd(self.u10m, self.v10m)
        self.ws80 = calc_ws(self.u80m, self.v80m) if (self.u80m != 0.0 or self.v80m != 0.0) else self.ws10
        self.ws500, self.wd500 = calc_ws(self.ugrd500, self.vgrd500), calc_wd(self.ugrd500, self.vgrd500)
        self.ws850, self.wd850 = calc_ws(self.ugrd850, self.vgrd850), calc_wd(self.ugrd850, self.vgrd850)
        self.ws975 = calc_ws(self.ugrd975, self.vgrd975)
        self.ws950 = calc_ws(self.ugrd950, self.vgrd950)

