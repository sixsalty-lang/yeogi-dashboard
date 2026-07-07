"""
forecast.py
─────────────────────────────────────────────
수요 예측(Prophet → 계절성 폴백) + Plotly 시각화 + 간단 인사이트 문구.
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go

PRIMARY = "#FF4757"
ACCENT = "#2E86DE"
BAND = "rgba(46,134,222,0.15)"
INK_LINE = "#1E272E"

# 설날·추석 (음력이라 매년 양력 날짜가 다름 → 직접 명시)
_SEOLLAL = ["2021-02-12", "2022-02-01", "2023-01-22", "2024-02-10",
            "2025-01-29", "2026-02-17", "2027-02-07", "2028-01-26"]
_CHUSEOK = ["2021-09-21", "2022-09-10", "2023-09-29", "2024-09-17",
            "2025-10-06", "2026-09-25", "2027-09-15", "2028-10-03"]


def _kr_holidays() -> pd.DataFrame:
    """
    한국 주요 연휴를 Prophet용으로 반환.
    ★ 중요: 이 앱의 데이터는 '월 단위(월초, MS)'라서, 공휴일 날짜를
    그대로 넣으면 월초가 아닌 날은 어떤 행과도 매칭되지 않아 효과가 0이 된다.
    그래서 모든 공휴일을 '해당 월의 1일'로 스냅해 "그 달에 큰 연휴가 있다"는
    월 단위 신호로 변환한다. (설날 1월/2월 이동도 자동 반영)
    """
    rows = []
    for d in _SEOLLAL:
        rows.append((d, "설날연휴"))
    for d in _CHUSEOK:
        rows.append((d, "추석연휴"))
    for y in range(2021, 2029):
        rows += [
            (f"{y}-05-05", "어린이날"),
            (f"{y}-08-15", "광복절"),
            (f"{y}-10-03", "개천절"),
        ]
    df = pd.DataFrame(rows, columns=["ds", "holiday"])
    df["ds"] = pd.to_datetime(df["ds"]).dt.to_period("M").dt.to_timestamp()  # 월초 스냅
    df = df.drop_duplicates(subset=["ds", "holiday"])
    return df


# ══════════════════════════════════════════════════════════════════════════
# 예측 본체
# ══════════════════════════════════════════════════════════════════════════
def forecast_demand(hist: pd.DataFrame, periods: int = 6):
    """
    반환: (result_df, method)
      result_df: ds, yhat, yhat_lower, yhat_upper  (과거+미래)
      method:    "prophet" 또는 "seasonal"
    데이터가 24개월 미만이거나 Prophet 미설치면 계절성 폴백.
    """
    if len(hist) >= 24:
        try:
            return _prophet(hist, periods), "prophet"
        except Exception:
            pass
    return _seasonal_naive(hist, periods), "seasonal"


def _prophet(hist: pd.DataFrame, periods: int) -> pd.DataFrame:
    from prophet import Prophet  # 지연 임포트 (미설치 시 except로 폴백)

    df = hist.rename(columns={"visitors": "y"})[["ds", "y"]]
    m = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        holidays=_kr_holidays(),
        interval_width=0.8,
    )
    m.fit(df)
    future = m.make_future_dataframe(periods=periods, freq="MS")
    fcst = m.predict(future)
    return fcst[["ds", "yhat", "yhat_lower", "yhat_upper"]]


def _seasonal_naive(hist: pd.DataFrame, periods: int) -> pd.DataFrame:
    """
    계절성 단순 예측: 작년 같은 달 값 + 최근 추세율.
    데이터가 짧을 때도 깨지지 않는 안전한 폴백.
    """
    h = hist.copy().reset_index(drop=True)
    h["month"] = h["ds"].dt.month
    monthly = h.groupby("month")["visitors"].mean()
    overall = h["visitors"].mean()

    # 추세율: 최근 12개월 vs 그 직전 12개월 (같은 계절 구성끼리 비교해 왜곡 방지)
    if len(h) >= 24:
        recent = h["visitors"].tail(12).mean()
        before = h["visitors"].iloc[-24:-12].mean()
        growth = np.clip(recent / max(before, 1), 0.8, 1.5)
    else:
        growth = 1.0

    last = h["ds"].max()
    future_ds = pd.date_range(
        start=last + pd.offsets.MonthBegin(1), periods=periods, freq="MS"
    )
    preds = []
    for d in future_ds:
        base = monthly.get(d.month, overall)
        preds.append(base * growth)
    preds = np.array(preds)

    fut = pd.DataFrame({
        "ds": future_ds,
        "yhat": preds,
        "yhat_lower": preds * 0.85,
        "yhat_upper": preds * 1.15,
    })
    past = pd.DataFrame({
        "ds": h["ds"], "yhat": h["visitors"],
        "yhat_lower": h["visitors"], "yhat_upper": h["visitors"],
    })
    return pd.concat([past, fut], ignore_index=True)


# ══════════════════════════════════════════════════════════════════════════
# 시각화
# ══════════════════════════════════════════════════════════════════════════
def plot_forecast(hist: pd.DataFrame, result: pd.DataFrame, region: str) -> go.Figure:
    split = hist["ds"].max()
    fig = go.Figure()

    # 불확실 범위(음영)
    fig.add_trace(go.Scatter(
        x=list(result["ds"]) + list(result["ds"][::-1]),
        y=list(result["yhat_upper"]) + list(result["yhat_lower"][::-1]),
        fill="toself", fillcolor=BAND, line=dict(color="rgba(0,0,0,0)"),
        hoverinfo="skip", showlegend=False,
    ))
    # 실제 방문자
    fig.add_trace(go.Scatter(
        x=hist["ds"], y=hist["visitors"], mode="lines+markers",
        name="실제 방문자", line=dict(color=INK_LINE, width=2),
    ))
    # 예측선
    fut = result[result["ds"] > split]
    fig.add_trace(go.Scatter(
        x=fut["ds"], y=fut["yhat"], mode="lines+markers",
        name="예측", line=dict(color=PRIMARY, width=3, dash="dash"),
    ))
    fig.add_vline(x=split, line_width=1, line_dash="dot", line_color="#aaa")
    fig.update_layout(
        height=420, margin=dict(t=20, b=20, l=10, r=10),
        legend=dict(orientation="h", y=1.1),
        yaxis_title="월 방문자 수(명)", xaxis_title=None,
        plot_bgcolor="white",
    )
    fig.update_yaxes(gridcolor="#f1f2f6")
    return fig


def plot_age_bar(age_df: pd.DataFrame, region: str) -> go.Figure:
    colors = [PRIMARY if s == age_df["share"].max() else ACCENT for s in age_df["share"]]
    fig = go.Figure(go.Bar(
        x=age_df["age"], y=age_df["share"] * 100,
        marker_color=colors,
        text=[f"{s*100:.0f}%" for s in age_df["share"]],
        textposition="outside",
    ))
    fig.update_layout(
        height=420, margin=dict(t=30, b=20, l=10, r=10),
        yaxis_title="비중(%)", plot_bgcolor="white", showlegend=False,
    )
    fig.update_yaxes(gridcolor="#f1f2f6")
    return fig


# ══════════════════════════════════════════════════════════════════════════
# 인사이트 문구
# ══════════════════════════════════════════════════════════════════════════
def age_insight(age_df: pd.DataFrame, region: str) -> str:
    top = age_df.loc[age_df["share"].idxmax()]
    second = age_df.sort_values("share", ascending=False).iloc[1]
    return (
        f"{region} 방문객은 **{top['age']}**({top['share']*100:.0f}%)와 "
        f"**{second['age']}**({second['share']*100:.0f}%) 비중이 가장 높습니다. "
        f"해당 연령대를 겨냥한 채널·크리에이티브에 예산을 우선 배분하고, "
        f"성수기 1~2개월 전부터 노출을 강화하는 전략이 효과적입니다."
    )
