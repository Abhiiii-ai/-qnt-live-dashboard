
import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

st.set_page_config(
    page_title="QNT Live Terminal",
    page_icon="₿",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BINANCE = "https://api.binance.com"
SYMBOL = "QNTUSDT"

@st.cache_data(ttl=4, show_spinner=False)
def ticker():
    r = requests.get(f"{BINANCE}/api/v3/ticker/24hr",
                     params={"symbol": SYMBOL}, timeout=8)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=12, show_spinner=False)
def klines(interval="15m", limit=250):
    r = requests.get(f"{BINANCE}/api/v3/klines",
                     params={"symbol": SYMBOL, "interval": interval, "limit": limit},
                     timeout=8)
    r.raise_for_status()
    cols = ["open_time","open","high","low","close","volume","close_time",
            "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(r.json(), columns=cols)
    for c in ["open","high","low","close","volume","quote_volume",
              "taker_buy_base","taker_buy_quote"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df

@st.cache_data(ttl=4, show_spinner=False)
def depth(limit=20):
    r = requests.get(f"{BINANCE}/api/v3/depth",
                     params={"symbol": SYMBOL, "limit": limit}, timeout=8)
    r.raise_for_status()
    d = r.json()
    bids = [(float(p), float(q)) for p, q in d["bids"]]
    asks = [(float(p), float(q)) for p, q in d["asks"]]
    return bids, asks

def add_indicators(df):
    d = df.copy()
    d["ema20"] = d.close.ewm(span=20, adjust=False).mean()
    d["ema50"] = d.close.ewm(span=50, adjust=False).mean()
    d["vwap"] = (d.close * d.volume).cumsum() / d.volume.cumsum()
    d["vol_ma20"] = d.volume.rolling(20).mean()
    d["delta"] = 2 * d.taker_buy_base - d.volume
    d["cvd"] = d.delta.cumsum()
    return d

def money(x):
    return f"${x:,.2f}"

def pct(x):
    return f"{x:+.2f}%"

@st.fragment(run_every="5s")
def live_dashboard():
    try:
        t = ticker()
        df = add_indicators(klines("15m"))
        bids, asks = depth(20)

        last = float(t["lastPrice"])
        chg = float(t["priceChangePercent"])
        vol = float(t["quoteVolume"])
        high = float(t["highPrice"])
        low = float(t["lowPrice"])

        bid_qty = sum(q for _, q in bids)
        ask_qty = sum(q for _, q in asks)
        total_book = bid_qty + ask_qty
        imbalance = ((bid_qty - ask_qty) / total_book * 100) if total_book else 0

        latest_delta = float(df["delta"].iloc[-1])
        cvd_change = float(df["cvd"].iloc[-1] - df["cvd"].iloc[-21])
        vol_ma = float(df["vol_ma20"].iloc[-1])
        volume_ratio = float(df["volume"].iloc[-1] / vol_ma) if vol_ma else 0

        c = st.columns(4)
        c[0].metric("QNT / USDT", money(last))
        c[1].metric("24H", pct(chg))
        c[2].metric("24H Volume", f"${vol/1e6:.1f}M")
        c[3].metric("Book Imbalance", f"{imbalance:+.1f}%")

        c2 = st.columns(4)
        c2[0].metric("24H High", money(high))
        c2[1].metric("24H Low", money(low))
        c2[2].metric("15M Delta", f"{latest_delta:+,.0f} QNT")
        c2[3].metric("Volume / 20MA", f"{volume_ratio:.2f}×")

        left, right = st.columns([1.7, 1])

        with left:
            st.subheader("15M Price / VWAP / EMA")
            chart = df.set_index("time")[["close","ema20","ema50","vwap"]].rename(
                columns={"close":"Price","ema20":"EMA20","ema50":"EMA50","vwap":"VWAP"}
            )
            st.line_chart(chart.tail(150), height=360)

            st.subheader("15M Volume")
            st.bar_chart(df.set_index("time")[["volume"]].tail(100), height=210)

            st.subheader("CVD")
            st.line_chart(df.set_index("time")[["cvd"]].tail(150), height=230)

        with right:
            st.subheader("Signal Engine")
            score = 0
            reasons = []

            if last > float(df["vwap"].iloc[-1]):
                score += 1
                reasons.append("Price above VWAP")
            else:
                score -= 1
                reasons.append("Price below VWAP")

            if float(df["ema20"].iloc[-1]) > float(df["ema50"].iloc[-1]):
                score += 1
                reasons.append("EMA20 above EMA50")
            else:
                score -= 1
                reasons.append("EMA20 below EMA50")

            if cvd_change > 0:
                score += 1
                reasons.append("CVD rising")
            else:
                score -= 1
                reasons.append("CVD falling")

            if imbalance > 5:
                score += 1
                reasons.append("Bid depth stronger")
            elif imbalance < -5:
                score -= 1
                reasons.append("Ask depth stronger")

            if volume_ratio > 1.5:
                reasons.append("Volume spike")

            state = (
                "BULLISH WATCH" if score >= 2
                else "BEARISH WATCH" if score <= -2
                else "NEUTRAL"
            )
            st.metric("Market State", state)
            st.write(f"Composite score: **{score:+d}**")
            for r in reasons:
                st.write("• " + r)

            st.subheader("Order Book — Top 10")
            book = pd.DataFrame({
                "Bid": [p for p, _ in bids[:10]],
                "Bid QNT": [q for _, q in bids[:10]],
                "Ask": [p for p, _ in asks[:10]],
                "Ask QNT": [q for _, q in asks[:10]],
            })
            st.dataframe(book, use_container_width=True, hide_index=True, height=330)

            st.subheader("Flow")
            st.metric("20-candle CVD change", f"{cvd_change:+,.0f} QNT")

        st.divider()
        st.caption(
            "LIVE • Read-only • Binance QNT/USDT public market data • "
            "Auto-refresh ~5 seconds • Last update: "
            + datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        )
        st.info(
            "This dashboard is market-structure information, not an automatic trading system. "
            "CVD and order-book data are venue-specific; walls can be cancelled."
        )

    except Exception as e:
        st.error("Live data connection failed. The app will retry automatically.")
        st.caption(str(e))

st.title("QNT Live Terminal")
st.caption("Mobile-friendly cloud dashboard • No laptop required")
live_dashboard()
