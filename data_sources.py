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

# 코드 버전 (설정 페이지에 표시 — 파일 교체 누락 확인용)
VERSION = "v6"

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

# 지역코드 앞 2자리 -> 광역시도 (강원 42→51, 전북 45→52 개편 이력 모두 포함)
PROVINCE_BY_CODE = {
    "11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주",
    "30": "대전", "31": "울산", "36": "세종", "41": "경기",
    "42": "강원", "51": "강원", "43": "충북", "44": "충남",
    "45": "전북", "52": "전북", "46": "전남", "47": "경북",
    "48": "경남", "50": "제주",
}

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
    """'강원 강릉시' -> '강릉 여행', '부산 전체' -> '부산 여행' (네이버 검색어용)."""
    parts = region.split()
    city = parts[0] if (len(parts) > 1 and parts[-1] == "전체") else parts[-1]
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
            nat = fetch_national_monthly(tour_key, months)
            df = filter_region(nat, region)
            if df is not None and len(df) >= 6:
                return df
            _log_err(f"방문자 API: '{region}' 결과가 비었거나 6개월 미만")
        except Exception as e:
            _log_err(f"방문자 API 오류: {type(e).__name__}: {e}")
    return _sample_visitors(region)


def filter_region(national: Optional[pd.DataFrame], region: str) -> Optional[pd.DataFrame]:
    """전국 월별 데이터에서 특정 지역만 추출. 앱에서 즉시 지역 전환용."""
    if national is None or len(national) == 0:
        return None
    city = _city_name(region)
    df = national[national["signguNm"].str.contains(city, na=False)]
    if len(df) == 0:
        return None
    out = df.groupby("ds", as_index=False)["visitors"].sum()
    out["visitors"] = out["visitors"].round().astype(int)
    return out.sort_values("ds").reset_index(drop=True)


def fetch_national_monthly(key: str, months: int = 36) -> Optional[pd.DataFrame]:
    """
    한국관광공사 '지역별 방문자수' API에서 전국 시군구의
    외지인(touDivCd=2) 방문자 수를 [ds(월초), signguNm, visitors]로 반환.

    ★ 설계: API는 어차피 전국 데이터를 통째로 주므로, 한 번만 받아
    캐시해 두고 지역 선택은 filter_region()으로 즉시 처리한다.
    (지역 바꿀 때마다 재호출하지 않음 → 속도·호출량 절약)
    """
    end = dt.date.today().replace(day=1) - dt.timedelta(days=1)  # 지난달 말일
    start = (end.replace(day=1) - pd.DateOffset(months=months - 1)).date()

    acc: dict[tuple, float] = {}  # (ym, signguNm) -> 합계
    days_seen: dict[str, set] = {}  # ym -> 데이터가 존재하는 날짜 집합 (완성도 판별용)
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
                ymd = str(it.get("baseYmd", ""))
                ym = ymd[:6]
                name = str(it.get("signguNm", ""))
                # 응답 필드명이 signguCode/signguCd 두 표기가 있어 모두 대응
                cd = str(it.get("signguCode") or it.get("signguCd") or "")
                acc[(ym, cd, name)] = acc.get((ym, cd, name), 0.0) + float(it.get("touNum", 0))
                days_seen.setdefault(ym, set()).add(ymd)

            total = int(body.get("totalCount", 0))
            if page * 10000 >= total or not rows:
                break
            page += 1

        cur = (pd.Timestamp(cur) + pd.offsets.MonthBegin(1)).date()

    if not acc:
        return None

    # ★ 미완성 월 제외: 집계 지연으로 며칠치만 올라온 달이 섞이면
    # 전년 대비/예측이 크게 왜곡되므로, 달력 일수를 못 채운 달은 뺀다.
    import calendar
    complete = set()
    for ym, days in days_seen.items():
        y, m = int(ym[:4]), int(ym[4:6])
        need = calendar.monthrange(y, m)[1]
        if len(days) >= need:
            complete.add(ym)
        else:
            _log_err(f"미완성 월 제외: {ym[:4]}-{ym[4:]} "
                     f"({len(days)}/{need}일만 집계됨 — 발표 시차)")
    acc = {k: v for k, v in acc.items() if k[0] in complete}
    if not acc:
        return None

    keys = sorted(acc)
    return pd.DataFrame({
        "ds": pd.to_datetime([k[0] for k in keys], format="%Y%m"),
        "signguCd": [k[1] for k in keys],
        "signguNm": [k[2] for k in keys],
        "visitors": [acc[k] for k in keys],
    })


def region_options(national: Optional[pd.DataFrame]) -> dict:
    """
    전국 데이터에서 선택 가능한 지역 목록을 만든다.
    반환: {"강원 강릉시": "51150", ...}  (표시명 -> 지역코드)
    동명 시군구(예: 강원/경남 고성군)는 광역시도로 구분된다.
    """
    if national is None or len(national) == 0:
        return {}
    pairs = national[["signguCd", "signguNm"]].drop_duplicates()
    opts = {}
    for cd, nm in pairs.itertuples(index=False):
        prov = PROVINCE_BY_CODE.get(str(cd)[:2], "")
        display = f"{prov} {nm}".strip()
        # 같은 표시명이 이미 있으면(드묾) 코드 뒤 3자리로 구분
        if display in opts and opts[display] != cd:
            display = f"{display}({str(cd)[-3:]})"
        opts[display] = cd
    return dict(sorted(opts.items()))


def filter_region_code(national: Optional[pd.DataFrame], code: str) -> Optional[pd.DataFrame]:
    """지역코드로 정확히 추출. 앱의 지역 선택은 이 함수를 쓴다."""
    if national is None or len(national) == 0 or not code:
        return None
    df = national[national["signguCd"] == code]
    if len(df) == 0:
        return None
    out = df.groupby("ds", as_index=False)["visitors"].sum()
    out["visitors"] = out["visitors"].round().astype(int)
    return out.sort_values("ds").reset_index(drop=True)


def _display_name(cd: str, nm: str) -> str:
    prov = PROVINCE_BY_CODE.get(str(cd)[:2], "")
    return f"{prov} {nm}".strip()


def compute_growth_ranking(national: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """
    전국 데이터에서 지역별 '전년 동월 대비 성장률'을 계산.
    반환: DataFrame[지역, 최근월, 방문자, 전년동월, 성장률] (성장률 내림차순)
    최근 완성 월과 그 12개월 전이 모두 있는 지역만 포함.
    """
    if national is None or len(national) == 0:
        return None
    last = national["ds"].max()
    prev = last - pd.DateOffset(years=1)

    cur = (national[national["ds"] == last]
           .groupby(["signguCd", "signguNm"], as_index=False)["visitors"].sum())
    old = (national[national["ds"] == prev]
           .groupby(["signguCd", "signguNm"], as_index=False)["visitors"].sum()
           .rename(columns={"visitors": "prev_visitors"}))
    m = cur.merge(old, on=["signguCd", "signguNm"])
    m = m[m["prev_visitors"] > 0]
    if len(m) == 0:
        return None
    m["성장률"] = (m["visitors"] / m["prev_visitors"] - 1) * 100
    m["지역"] = [_display_name(c, n) for c, n in zip(m["signguCd"], m["signguNm"])]
    out = m[["지역", "visitors", "prev_visitors", "성장률"]].rename(
        columns={"visitors": "방문자", "prev_visitors": "전년동월"})
    out.insert(1, "최근월", last.strftime("%Y-%m"))
    return out.sort_values("성장률", ascending=False).reset_index(drop=True)


def sample_national() -> pd.DataFrame:
    """키가 없을 때 랭킹/비교 화면용 샘플 전국 데이터 (대표 8곳)."""
    frames = []
    for i, r in enumerate(REGIONS):
        df = _sample_visitors(r).copy()
        df["signguCd"] = f"S{i:03d}"
        df["signguNm"] = r  # 표시명 그대로 (코드가 매핑에 없으니 _display_name이 이름만 반환)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)[["ds", "signguCd", "signguNm", "visitors"]]


def region_tree(national: Optional[pd.DataFrame]) -> dict:
    """
    도·광역시 -> 시군구 계층 목록.
    반환: {"강원": {"강릉시": "51150", "속초시": "51210", ...}, ...}
    (샘플 데이터는 signguNm이 '강원 강릉시' 형태라 이름에서 분리)
    """
    tree: dict = {}
    if national is None or len(national) == 0:
        return tree
    pairs = national[["signguCd", "signguNm"]].drop_duplicates()
    for cd, nm in pairs.itertuples(index=False):
        prov = PROVINCE_BY_CODE.get(str(cd)[:2], "")
        city = nm
        if not prov and " " in nm:  # 샘플: '강원 강릉시'
            prov, city = nm.split(" ", 1)
        prov = prov or "기타"
        tree.setdefault(prov, {})[city] = cd
    return {p: dict(sorted(c.items())) for p, c in sorted(tree.items())}


def filter_province(national: Optional[pd.DataFrame], codes) -> Optional[pd.DataFrame]:
    """광역(도·시) 단위 합산: 소속 시군구 코드 전체의 월별 합계."""
    if national is None or len(national) == 0:
        return None
    df = national[national["signguCd"].isin(list(codes))]
    if len(df) == 0:
        return None
    out = df.groupby("ds", as_index=False)["visitors"].sum()
    out["visitors"] = out["visitors"].round().astype(int)
    return out.sort_values("ds").reset_index(drop=True)


def compute_next_month_hot(national: Optional[pd.DataFrame],
                           target_month: int) -> Optional[tuple]:
    """
    '다음 달에 뜰 여행지': 작년 같은 달의 계절 강세 지역.
    계절지수 = (작년 해당월 방문자) / (그 지역 전 기간 월평균) x 100
      -> 100 초과일수록 그 달에 특히 강한 지역.
    반환: (기준연월 문자열, DataFrame[지역, 작년 M월 방문자, 계절지수, 최근 성장률])
    """
    if national is None or len(national) == 0:
        return None
    cands = sorted({d for d in national["ds"] if d.month == target_month})
    if not cands:
        return None
    tgt = cands[-1]  # 해당 월의 가장 최근(작년) 데이터

    grp = ["signguCd", "signguNm"]
    v_t = (national[national["ds"] == tgt]
           .groupby(grp, as_index=False)["visitors"].sum()
           .rename(columns={"visitors": "target"}))
    avg = (national.groupby(grp, as_index=False)["visitors"].mean()
           .rename(columns={"visitors": "avg"}))
    m = v_t.merge(avg, on=grp)
    m = m[m["avg"] > 0]
    if len(m) == 0:
        return None
    m["계절지수"] = m["target"] / m["avg"] * 100
    m["지역"] = [_display_name(c, n) for c, n in zip(m["signguCd"], m["signguNm"])]

    out = m[["지역", "target", "계절지수"]].rename(
        columns={"target": f"작년 {target_month}월 방문자"})

    rank = compute_growth_ranking(national)
    if rank is not None:
        out = out.merge(rank[["지역", "성장률"]].rename(columns={"성장률": "최근 성장률"}),
                        on="지역", how="left")
    return tgt.strftime("%Y-%m"), out.sort_values("계절지수", ascending=False).reset_index(drop=True)


def extract_region_counts(cards: list, national: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """
    예능 여행지 카드(제목+설명)에서 시군구 지명 언급을 카운트.
    반환: DataFrame[지역, 언급 수] (언급 수 내림차순). 지명 매칭은 근사치.
    """
    if not cards or national is None or len(national) == 0:
        return None
    # 시군구명에서 접미사(시/군/구)를 뗀 기본 지명 목록 (2글자 이상만)
    bases = {}
    for cd, nm in national[["signguCd", "signguNm"]].drop_duplicates().itertuples(index=False):
        city = nm.split(" ", 1)[1] if (" " in nm) else nm
        base = city
        for suf in ("특별자치시", "시", "군", "구"):
            if base.endswith(suf) and len(base) > len(suf) + 1:
                base = base[: -len(suf)]
                break
        if len(base) >= 2:
            bases[base] = _display_name(cd, nm) if " " not in nm else nm
    counts: dict = {}
    for c in cards:
        text = f"{c.get('title','')} {c.get('desc','')}"
        for base, display in bases.items():
            if base in text:
                counts[display] = counts.get(display, 0) + 1
    if not counts:
        return None
    df = pd.DataFrame(sorted(counts.items(), key=lambda x: -x[1]),
                      columns=["지역", "언급 수"])
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
    """
    최신순으로 넉넉히(50건) 받아 '작성일이 확인되는 최근 90일 이내' 글만 남긴다.
    작성일(postdate)이 없거나 형식이 이상한 글은 오래된 글일 수 있어 제외한다.
    """
    url = "https://openapi.naver.com/v1/search/blog.json"
    headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}
    params = {"query": keyword, "display": 50, "sort": "date"}
    r = requests.get(url, headers=headers, params=params, timeout=8)
    r.raise_for_status()
    cutoff = (dt.date.today() - dt.timedelta(days=90)).strftime("%Y%m%d")
    out = []
    for it in r.json().get("items", []):
        postdate = str(it.get("postdate", "")).strip()
        # 엄격 필터: 8자리 날짜가 확인되고 90일 이내인 글만 통과
        if len(postdate) != 8 or not postdate.isdigit() or postdate < cutoff:
            continue
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
            "date": f"{postdate[:4]}.{postdate[4:6]}.{postdate[6:]}",
            "_pd": postdate,
        })
    out.sort(key=lambda c: c["_pd"], reverse=True)  # 최신 글부터
    return out[:20]


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
