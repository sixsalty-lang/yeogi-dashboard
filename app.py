"""
여기어때 국내여행 인사이트 대시보드
─────────────────────────────────────────────
국내여행 광고주를 위한 데이터 솔루션 (내국인 기준)

탭 구성
  1) 국내여행 수요 예측   : Prophet 기반 예측(데이터 부족 시 계절성 그래프로 자동 폴백)
  2) 연령대별 데이터       : 지역 x 연령대 수요 분포
  3) 예능 여행지 모음      : 네이버 검색 API 자동 수집(키 없으면 샘플)

* API 키가 없어도 '샘플 데이터'로 모든 화면이 그대로 작동합니다.
* 사이드바에 키를 입력하면 실데이터로 전환됩니다.
"""

import pandas as pd
import streamlit as st

import data_sources as ds
import forecast as fc


# ── 캐시: 같은 조건이면 재호출/재계산 생략 (1시간) ──────────────────────
@st.cache_data(ttl=6 * 3600,
               show_spinner="전국 방문자 데이터를 처음 받아오는 중... 최초 1회만 1~3분 걸립니다. "
                            "이후에는 지역을 바꿔도 즉시 표시돼요.")
def cached_national(key: str) -> pd.DataFrame | None:
    """전국 월별 데이터를 6시간 캐시. 모든 지역이 이 캐시를 공유."""
    return ds.fetch_national_monthly(key)


def cached_visitors(region: str, key: str | None) -> pd.DataFrame:
    if key:
        try:
            nat = cached_national(key)
            df = ds.filter_region(nat, region)
            if df is not None and len(df) >= 6:
                return df
            ds._log_err(f"방문자 API: '{region}' 결과가 비었거나 6개월 미만")
        except Exception as e:
            ds._log_err(f"방문자 API 오류: {type(e).__name__}: {e}")
    return ds.get_visitor_history(region, None)  # 샘플 폴백


@st.cache_data(ttl=3600, show_spinner=False)
def cached_age(region: str, nid: str | None, nsecret: str | None) -> pd.DataFrame:
    return ds.get_age_distribution(region, nid, nsecret)


@st.cache_data(ttl=1800, show_spinner=False)
def cached_spots(keyword: str, nid: str | None, nsecret: str | None):
    return ds.get_variety_spots(keyword, nid, nsecret)

# ──────────────────────────────────────────────────────────────────────────
# 페이지 기본 설정
# ──────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="여기어때 국내여행 인사이트",
    page_icon="🧳",
    layout="wide",
)

PRIMARY = "#FF4757"   # 여기어때 레드 계열
ACCENT = "#2E86DE"
INK = "#1E272E"

st.markdown(
    f"""
    <style>
    .block-container {{padding-top: 2rem; max-width: 1200px;}}
    h1, h2, h3 {{color: {INK};}}
    .metric-card {{
        background: #fff; border: 1px solid #eee; border-radius: 16px;
        padding: 18px 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.04);
    }}
    .tag {{
        display:inline-block; background:{PRIMARY}1A; color:{PRIMARY};
        padding:2px 10px; border-radius:999px; font-size:12px; margin-right:6px;
    }}
    .src {{color:#8395a7; font-size:12px;}}
    </style>
    """,
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────────────────────────────────
# 사이드바 : API 키 입력 + 전역 필터
# ──────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ 설정")
    st.caption("키를 비워두면 샘플 데이터로 작동합니다.")

    # Secrets 금고(배포 설정)에 저장된 키가 있으면 자동 사용
    _sec = st.secrets if hasattr(st, "secrets") else {}
    sec_tour = _sec.get("TOUR_KEY", "") if _sec else ""
    sec_nid = _sec.get("NAVER_ID", "") if _sec else ""
    sec_nsecret = _sec.get("NAVER_SECRET", "") if _sec else ""

    with st.expander("🔑 API 키 입력", expanded=False):
        if sec_tour or sec_nid:
            st.success("배포 설정(Secrets)에 저장된 키를 사용 중입니다. "
                       "아래에 입력하면 그 키가 우선됩니다.")
        in_tour = st.text_input(
            "공공데이터포털 서비스키 (한국관광공사)",
            type="password",
            help="data.go.kr 회원가입 → 활용신청 후 발급 (일반 인증키/Decoding 권장)",
        )
        in_nid = st.text_input("네이버 Client ID", type="password")
        in_nsecret = st.text_input("네이버 Client Secret", type="password")

    # 입력값 우선, 없으면 Secrets
    tour_key = in_tour or sec_tour
    naver_id = in_nid or sec_nid
    naver_secret = in_nsecret or sec_nsecret

    st.divider()
    region = st.selectbox(
        "📍 분석 지역",
        ds.REGIONS,
        index=ds.REGIONS.index("강원 강릉시") if "강원 강릉시" in ds.REGIONS else 0,
    )
    horizon = st.slider("예측 기간(개월)", 3, 12, 6)

    st.divider()
    if tour_key and ds.LIVE_IMPLEMENTED:
        st.markdown("**방문자 데이터:** 🟢 실데이터 (외지인=내국인 여행자)")
    else:
        st.markdown("**방문자 데이터:** 🟡 샘플")
    if naver_id and naver_secret:
        st.markdown("**연령대·예능:** 🟢 실데이터 (네이버)")
    else:
        st.markdown("**연령대·예능:** 🟡 샘플")

    if ds.LAST_ERRORS:
        with st.expander("⚠️ 데이터 연결 오류 기록"):
            for msg in reversed(ds.LAST_ERRORS):
                st.caption(msg)
            st.caption("오류가 반복되면 이 내용을 복사해 Claude에게 보여주세요.")

# ──────────────────────────────────────────────────────────────────────────
# 헤더
# ──────────────────────────────────────────────────────────────────────────
st.markdown("# 🧳 여기어때 국내여행 인사이트")
st.markdown(
    "<span class='tag'>내국인 여행자(외지인) 기준</span>"
    "<span class='tag'>KT 이동통신 기반</span>"
    "<span class='tag'>현지인·외국인 제외</span>",
    unsafe_allow_html=True,
)
st.write("")

tab1, tab2, tab3 = st.tabs(["📈 수요 예측", "👥 연령대별", "📺 예능 여행지"])

# ══════════════════════════════════════════════════════════════════════════
# 탭 1 : 수요 예측
# ══════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader(f"{region} · 국내여행 수요 예측")

    hist = cached_visitors(region, tour_key or None)  # 월별 방문자 수(내국인)

    # 최근 지표 카드
    last = hist.iloc[-1]
    prev = hist.iloc[-13] if len(hist) >= 13 else hist.iloc[0]
    yoy = (last["visitors"] / prev["visitors"] - 1) * 100 if prev["visitors"] else 0

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            f"<div class='metric-card'><div class='src'>최근 월 방문자</div>"
            f"<h2>{int(last['visitors']):,}명</h2></div>",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"<div class='metric-card'><div class='src'>전년 동월 대비</div>"
            f"<h2 style='color:{PRIMARY if yoy>=0 else ACCENT}'>{yoy:+.1f}%</h2></div>",
            unsafe_allow_html=True,
        )
    with c3:
        peak = hist.loc[hist["visitors"].idxmax()]
        st.markdown(
            f"<div class='metric-card'><div class='src'>성수기(최다 방문)</div>"
            f"<h2>{peak['ds'].strftime('%Y-%m')}</h2></div>",
            unsafe_allow_html=True,
        )

    st.write("")

    # 예측 실행 (Prophet → 실패 시 계절성 폴백)
    result, method = fc.forecast_demand(hist, periods=horizon)

    if method == "prophet":
        st.success("Prophet 시계열 모델로 예측했습니다. (연휴·계절성 반영, 음영=불확실 범위)")
    else:
        st.warning(
            "데이터 기간이 짧아 통계 예측 모델 대신 **계절성 추세 그래프**로 보여드립니다. "
            "데이터가 2~3년 이상 쌓이면 자동으로 예측 모델로 전환됩니다."
        )

    st.plotly_chart(fc.plot_forecast(hist, result, region), use_container_width=True)

    with st.expander("📋 예측 수치 표로 보기 / 내려받기"):
        table = result.tail(horizon).rename(
            columns={"ds": "월", "yhat": "예측", "yhat_lower": "하한", "yhat_upper": "상한"}
        ).round(0)
        st.dataframe(table, use_container_width=True)
        st.download_button(
            "⬇️ CSV로 내려받기 (보고서용)",
            data=table.to_csv(index=False).encode("utf-8-sig"),  # 엑셀 한글 깨짐 방지
            file_name=f"{region}_수요예측_{horizon}개월.csv",
            mime="text/csv",
        )

    st.caption(
        "출처: 한국관광공사 지역별 방문자수(KT 이동통신 기반). "
        "★ '외지인'(타지역에서 온 내국인)만 집계 — 현지인·외국인 제외로 "
        "국내여행 수요에 가장 근접한 지표입니다. 방문자는 일자별 순방문자 기준."
    )

# ══════════════════════════════════════════════════════════════════════════
# 탭 2 : 연령대별 데이터
# ══════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader(f"{region} · 연령대별 수요 분포")

    age_df = cached_age(region, naver_id or None, naver_secret or None)

    cL, cR = st.columns([3, 2])
    with cL:
        st.plotly_chart(fc.plot_age_bar(age_df, region), use_container_width=True)
    with cR:
        top = age_df.loc[age_df["share"].idxmax()]
        st.markdown(
            f"<div class='metric-card'><div class='src'>핵심 타깃 연령</div>"
            f"<h2>{top['age']}</h2>"
            f"<div class='src'>전체의 {top['share']*100:.0f}%</div></div>",
            unsafe_allow_html=True,
        )
        st.write("")
        st.markdown("**🎯 광고 집행 제안**")
        st.write(fc.age_insight(age_df, region))

    st.caption(
        "출처: 네이버 데이터랩 검색어트렌드 — 최근 12개월 '{지역} 여행' 검색 관심도의 "
        "연령대별 분포(추정치). 내국인 국내방문의 연령별 방문자 수는 오픈API로 "
        "제공되지 않아, 광고 타깃팅에 직결되는 '검색 관심도'로 대신 추정합니다."
    )

# ══════════════════════════════════════════════════════════════════════════
# 탭 3 : 예능 여행지
# ══════════════════════════════════════════════════════════════════════════
with tab3:
    st.subheader("📺 예능에 나온 여행지 모음")
    st.caption("네이버 검색을 통해 여행 예능에 등장한 국내 여행지 콘텐츠를 모읍니다.")

    kw = st.text_input("검색 키워드", value="예능 국내여행 여행지")
    cards = cached_spots(kw, naver_id or None, naver_secret or None)

    if not cards:
        st.info("결과가 없습니다. 키워드를 바꿔보세요.")
    else:
        cols = st.columns(2)
        for i, c in enumerate(cards):
            with cols[i % 2]:
                st.markdown(
                    f"""
                    <div class='metric-card' style='margin-bottom:14px'>
                        <div class='tag'>{c['program']}</div>
                        <h4 style='margin:8px 0'>{c['title']}</h4>
                        <p style='color:#57606f;font-size:14px;margin:0'>{c['desc']}</p>
                        <a href='{c['link']}' target='_blank' class='src'>관련 콘텐츠 보기 ↗</a>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    st.caption("출처: 네이버 검색 API. 자동 수집 결과로, 실제 방영 정보와 다를 수 있습니다.")
