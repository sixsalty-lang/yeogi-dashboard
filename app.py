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
@st.cache_data(ttl=3600, show_spinner=False)
def cached_visitors(region: str, key: str | None) -> pd.DataFrame:
    return ds.get_visitor_history(region, key)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_age(region: str, key: str | None) -> pd.DataFrame:
    return ds.get_age_distribution(region, key)


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

    with st.expander("🔑 API 키 입력", expanded=False):
        tour_key = st.text_input(
            "공공데이터포털 서비스키 (한국관광공사)",
            type="password",
            help="data.go.kr 회원가입 → 활용신청 후 발급",
        )
        naver_id = st.text_input("네이버 Client ID", type="password")
        naver_secret = st.text_input("네이버 Client Secret", type="password")

    st.divider()
    region = st.selectbox(
        "📍 분석 지역",
        ds.REGIONS,
        index=ds.REGIONS.index("강원 강릉시") if "강원 강릉시" in ds.REGIONS else 0,
    )
    horizon = st.slider("예측 기간(개월)", 3, 12, 6)

    st.divider()
    if tour_key and ds.LIVE_IMPLEMENTED:
        st.markdown("**데이터 모드:** 🟢 실데이터")
    elif tour_key:
        st.markdown("**데이터 모드:** 🟡 샘플")
        st.caption("키는 입력됐지만 실데이터 연결 함수가 아직 미구현 상태입니다. "
                   "(data_sources.py의 _fetch_datalab_* 완성 필요)")
    else:
        st.markdown("**데이터 모드:** 🟡 샘플")

# ──────────────────────────────────────────────────────────────────────────
# 헤더
# ──────────────────────────────────────────────────────────────────────────
st.markdown("# 🧳 여기어때 국내여행 인사이트")
st.markdown(
    "<span class='tag'>내국인 기준</span>"
    "<span class='tag'>KT 이동통신 기반</span>"
    "<span class='tag'>외국인 지표 제외</span>",
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
        "출처: 한국관광 데이터랩(KT 내국인 이동통신 기반 지역별 방문자 수). "
        "방문자는 일상생활권을 벗어나 머문 순방문자 기준입니다."
    )

# ══════════════════════════════════════════════════════════════════════════
# 탭 2 : 연령대별 데이터
# ══════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader(f"{region} · 연령대별 수요 분포")

    age_df = cached_age(region, tour_key or None)

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

    st.caption("출처: 한국관광 데이터랩 이동통신 기반 연령대별 방문자 비중(내국인).")

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
