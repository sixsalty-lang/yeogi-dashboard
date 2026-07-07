"""
여기어때 국내여행 인사이트 대시보드  (v4 - 메뉴형 UI)
─────────────────────────────────────────────
왼쪽: 로고 + 메뉴 내비게이션 / 오른쪽: 페이지별 콘텐츠.
지역 선택·예측 기간 등 조작 요소는 각 페이지 상단에 배치.

메뉴
  📈 수요 예측   : 지역별 방문자 추이 + Prophet 예측 + CSV
  🏆 뜨는 지역   : 전년 동월 대비 성장률 Top/Bottom 랭킹 (전국)
  ⚖️ 지역 비교   : 최대 4개 지역 방문자 추이 오버레이
  👥 연령대별    : 네이버 검색 관심도 기반 연령 분포(추정)
  📺 예능 여행지 : 예능에 나온 여행지 콘텐츠 수집
  ⚙️ 설정        : API 키 입력 · 데이터 상태 · 오류 기록
"""

import pandas as pd
import streamlit as st

import data_sources as ds
import forecast as fc

# ──────────────────────────────────────────────────────────────────────────
# 페이지 기본 설정 + 스타일
# ──────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="여기어때 국내여행 인사이트", page_icon="🧳", layout="wide")

PRIMARY = "#FF4757"
ACCENT = "#2E86DE"
INK = "#1E272E"
SIDE_BG = "#1B2440"

st.markdown(
    f"""
    <style>
    .block-container {{padding-top: 1.6rem; max-width: 1200px;}}
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

    /* ── 사이드바: 어두운 네이비 + 메뉴형 라디오 ── */
    [data-testid="stSidebar"] {{background: {SIDE_BG};}}
    [data-testid="stSidebar"] * {{color: #E8ECF6;}}
    [data-testid="stSidebar"] hr {{border-color: #2E3A5C;}}
    [data-testid="stSidebar"] [role="radiogroup"] label {{
        display:block; padding:10px 16px; border-radius:12px; margin:3px 0;
        font-weight:600; font-size:15px; cursor:pointer;
        transition: background .15s;
    }}
    [data-testid="stSidebar"] [role="radiogroup"] label:hover {{background:#26325A;}}
    [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {{
        background:{PRIMARY};
    }}
    /* 라디오 동그라미 숨기고 글자만 메뉴처럼 */
    [data-testid="stSidebar"] [role="radiogroup"] label > div:first-child {{display:none;}}
    </style>
    """,
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────────────────────────────────
# 키: Secrets 금고 우선, 설정 페이지 입력이 있으면 그것이 우선
# ──────────────────────────────────────────────────────────────────────────
_sec = st.secrets if hasattr(st, "secrets") else {}
tour_key = st.session_state.get("in_tour", "") or (_sec.get("TOUR_KEY", "") if _sec else "")
naver_id = st.session_state.get("in_nid", "") or (_sec.get("NAVER_ID", "") if _sec else "")
naver_secret = st.session_state.get("in_nsecret", "") or (_sec.get("NAVER_SECRET", "") if _sec else "")


# ──────────────────────────────────────────────────────────────────────────
# 데이터 로딩 (전국 1회 수집, 6시간 캐시)
# ──────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=6 * 3600,
               show_spinner="전국 방문자 데이터를 처음 받아오는 중... 최초 1회만 1~3분 걸립니다. "
                            "이후에는 지역을 바꿔도 즉시 표시돼요.")
def cached_national(key: str) -> pd.DataFrame | None:
    return ds.fetch_national_monthly(key)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_age(region: str, nid: str | None, nsecret: str | None) -> pd.DataFrame:
    return ds.get_age_distribution(region, nid, nsecret)


@st.cache_data(ttl=1800, show_spinner=False)
def cached_spots(keyword: str, nid: str | None, nsecret: str | None):
    return ds.get_variety_spots(keyword, nid, nsecret)


def visitors_for(region_display: str, region_code: str | None,
                 nat: pd.DataFrame | None) -> pd.DataFrame:
    if nat is not None and region_code:
        df = ds.filter_region_code(nat, region_code)
        if df is not None and len(df) >= 6:
            return df
        ds._log_err(f"방문자 데이터: '{region_display}' 추출 실패 또는 6개월 미만")
    return ds.get_visitor_history(region_display, None)  # 샘플 폴백


nat = None
live = False
if tour_key and ds.LIVE_IMPLEMENTED:
    try:
        nat = cached_national(tour_key)
        live = nat is not None and len(nat) > 0
    except Exception as e:
        ds._log_err(f"전국 데이터 수집 오류: {type(e).__name__}: {e}")

nat_src = nat if live else ds.sample_national()
region_opts = ds.region_options(nat_src)   # 지역 비교용 평면 목록
tree = ds.region_tree(nat_src)             # 수요 예측용 계층 목록

naver_live = bool(naver_id and naver_secret)


def region_selector():
    """도·광역시 -> 시군구('전체' 포함) 2단 선택. 페이지 간 선택 유지."""
    provs = list(tree.keys())
    c1, c2 = st.columns(2)
    with c1:
        d_prov = "강원" if "강원" in provs else provs[0]
        if "prov_sel" not in st.session_state or st.session_state["prov_sel"] not in provs:
            st.session_state["prov_sel"] = d_prov
        prov = st.selectbox("광역 (도·시)", provs, key="prov_sel")
    cities = ["전체"] + list(tree[prov].keys())
    with c2:
        if ("city_sel" not in st.session_state
                or st.session_state["city_sel"] not in cities):
            st.session_state["city_sel"] = "전체"
        city = st.selectbox("시·군·구", cities, key="city_sel")

    if city == "전체":
        display = f"{prov} 전체"
        hist = ds.filter_province(nat_src, tree[prov].values())
    else:
        display = f"{prov} {city}" if not city.startswith(prov) else city
        hist = ds.filter_region_code(nat_src, tree[prov][city])

    if hist is None or len(hist) < 6:
        ds._log_err(f"방문자 데이터: '{display}' 추출 실패 또는 6개월 미만")
        hist = ds.get_visitor_history(display, None)  # 샘플 폴백
    return display, hist


# ──────────────────────────────────────────────────────────────────────────
# 사이드바: 로고 + 메뉴 + 데이터 소스 상태
# ──────────────────────────────────────────────────────────────────────────
with st.sidebar:
    import os
    if os.path.exists("logo.png"):
        _l, _c, _r = st.columns([1, 3, 1])
        with _c:
            st.image("logo.png", use_container_width=True)
        st.markdown(
            "<div style='text-align:center; color:#C9D4EE; font-size:14px; "
            "font-weight:700'>국내여행 인사이트</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div style='text-align:center; padding:14px 0 6px'>
                <div style='font-size:34px; font-weight:900; color:{PRIMARY};
                            letter-spacing:-1.5px; line-height:1'>여기어때</div>
                <div style='color:#C9D4EE; font-size:14px; font-weight:700;
                            margin-top:8px'>국내여행 인사이트</div>
                <div style='color:#6B7A9F; font-size:11px; margin-top:2px'>
                    Domestic Travel Demand Dashboard</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown("---")

    page = st.radio(
        "메뉴",
        ["📈 수요 예측", "🏆 뜨는 지역", "⚖️ 지역 비교",
         "👥 연령대별", "📺 예능 여행지", "⚙️ 설정"],
        label_visibility="collapsed",
    )

    st.markdown("---")
    dot_v = "#2ecc71" if live else "#f1c40f"
    dot_n = "#2ecc71" if naver_live else "#f1c40f"
    st.markdown(
        f"""
        <div style='font-size:12px; color:#9AA7C7; font-weight:700;
                    margin-bottom:6px'>데이터 소스</div>
        <div style='font-size:13px; line-height:2'>
            <span style='color:{dot_v}'>●</span> 관광공사 방문자 (내국인 여행자)<br>
            <span style='color:{dot_n}'>●</span> 네이버 데이터랩·검색
        </div>
        <div style='color:#6B7A9F; font-size:11px; margin-top:10px'>
            ● 초록=실데이터 · 노랑=샘플<br>키 입력은 ⚙️ 설정에서
        </div>
        """,
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════
# 📈 수요 예측
# ══════════════════════════════════════════════════════════════════════════
if page == "📈 수요 예측":
    st.markdown("# 📈 국내여행 수요 예측")
    st.markdown(
        "<span class='tag'>내국인 여행자(외지인) 기준</span>"
        "<span class='tag'>KT 이동통신 기반</span>"
        "<span class='tag'>현지인·외국인 제외</span>",
        unsafe_allow_html=True,
    )
    st.write("")

    c1, c2 = st.columns([3, 2])
    with c1:
        region, hist = region_selector()
    with c2:
        horizon = st.slider("예측 기간(개월)", 3, 12, 6)

    last = hist.iloc[-1]
    last_label = last["ds"].strftime("%Y년 %m월")
    if len(hist) >= 13:
        prev = hist.iloc[-13]
        yoy = (last["visitors"] / prev["visitors"] - 1) * 100 if prev["visitors"] else 0
        yoy_html = f"<h2 style='color:{PRIMARY if yoy>=0 else ACCENT}'>{yoy:+.1f}%</h2>"
    else:
        yoy_html = "<h2 style='color:#8395a7'>-</h2><div class='src'>13개월 이상 쌓이면 표시</div>"

    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(
            f"<div class='metric-card'><div class='src'>최근 완성 월({last_label}) 방문자</div>"
            f"<h2>{int(last['visitors']):,}명</h2></div>", unsafe_allow_html=True)
    with m2:
        st.markdown(
            f"<div class='metric-card'><div class='src'>전년 동월 대비</div>{yoy_html}</div>",
            unsafe_allow_html=True)
    with m3:
        peak = hist.loc[hist["visitors"].idxmax()]
        st.markdown(
            f"<div class='metric-card'><div class='src'>성수기(최다 방문)</div>"
            f"<h2>{peak['ds'].strftime('%Y-%m')}</h2></div>", unsafe_allow_html=True)

    st.write("")
    result, method = fc.forecast_demand(hist, periods=horizon)
    if method == "prophet":
        st.success("Prophet 시계열 모델로 예측했습니다. (설날·추석 등 연휴·계절성 반영, 음영=불확실 범위)")
    elif method == "seasonal_short":
        st.warning("이 지역은 데이터 기간이 24개월 미만이라 계절성 추세 그래프로 보여드립니다.")
    else:
        st.warning("예측 모델(Prophet)이 서버에 설치되지 않았거나 계산에 실패해 "
                   "계절성 추세 그래프로 보여드립니다. requirements.txt에 prophet이 "
                   "포함돼 있는지 확인하세요.")
    st.plotly_chart(fc.plot_forecast(hist, result, region), use_container_width=True)

    with st.expander("📋 예측 수치 표로 보기 / 내려받기"):
        table = result.tail(horizon).rename(
            columns={"ds": "월", "yhat": "예측", "yhat_lower": "하한", "yhat_upper": "상한"}
        ).round(0)
        st.dataframe(table, use_container_width=True)
        st.download_button(
            "⬇️ CSV로 내려받기 (보고서용)",
            data=table.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{region}_수요예측_{horizon}개월.csv",
            mime="text/csv",
        )

    st.caption(
        "출처: 한국관광공사 지역별 방문자수(KT 이동통신 기반). "
        "★ '외지인'(타지역에서 온 내국인)만 집계 — 현지인·외국인 제외로 "
        "국내여행 수요에 가장 근접한 지표입니다. 발표 시차로 미완성 달은 자동 제외됩니다."
    )

# ══════════════════════════════════════════════════════════════════════════
# 🏆 뜨는 지역
# ══════════════════════════════════════════════════════════════════════════
elif page == "🏆 뜨는 지역":
    st.markdown("# 🏆 뜨는 지역 랭킹")
    st.caption("최근 완성 월 기준, 전년 동월 대비 방문자 성장률 순위입니다. "
               "광고 예산을 어디에 실을지 빠르게 훑어보세요.")

    rank_src = nat if live else ds.sample_national()

    # ── 🚀 다음 달 주목 여행지 (선행 지표) ──
    next_month = (pd.Timestamp.today() + pd.DateOffset(months=1)).month
    hot = ds.compute_next_month_hot(rank_src, next_month)
    if hot is not None:
        base_ym, hot_df = hot
        st.markdown(f"### 🚀 다음 달({next_month}월)에 뜰 여행지 Top 10")
        st.caption(
            f"작년 같은 달({base_ym}) 데이터 기준, 그 지역의 평소 대비 {next_month}월 "
            "방문 강세를 '계절지수'로 계산했습니다. 100 초과 = 평소보다 붐비는 달. "
            "최근 성장률이 함께 높다면 올해는 더 뜰 가능성이 큽니다."
        )
        fmt = {hot_df.columns[1]: "{:,.0f}", "계절지수": "{:.0f}"}
        if "최근 성장률" in hot_df.columns:
            fmt["최근 성장률"] = "{:+.1f}%"
        st.dataframe(hot_df.head(10).style.format(fmt, na_rep="-"),
                     use_container_width=True, hide_index=True)
        st.divider()

    ranking = ds.compute_growth_ranking(rank_src)

    if ranking is None or len(ranking) == 0:
        st.info("랭킹을 계산할 데이터가 부족합니다. 13개월 이상 데이터가 필요해요.")
    else:
        base_month = ranking["최근월"].iloc[0]
        n_show = st.slider("표시 개수", 5, 20, 10)
        st.markdown(f"**기준: {base_month} vs 전년 동월** · 총 {len(ranking)}개 지역")

        up, down = st.columns(2)
        show_cols = ["지역", "방문자", "전년동월", "성장률"]
        with up:
            st.markdown(f"### 🔥 성장 Top {n_show}")
            top = ranking.head(n_show)[show_cols].copy()
            st.dataframe(
                top.style.format({"방문자": "{:,.0f}", "전년동월": "{:,.0f}",
                                  "성장률": "{:+.1f}%"}),
                use_container_width=True, hide_index=True,
            )
        with down:
            st.markdown(f"### 🧊 감소 Top {n_show}")
            bottom = ranking.tail(n_show)[show_cols].iloc[::-1].copy()
            st.dataframe(
                bottom.style.format({"방문자": "{:,.0f}", "전년동월": "{:,.0f}",
                                     "성장률": "{:+.1f}%"}),
                use_container_width=True, hide_index=True,
            )

        st.download_button(
            "⬇️ 전체 랭킹 CSV 내려받기",
            data=ranking.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"뜨는지역_랭킹_{base_month}.csv",
            mime="text/csv",
        )
        st.caption("출처: 한국관광공사 지역별 방문자수 — 외지인(내국인 여행자) 기준. "
                   "전년 동월 데이터가 있는 지역만 포함됩니다.")

# ══════════════════════════════════════════════════════════════════════════
# ⚖️ 지역 비교
# ══════════════════════════════════════════════════════════════════════════
elif page == "⚖️ 지역 비교":
    st.markdown("# ⚖️ 지역 비교")
    st.caption("최대 4개 지역의 방문자 추이를 겹쳐 봅니다. 예산 배분 판단용.")

    names = list(region_opts.keys())
    default_pick = [n for n in ["강원 강릉시", "강원 속초시"] if n in names] or names[:2]
    picked = st.multiselect("비교할 지역 (2~4곳)", names, default=default_pick, max_selections=4)

    if len(picked) < 2:
        st.info("두 곳 이상 선택하면 비교 차트가 나타납니다.")
    else:
        series = {}
        for name in picked:
            df = ds.filter_region_code(nat_src, region_opts.get(name))
            if df is None or len(df) < 6:
                df = ds.get_visitor_history(name, None)
            series[name] = df
        st.plotly_chart(fc.plot_compare(series), use_container_width=True)

        rows = []
        for name, df in series.items():
            last = df.iloc[-1]
            yoy = None
            if len(df) >= 13 and df.iloc[-13]["visitors"]:
                yoy = (last["visitors"] / df.iloc[-13]["visitors"] - 1) * 100
            rows.append({
                "지역": name,
                "최근월": last["ds"].strftime("%Y-%m"),
                "방문자": int(last["visitors"]),
                "전년 대비": f"{yoy:+.1f}%" if yoy is not None else "-",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("출처: 한국관광공사 지역별 방문자수 — 외지인(내국인 여행자) 기준.")

# ══════════════════════════════════════════════════════════════════════════
# 👥 연령대별
# ══════════════════════════════════════════════════════════════════════════
elif page == "👥 연령대별":
    st.markdown("# 👥 연령대별 수요 분포")
    region, _hist = region_selector()

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
# 📺 예능 여행지
# ══════════════════════════════════════════════════════════════════════════
elif page == "📺 예능 여행지":
    st.markdown("# 📺 예능에 나온 여행지 모음")
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

    # ── 📊 언급 지역 카운트 x 뜨는 지역 대조 ──
    counts = ds.extract_region_counts(cards, nat if live else ds.sample_national())
    if counts is not None and len(counts) > 0:
        st.divider()
        st.markdown("### 📊 예능 콘텐츠 언급 지역 vs 실제 성장률")
        st.caption(
            "수집된 콘텐츠에서 시군구 지명 언급을 세고, '뜨는 지역'의 전년 대비 "
            "성장률과 붙여봤습니다. 언급도 많고 성장률도 높다면 방송 효과가 "
            "실제 수요로 이어지고 있다는 신호입니다. (지명 매칭은 근사치)"
        )
        rank = ds.compute_growth_ranking(nat if live else ds.sample_national())
        if rank is not None:
            counts = counts.merge(
                rank[["지역", "성장률"]].rename(columns={"성장률": "전년 대비 성장률"}),
                on="지역", how="left")
        fmt = {}
        if "전년 대비 성장률" in counts.columns:
            fmt["전년 대비 성장률"] = "{:+.1f}%"
        st.dataframe(counts.head(15).style.format(fmt, na_rep="-"),
                     use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════
# ⚙️ 설정
# ══════════════════════════════════════════════════════════════════════════
elif page == "⚙️ 설정":
    st.markdown("# ⚙️ 설정")

    st.markdown("### 🔑 API 키")
    if (_sec and (_sec.get("TOUR_KEY") or _sec.get("NAVER_ID"))):
        st.success("배포 설정(Secrets)에 저장된 키를 사용 중입니다. "
                   "아래에 입력하면 그 키가 우선됩니다.")
    st.text_input("공공데이터포털 서비스키 (한국관광공사)", type="password",
                  key="in_tour",
                  help="data.go.kr 활용신청 후 발급된 '일반 인증키(Decoding)' 사용")
    st.text_input("네이버 Client ID", type="password", key="in_nid")
    st.text_input("네이버 Client Secret", type="password", key="in_nsecret")

    st.markdown("### 📡 데이터 상태")
    st.markdown(
        f"- 방문자 데이터: {'🟢 실데이터 (외지인=내국인 여행자)' if live else '🟡 샘플'}\n"
        f"- 연령대·예능: {'🟢 실데이터 (네이버)' if naver_live else '🟡 샘플'}"
    )

    if ds.LAST_ERRORS:
        st.markdown("### ⚠️ 데이터 연결 오류 기록")
        for msg in reversed(ds.LAST_ERRORS):
            st.caption(msg)
        st.caption("오류가 반복되면 이 내용을 복사해 Claude에게 보여주세요.")
