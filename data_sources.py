"""
data_sources.py
─────────────────────────────────────────────
실데이터(공공데이터포털/네이버) 호출 + 키가 없을 때의 샘플 데이터.

핵심 원칙
  - 한국관광 데이터랩의 지역별 방문자 수는 KT(내국인)/SKT(외국인)로
    소스가 분리되어 있으므로, 내국인 지표만 사용한다.
  - API 호출이 실패하거나 키가 없으면 조용히 샘플 데이터로 폴백한다.
"""

import datetime as dt
import hashlib
from typing import Optional

import numpy as np
import pandas as pd
import requests

# 실데이터 fetch 함수(_fetch_datalab_*)가 구현되면 True로 변경.
# 이 값이 False면 키를 넣어도 앱은 '샘플 모드'로 정직하게 표시한다.
LIVE_IMPLEMENTED = False

# 분석 대상 지역(샘플). 실제로는 데이터랩 지역코드와 매핑.
REGIONS = [
    "강원 강릉시",
    "강원 속초시",
    "부산 해운대구",
    "제주 제주시",
    "경북 경주시",
    "전남 여수시",
    "충남 태안군",
    "경남 통영시",
]

AGE_BUCKETS = ["10대", "20대", "30대", "40대", "50대", "60대+"]


# ──────────────────────────────────────────────────────────────────────────
# 내부 유틸 : 지역명을 시드로 재현 가능한 랜덤 생성 (샘플 데이터용)
# ──────────────────────────────────────────────────────────────────────────
def _seed(region: str) -> np.random.Generator:
    h = int(hashlib.md5(region.encode()).hexdigest(), 16) % (2**32)
    return np.random.default_rng(h)


# ══════════════════════════════════════════════════════════════════════════
# 1) 월별 방문자 수 (수요 예측용)
# ══════════════════════════════════════════════════════════════════════════
def get_visitor_history(region: str, tour_key: Optional[str]) -> pd.DataFrame:
    """
    반환: DataFrame[ds(datetime), visitors(int)]  월 단위, 내국인 기준.
    실데이터 연결 시 _fetch_datalab_visitors() 를 채워 사용.
    """
    if tour_key:
        try:
            df = _fetch_datalab_visitors(region, tour_key)
            if df is not None and len(df) > 0:
                return df
        except Exception:
            pass  # 실패 시 샘플로 폴백
    return _sample_visitors(region)


def _fetch_datalab_visitors(region: str, key: str) -> Optional[pd.DataFrame]:
    """
    실데이터 훅(미연결 상태의 골격).
    공공데이터포털 '한국관광공사_빅데이터_지역별 방문자수' 오픈API를 호출해
    KT(내국인) 방문자 수를 월별로 집계하도록 구현하면 됩니다.

    예시 엔드포인트(지역마다 다름):
      https://apis.data.go.kr/B551011/DataLabService/locgoRegnVisitrDDList
    파라미터: serviceKey, MobileOS, MobileApp, startYmd, endYmd, signguCode ...
    응답에서 내국인(KT) 항목만 추출 → 월별 합계.

    실제 지역코드 매핑과 응답 파싱이 필요하므로, 키 발급 후 이 함수만 채우면
    앱 나머지는 수정 없이 실데이터로 작동합니다.
    """
    return None  # TODO: 키 발급 후 구현


def _sample_visitors(region: str) -> pd.DataFrame:
    """36개월치 가짜 월별 방문자 수. 추세 + 계절성 + 노이즈."""
    rng = _seed(region)
    n = 36
    end = dt.date.today().replace(day=1)
    months = pd.date_range(end=end, periods=n, freq="MS")

    base = rng.integers(80_000, 300_000)         # 지역별 기본 규모
    trend = np.linspace(0, rng.uniform(0.1, 0.4), n)  # 완만한 우상향
    # 계절성: 여름(7~8월), 가을(10월) 성수기
    month_idx = months.month
    seasonal = np.where(np.isin(month_idx, [7, 8]), 0.55,
               np.where(np.isin(month_idx, [10]), 0.35,
               np.where(np.isin(month_idx, [1, 2]), -0.15, 0.0)))
    noise = rng.normal(0, 0.05, n)
    visitors = base * (1 + trend + seasonal + noise)
    return pd.DataFrame({"ds": months, "visitors": visitors.clip(min=1000).astype(int)})


# ══════════════════════════════════════════════════════════════════════════
# 2) 연령대별 분포
# ══════════════════════════════════════════════════════════════════════════
def get_age_distribution(region: str, tour_key: Optional[str]) -> pd.DataFrame:
    """반환: DataFrame[age, share(0~1)]  내국인 방문자의 연령대 비중."""
    if tour_key:
        try:
            df = _fetch_datalab_age(region, tour_key)
            if df is not None and len(df) > 0:
                return df
        except Exception:
            pass
    return _sample_age(region)


def _fetch_datalab_age(region: str, key: str) -> Optional[pd.DataFrame]:
    """실데이터 훅. 데이터랩 연령대별 방문자 API에서 내국인 비중 추출."""
    return None  # TODO: 키 발급 후 구현


def _sample_age(region: str) -> pd.DataFrame:
    rng = _seed(region + "age")
    raw = rng.dirichlet(np.array([1.0, 2.6, 3.0, 2.4, 1.6, 1.0]))  # 20~40대 강세
    return pd.DataFrame({"age": AGE_BUCKETS, "share": raw})


# ══════════════════════════════════════════════════════════════════════════
# 3) 예능 여행지 (네이버 검색)
# ══════════════════════════════════════════════════════════════════════════
def get_variety_spots(keyword: str, naver_id: Optional[str], naver_secret: Optional[str]):
    """반환: list[dict(program, title, desc, link)]"""
    if naver_id and naver_secret:
        try:
            items = _fetch_naver(keyword, naver_id, naver_secret)
            if items:
                return items
        except Exception:
            pass
    return _sample_spots()


# 프로그램명 자동 감지용 대표 여행 예능 목록 (필요 시 추가)
KNOWN_SHOWS = [
    "1박 2일", "텐트 밖은 유럽", "나 혼자 산다", "삼시세끼", "백패커",
    "어쩌다 사장", "안다행", "지구오락실", "뿅뿅 지구오락실", "태어난 김에 세계일주",
    "서진이네", "콩콩팥팥", "출장 십오야",
]


def _fetch_naver(keyword: str, cid: str, secret: str):
    url = "https://openapi.naver.com/v1/search/blog.json"
    headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}
    params = {"query": keyword, "display": 8, "sort": "sim"}
    r = requests.get(url, headers=headers, params=params, timeout=8)
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        title = _clean(it.get("title", ""))
        desc = _clean(it.get("description", ""))[:120]
        link = it.get("link", "#")
        if not str(link).startswith(("http://", "https://")):
            link = "#"  # 이상한 링크는 무력화
        out.append({
            "program": _detect_show(title + " " + desc),
            "title": title,
            "desc": desc,
            "link": link,
        })
    return out


def _detect_show(text: str) -> str:
    """본문에서 알려진 예능 프로그램명을 찾아 라벨로 사용."""
    for show in KNOWN_SHOWS:
        if show in text:
            return show
    return "여행 예능"


def _clean(s: str) -> str:
    """
    네이버 응답의 <b> 등 태그 제거 후 HTML 이스케이프.
    (앱이 unsafe_allow_html로 렌더링하므로, 외부 텍스트는 반드시 이스케이프)
    """
    import html
    import re
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)      # &quot; 같은 엔티티를 일반 문자로
    return html.escape(s)     # 다시 안전하게 이스케이프


def _sample_spots():
    return [
        {"program": "텐트 밖은 유럽", "title": "강릉 안목해변 커피거리",
         "desc": "출연진이 찾은 동해안 감성 카페 거리. 일출 명소로도 유명.",
         "link": "https://search.naver.com/search.naver?query=강릉+안목해변"},
        {"program": "1박 2일", "title": "여수 밤바다 낭만포차",
         "desc": "야경과 포장마차 거리로 화제가 된 남해안 대표 여행지.",
         "link": "https://search.naver.com/search.naver?query=여수+밤바다"},
        {"program": "나 혼자 산다", "title": "제주 비자림 숲길",
         "desc": "힐링 산책 코스로 소개된 천년의 비자나무 숲.",
         "link": "https://search.naver.com/search.naver?query=제주+비자림"},
        {"program": "어쩌다 사장", "title": "경북 영주 부석사",
         "desc": "고즈넉한 산사 풍경으로 재조명된 가을 단풍 명소.",
         "link": "https://search.naver.com/search.naver?query=영주+부석사"},
        {"program": "백패커", "title": "통영 동피랑 벽화마을",
         "desc": "골목 벽화와 항구 전망이 어우러진 남해안 포토 스팟.",
         "link": "https://search.naver.com/search.naver?query=통영+동피랑"},
        {"program": "안다행", "title": "속초 영금정 일출",
         "desc": "설악과 동해를 한 번에 담는 강원 대표 일출 명소.",
         "link": "https://search.naver.com/search.naver?query=속초+영금정"},
    ]
