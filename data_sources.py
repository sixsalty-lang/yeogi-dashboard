"""
data_sources.py  (v3 - 실데이터 연결판)
─────────────────────────────────────────────
실데이터(공공데이터포털/네이버) 호출 + 키가 없을 때의 샘플 데이터.

데이터 원칙
  - 수요(방문자): 한국관광공사 '지역별 방문자수' API에서
    ★외지인(touDivCd=2, 타지역에서 온 내국인)만 사용.
    현지인(1, 동네 주민)과 외국인(3)은 제외 → '국내여행 수요'에 정확히 부합.
  - 연령대: 내국인 국내방문의 연령별 오픈API가 없어,
    네이버 데이터랩 '검색어트렌드'의 연령 필터로 '검색 관심도 기반 추정 분포'를 계산.
  - API 호출이 실패하거나 키가 없으면 샘플 데이터로 폴백하고,
    실패 사유는 LAST_ERRORS에 기록해 앱에서 확인 가능.
"""

import datetime as dt
import hashlib
import json
from typing import Optional

import numpy as np
import pandas as pd
import requests

# 실데이터 연결 함수 구현 여부 (배지 표시용)
LIVE_IMPLEMENTED = True

# 실데이터 호출 실패 시 사유가 쌓이는 곳 (앱 사이드바에서 확인)
LAST_ERRORS: list[str] = []

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

# 관광공사 API 기본 주소
KTO_BASE = "https://apis.data.go.kr/B551011/DataLabService/locgoRegnVisitrDDList"

# 네이버 데이터랩 검색어트렌드
NAVER_DATALAB = "https://openapi.naver.com/v1/datalab/search"

# 네이버 데이터랩 연령 코드 매핑 (공식: 1=0~12, 2=13~18, 3=19~24, 4=25~29,
# 5=30~34, 6=35~39, 7=40~44, 8=45~49, 9=50~54, 10=55~59, 11=60~)
NAVER_AGE_MAP = {
    "10대": ["2"],
    "20대": ["3", "4"],
    "30대": ["5", "6"],
    "40대": ["7", "8"],
    "50대": ["9", "10"],
    "60대+": ["11"],
}


def _log_err(msg: str):
    LAST_ERRORS.append(f"[{dt.datetime.now():%H:%M:%S}] {msg}")
    del LAST_ERRORS[:-20]  # 최근 20건만 유지


def _seed(region: str) -> np.random.Generator:
    h = int(hashlib.md5(region.encode()).hexdigest(), 16) % (2**32)
    return np.random.default_rng(h)


def _city_name(region: str) -> str:
    """'강원 강릉시' -> '강릉시', '강릉' 검색어용은 city_keyword 사용."""
    return region.split()[-1]


def _city_keyword(region: str) -> str:
    """'강원 강릉시' -> '강릉 여행' (네이버 검색어용)."""
    city = region.split()[-1]
    for suffix in ("특별자치시", "광역시", "시", "군", "구"):
        if city.endswith(suffix):
            city = city[: -len(suffix)]
            break
    return f"{city} 여행"


# ══════════════════════════════════════════════════════════════════════════
# 1) 월별 방문자 수 (수요 예측용) - 외지인(내국인 여행자)만
# ══════════════════════════════════════════════════════════════════════════
def get_visitor_history(region: str, tour_key: Optional[str],
                        months: int = 24) -> pd.DataFrame:
    """반환: DataFrame[ds(datetime, 월초), visitors(int)]"""
    if tour_key:
        try:
            df = _fetch_datalab_visitors(region, tour_key, months)
            if df is not None and len(df) >= 6:
                return df
            _log_err(f"방문자 API: '{region}' 결과가 비었거나 6개월 미만")
        except Exception as e:
            _log_err(f"방문자 API 오류: {type(e).__name__}: {e}")
    return _sample_visitors(region)


def _fetch_datalab_visitors(region: str, key: str, months: int) -> Optional[pd.DataFrame]:
    """
    한국관광공사 '지역별 방문자수' API에서 해당 시군구의
    외지인(touDivCd=2) 방문자 수를 월별 합계로 반환.

    API는 일 단위 · 전국 시군구 데이터를 주므로,
    월 단위로 나눠 페이지를 돌며 받아온 뒤 지역명으로 필터링한다.
    """
    city = _city_name(region)
    end = dt.date.today().replace(day=1) - dt.timedelta(days=1)  # 지난달 말일
    start = (end.replace(day=1) - pd.DateOffset(months=months - 1)).date()

    monthly: dict[str, float] = {}
    cur = start.replace(day=1)
    session = requests.Session()

    while cur <= end:
        month_end = (pd.Timestamp(cur) + pd.offsets.MonthEnd(0)).date()
        page = 1
        while True:
            params = {
                "serviceKey": key,
                "numOfRows": 10000,
                "pageNo": page,
                "MobileOS": "ETC",
                "MobileApp": "yeogi-dashboard",
                "startYmd": cur.strftime("%Y%m%d"),
                "endYmd": min(month_end, end).strftime("%Y%m%d"),
                "_type": "json",
            }
            r = session.get(KTO_BASE, params=params, timeout=20)
            r.raise_for_status()
            try:
                body = r.json()["response"]["body"]
            except (json.JSONDecodeError, KeyError):
                # 키 오류 등은 XML 에러문서로 오는 경우가 많음
                snippet = r.text[:200].replace("\n", " ")
                raise RuntimeError(f"API 응답 형식 이상 (키/승인 확인 필요): {snippet}")

            items = body.get("items") or {}
            rows = items.get("item") or []
            if isinstance(rows, dict):
                rows = [rows]

            for it in rows:
                # 외지인(2)만: 타지역에서 온 내국인 = 국내여행 수요
                if str(it.get("touDivCd")) != "2":
                    continue
                if city not in str(it.get("signguNm", "")):
                    continue
                ym = str(it.get("baseYmd", ""))[:6]
                monthly[ym] = monthly.get(ym, 0.0) + float(it.get("touNum", 0))

            total = int(body.get("totalCount", 0))
            if page * 10000 >= total or not rows:
                break
            page += 1

        cur = (pd.Timestamp(cur) + pd.offsets.MonthBegin(1)).date()

    if not monthly:
        return None
    df = pd.DataFrame(
        {"ds": pd.to_datetime(sorted(monthly), format="%Y%m"),
         "visitors": [monthly[k] for k in sorted(monthly)]}
    )
    df["visitors"] = df["visitors"].round().astype(int)
    return df


def _sample_visitors(region: str) -> pd.DataFrame:
    rng = _seed(region)
    n = 36
    end = dt.date.today().replace(day=1)
    months = pd.date_range(end=end, periods=n, freq="MS")
    base = rng.integers(80_000, 300_000)
    trend = np.linspace(0, rng.uniform(0.1, 0.4), n)
    month_idx = months.month
    seasonal = np.where(np.isin(month_idx, [7, 8]), 0.55,
               np.where(np.isin(month_idx, [10]), 0.35,
               np.where(np.isin(month_idx, [1, 2]), -0.15, 0.0)))
    noise = rng.normal(0, 0.05, n)
    visitors = base * (1 + trend + seasonal + noise)
    return pd.DataFrame({"ds": months, "visitors": visitors.clip(min=1000).astype(int)})


# ══════════════════════════════════════════════════════════════════════════
# 2) 연령대별 분포 - 네이버 데이터랩 검색 관심도 기반 '추정'
# ══════════════════════════════════════════════════════════════════════════
def get_age_distribution(region: str, naver_id: Optional[str],
                         naver_secret: Optional[str]) -> pd.DataFrame:
    """
    반환: DataFrame[age, share(0~1)]
    내국인 국내방문의 연령별 '방문자 수' 오픈API가 없어,
    네이버 검색어트렌드의 연령 필터로 관심도 분포를 추정한다.
    """
    if naver_id and naver_secret:
        try:
            df = _fetch_naver_age(region, naver_id, naver_secret)
            if df is not None and len(df) == len(AGE_BUCKETS):
                return df
            _log_err(f"연령 추정: '{region}' 결과 불충분")
        except Exception as e:
            _log_err(f"네이버 데이터랩 오류: {type(e).__name__}: {e}")
    return _sample_age(region)


def _fetch_naver_age(region: str, cid: str, secret: str) -> Optional[pd.DataFrame]:
    """
    연령대별로 '{지역} 여행' 검색 비율을 조회해 분포를 추정.

    ★ 방법 설명: 네이버 데이터랩의 비율은 요청 안에서 최댓값=100으로
    정규화되어, 연령대별 '별도 요청'끼리는 직접 비교가 안 된다.
    그래서 각 요청에 기준 키워드('날씨')를 함께 넣고
    (지역 평균 ÷ 기준 평균) 비율로 정규화해 연령 간 비교가 가능하게 한다.
    결과는 어디까지나 '검색 관심도 기반 추정치'다.
    """
    kw = _city_keyword(region)
    end = dt.date.today().replace(day=1) - dt.timedelta(days=1)
    start = end - dt.timedelta(days=365)
    headers = {
        "X-Naver-Client-Id": cid,
        "X-Naver-Client-Secret": secret,
        "Content-Type": "application/json",
    }
    scores = {}
    for bucket, codes in NAVER_AGE_MAP.items():
        body = {
            "startDate": start.strftime("%Y-%m-%d"),
            "endDate": end.strftime("%Y-%m-%d"),
            "timeUnit": "month",
            "keywordGroups": [
                {"groupName": "지역", "keywords": [kw]},
                {"groupName": "기준", "keywords": ["날씨"]},
            ],
            "ages": codes,
        }
        r = requests.post(NAVER_DATALAB, headers=headers,
                          data=json.dumps(body), timeout=10)
        r.raise_for_status()
        results = {g["title"]: g["data"] for g in r.json().get("results", [])}
        region_avg = np.mean([d["ratio"] for d in results.get("지역", [])] or [0])
        anchor_avg = np.mean([d["ratio"] for d in results.get("기준", [])] or [0])
        scores[bucket] = (region_avg / anchor_avg) if anchor_avg else 0.0

    total = sum(scores.values())
    if total <= 0:
        return None
    return pd.DataFrame(
        {"age": AGE_BUCKETS, "share": [scores[a] / total for a in AGE_BUCKETS]}
    )


def _sample_age(region: str) -> pd.DataFrame:
    rng = _seed(region + "age")
    raw = rng.dirichlet(np.array([1.0, 2.6, 3.0, 2.4, 1.6, 1.0]))
    return pd.DataFrame({"age": AGE_BUCKETS, "share": raw})


# ══════════════════════════════════════════════════════════════════════════
# 3) 예능 여행지 (네이버 검색)
# ══════════════════════════════════════════════════════════════════════════
KNOWN_SHOWS = [
    "1박 2일", "텐트 밖은 유럽", "나 혼자 산다", "삼시세끼", "백패커",
    "어쩌다 사장", "안다행", "지구오락실", "뿅뿅 지구오락실", "태어난 김에 세계일주",
    "서진이네", "콩콩팥팥", "출장 십오야",
]


def get_variety_spots(keyword: str, naver_id: Optional[str], naver_secret: Optional[str]):
    if naver_id and naver_secret:
        try:
            items = _fetch_naver_blog(keyword, naver_id, naver_secret)
            if items:
                return items
            _log_err(f"블로그 검색: '{keyword}' 결과 없음")
        except Exception as e:
            _log_err(f"네이버 검색 오류: {type(e).__name__}: {e}")
    return _sample_spots()


def _fetch_naver_blog(keyword: str, cid: str, secret: str):
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
            link = "#"
        out.append({
            "program": _detect_show(title + " " + desc),
            "title": title,
            "desc": desc,
            "link": link,
        })
    return out


def _detect_show(text: str) -> str:
    for show in KNOWN_SHOWS:
        if show in text:
            return show
    return "여행 예능"


def _clean(s: str) -> str:
    import html
    import re
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return html.escape(s)


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
