# -*- coding: utf-8 -*-
"""
양산시 악취 예보 시스템 - 환경설정
main.py에서 환경설정 부분만 분리
"""
import os
import logging
import httpx
from typing import Dict, List

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("odor")

# ============================================================
# 1) 5개 격자 지점 (양산시 주변: 북/서/중/동/남)
# PDF 기준: "2개 지점"은 '중부'과 '동부'만 사용
# ============================================================
# LOCATIONS: Dict[str, Dict[str, float]] = {
#     "북부": {"lat": 35.40, "lon": 129.04},
#     "서부": {"lat": 35.33, "lon": 128.95},
#     "중부": {"lat": 35.335, "lon": 129.037},  # PDF 기준 지점
#     "동부": {"lat": 35.33, "lon": 129.10},    # PDF 기준 지점
#     "남부": {"lat": 35.28, "lon": 129.04},
# }
LOCATIONS: Dict[str, Dict[str, float]] = {
    "북부": {"lat": 35.475, "lon": 129.075, "topo": 80}, 
    "서부": {"lat": 35.400, "lon": 128.925, "topo": 40}, 
    "중부": {"lat": 35.375, "lon": 129.050, "topo": 22},  
    "동부": {"lat": 35.425, "lon": 129.175, "topo": 110},      
    "남부": {"lat": 35.300, "lon": 129.025, "topo": 5}
}

# PDF 기준 사용 지점 (중부, 동부만)
MAIN_SITES = ["중부", "동부", "서부","남부","북부"]
# MAIN_SITES = ["중부", "동부"]

# ============================================================
# 2) KMA KIM API 설정
# ============================================================
DEFAULT_AUTH_KEY = "ZVCdZ89kTgCQnWfPZP4Apg"  # 기관 키

KMA_AUTH_KEY = (
    os.getenv("KMA_AUTH_KEY")
    or os.getenv("KMA_KIM_API_KEY")
    or DEFAULT_AUTH_KEY
).strip()

if os.getenv("KMA_AUTH_KEY") or os.getenv("KMA_KIM_API_KEY"):
    logger.info(f"✅ 인증키: 환경변수에서 로드됨 (길이: {len(KMA_AUTH_KEY)})")
else:
    logger.info(f"✅ 인증키: 기본 키 사용 중")

# 사용자 유형 설정 (기본값: 기관)
USER_TYPE = os.getenv("KMA_USER_TYPE", "institutional").lower()

# 엔드포인트 설정
_DEFAULT_INSTITUTIONAL_URL = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-kim_nc_pt_txt2"
_DEFAULT_GENERAL_URL = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-kim_nc_pt_txt2"
KMA_BASE_URL = os.getenv(
    "KMA_BASE_URL",
    _DEFAULT_INSTITUTIONAL_URL if USER_TYPE == "institutional" else _DEFAULT_GENERAL_URL,
)

# 모델/그룹 설정
KIM_GROUP = os.getenv("KIM_GROUP", "KIMG")
KIM_NWP = os.getenv("KIM_NWP", "NE57")

if USER_TYPE == "institutional":
    logger.info("🔧 기관사용자 모드로 설정됨")
    logger.info(f"📋 기본 설정: 그룹={KIM_GROUP}, NWP={KIM_NWP}")
else:
    logger.info("🔧 일반사용자 모드로 설정됨")

logger.info(f"📋 API 엔드포인트: {KMA_BASE_URL}")

# 네트워크 안정화 설정
CONNECT_TIMEOUT = float(os.getenv("KMA_HTTP_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("KMA_HTTP_READ_TIMEOUT", "60"))
WRITE_TIMEOUT = float(os.getenv("KMA_HTTP_WRITE_TIMEOUT", "10"))
POOL_TIMEOUT = float(os.getenv("KMA_HTTP_POOL_TIMEOUT", "10"))
HTTP_TIMEOUT = httpx.Timeout(
    connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=WRITE_TIMEOUT, pool=POOL_TIMEOUT
)
RETRY = int(os.getenv("KMA_HTTP_RETRY", "10"))
BACKOFF_BASE = float(os.getenv("KMA_HTTP_BACKOFF_BASE", "2.0"))
JITTER_SEC = float(os.getenv("KMA_HTTP_JITTER_SEC", "0.3"))
SLEEP_BETWEEN_CALLS = float(os.getenv("KMA_SLEEP_BETWEEN_CALLS", "0.2"))
MAX_CONCURRENCY = int(os.getenv("KMA_MAX_CONCURRENCY", "3"))

logger.info(
    f"🌐 HTTP 설정 connect={CONNECT_TIMEOUT}s read={READ_TIMEOUT}s "
    f"retry={RETRY} max_concurrency={MAX_CONCURRENCY}"
)

# 결측 보정 설정
HOLE_FILL_PASSES = 4
HOLE_FILL_DELAY_SEC = 0.4

# 캐시 설정
KMA_CACHE_MAX_ENTRIES = 2000

# Kelvin 판별 임계값
KELVIN_THRESHOLD = 150.0
KELVIN_TO_C = 273.15

# ============================================================
# 3) 기상 모델 및 데이터 처리 상수
# ============================================================
# KIM 모델 업데이트 주기 (UTC 기준)
KIM_CYCLE_HOURS = [0, 6, 12, 18]


def get_max_hf_needed(requested_hours: int) -> int:
    return requested_hours + 12

def validate_location(site_name: str) -> bool:
    """요청된 지점이 시스템에서 지원하는 지점인지 확인"""
    return site_name in MAIN_SITES

