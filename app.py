
import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

st.set_page_config(
    page_title="QNT Live Terminal",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BINANCE = "https://data-api.binance.vision"
SYMBOL = "QNTUSDT"

# ---------- Market data ----------
@st.cache_data(ttl=4, show_spinner=False)
def ticker():
    r = requests.get(
        f"{BINANCE}/api/v3/ticker/24hr",
        params={"symbol": SYMBOL},
        timeout=8,
    )
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=10, show_spinner=False)
def klines(interval="15m", limit=250):
    r = requests.get(
        f"{BINANCE}/api/v3/klines",
        params={"symbol": SYMBOL, "interval": interval, "limit": limit},
        timeout=8,
    )
    r.raise_for_status()
    cols = [
        "open_time","open","high","low","close","volume","close_time",
        "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
    ]
    df = pd.DataFrame(r.json(), columns=cols)
    for c in ["open","high","low","close","volume","quote_volume",
              "taker_buy_base","taker_buy_quote"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df

@st.cache_data(ttl=3, show_spinner=False)
def depth(limit=20):
    r = requests.get(
        f"{BINANCE}/api/v3/depth",
        params={"symbol": SYMBOL, "limit": limit},
        timeout=8,
    )
    r.raise_for_status()
    d = r.json()
    bids = [(float(p), float(q)) for p, q in d["bids"]]
    asks = [(float(p), float(q)) for p, q in d["asks"]]
    return bids, asks

@st.cache_data(ttl=4, show_spinner=False)
def recent_trades(limit=1000):
    r = requests.get(
        f"{BINANCE}/api/v3/aggTrades",
        params={"symbol": SYMBOL, "limit": limit},
        timeout=8,
    )
    r.raise_for_status()
    return r.json()

# ---------- Calculations ----------
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

def qnt(x):
    return f"{x:,.3f}"

def pct(x):
    return f"{x:+.2f}%"

def trade_flow(trades):
    buy_taker = 0.0
    sell_taker = 0.0
    buy_usd = 0.0
    sell_usd = 0.0
    rows = []

    for t in trades:
        price = float(t["p"])
        qty = float(t["q"])
        # Binance: m=true means buyer is market maker,
        # therefore the aggressive/taker side is the seller.
        if bool(t["m"]):
            sell_taker += qty
            sell_usd += price * qty
            side = "SELL"
        else:
            buy_taker += qty
            buy_usd += price * qty
            side = "BUY"
        rows.append((t["T"], price, qty, side))

    total = buy_taker + sell_taker
    delta = buy_taker - sell_taker
    buy_pct = (buy_taker / total * 100) if total else 50
    return buy_taker, sell_taker, buy_usd, sell_usd, delta, buy_pct, rows

# ---------- Dashboard ----------
@st.fragment(run_every="5s")
def live_dashboard():
    try:
        t = ticker()
        df = add_indicators(klines("15m"))
        bids, asks = depth(20)
        trades = recent_trades(1000)

        last = float(t["lastPrice"])
        chg = float(t["priceChangePercent"])
        vol24 = float(t["quoteVolume"])
        high = float(t["highPrice"])
        low = float(t["lowPrice"])

        # Order book
        bid_qty = sum(q for _, q in bids)
        ask_qty = sum(q for _, q in asks)
        book_total = bid_qty + ask_qty
        imbalance = ((bid_qty - ask_qty) / book_total * 100) if book_total else 0

        best_bid = bids[0][0] if bids else np.nan
        best_ask = asks[0][0] if asks else np.nan
        spread = best_ask - best_bid if bids and asks else np.nan
        spread_pct = (spread / last * 100) if last else np.nan

        # Largest visible walls
        largest_bid = max(bids, key=lambda x: x[1]) if bids else (np.nan, 0)
        largest_ask = max(asks, key=lambda x: x[1]) if asks else (np.nan, 0)

        # Executed trade flow
        buy_taker, sell_taker, buy_usd, sell_usd, trade_delta, buy_pct, trade_rows = trade_flow(trades)
        total_taker = buy_taker + sell_taker
        sell_pct = 100 - buy_pct

        # Technical
        latest_delta = float(df["delta"].iloc[-1])
        cvd_change = float(df["cvd"].iloc[-1] - df["cvd"].iloc[-21])
        vol_ma = float(df["vol_ma20"].iloc[-1])
        volume_ratio = float(df["volume"].iloc[-1] / vol_ma) if vol_ma else 0

        # Signal engine
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
            reasons.append("15M CVD rising")
        else:
            score -= 1
            reasons.append("15M CVD falling")

        if imbalance > 5:
            score += 1
            reasons.append("More visible bid depth")
        elif imbalance < -5:
            score -= 1
            reasons.append("More visible ask depth")

        if buy_pct > 55:
            score += 1
            reasons.append("Aggressive buyers > sellers")
        elif buy_pct < 45:
            score -= 1
            reasons.append("Aggressive sellers > buyers")

        if volume_ratio > 1.5:
            reasons.append("15M volume spike")

        state = (
            "🟢 BULLISH WATCH" if score >= 2
            else "🔴 BEARISH WATCH" if score <= -2
            else "🟡 NEUTRAL"
        )

        # ---------- Header ----------
        st.title("QNT Live Terminal")
        st.caption("Mobile-friendly cloud dashboard • Binance QNT/USDT • Read-only")

        c = st.columns(4)
        c[0].metric("QNT / USDT", money(last))
        c[1].metric("24H", pct(chg))
        c[2].metric("24H Volume", f"${vol24/1e6:.2f}M")
        c[3].metric("Signal", state)

        c2 = st.columns(4)
        c2[0].metric("Best Bid", money(best_bid))
        c2[1].metric("Best Ask", money(best_ask))
        c2[2].metric("Spread", f"${spread:.4f} ({spread_pct:.3f}%)")
        c2[3].metric("Book Imbalance", f"{imbalance:+.1f}%")

        # ---------- Executed flow ----------
        st.subheader("⚡ Executed Trade Flow — Last 1,000 Aggregate Trades")
        f = st.columns(5)
        f[0].metric("🟢 Aggressive BUY", f"{buy_taker:,.2f} QNT")
        f[1].metric("🔴 Aggressive SELL", f"{sell_taker:,.2f} QNT")
        f[2].metric("Flow Delta", f"{trade_delta:+,.2f} QNT")
        f[3].metric("Buy %", f"{buy_pct:.1f}%")
        f[4].metric("Sell %", f"{sell_pct:.1f}%")

        flow_bar = pd.DataFrame(
            {"QNT": [buy_taker, sell_taker]},
            index=["🟢 Aggressive BUY", "🔴 Aggressive SELL"]
        )
        st.bar_chart(flow_bar, height=180)

        # ---------- Main grid ----------
        left, right = st.columns([1.65, 1])

        with left:
            st.subheader("15M Price / VWAP / EMA")
            chart = df.set_index("time")[["close","ema20","ema50","vwap"]].rename(
                columns={"close":"Price","ema20":"EMA20","ema50":"EMA50","vwap":"VWAP"}
            )
            st.line_chart(chart.tail(150), height=340)

            st.subheader("15M Volume")
            st.bar_chart(df.set_index("time")[["volume"]].tail(100), height=190)

            st.subheader("CVD")
            st.line_chart(df.set_index("time")[["cvd"]].tail(150), height=220)

        with right:
            st.subheader("📖 Order Book — BUY vs SELL")

            # Pair top levels for compact mobile display.
            n = max(len(bids), len(asks))
            book_rows = []
            for i in range(n):
                bp, bq = bids[i] if i < len(bids) else (np.nan, 0)
                ap, aq = asks[i] if i < len(asks) else (np.nan, 0)
                book_rows.append({
                    "BUY BID": bp,
                    "BUY QNT": bq,
                    "SELL ASK": ap,
                    "SELL QNT": aq,
                })

            book = pd.DataFrame(book_rows)

            def highlight_walls(row):
                styles = [""] * len(row)
                if row["BUY QNT"] == largest_bid[1] and largest_bid[1] > 0:
                    styles[1] = "font-weight: bold; background-color: rgba(0,180,80,0.18)"
                if row["SELL QNT"] == largest_ask[1] and largest_ask[1] > 0:
                    styles[3] = "font-weight: bold; background-color: rgba(220,50,50,0.18)"
                return styles

            st.dataframe(
                book.style
                    .format({
                        "BUY BID": "${:.2f}",
                        "BUY QNT": "{:.3f}",
                        "SELL ASK": "${:.2f}",
                        "SELL QNT": "{:.3f}",
                    })
                    .apply(highlight_walls, axis=1),
                use_container_width=True,
                hide_index=True,
                height=420,
            )

            w = st.columns(2)
            w[0].metric("Largest BUY wall", f"{largest_bid[1]:,.3f} QNT")
            w[1].metric("Largest SELL wall", f"{largest_ask[1]:,.3f} QNT")

            st.caption(
                f"BUY side total: {bid_qty:,.3f} QNT  •  "
                f"SELL side total: {ask_qty:,.3f} QNT"
            )

            st.subheader("Signal Engine")
            st.metric("Composite score", f"{score:+d}")
            for r in reasons:
                st.write("• " + r)

        # ---------- Interpretation ----------
        st.divider()
        st.subheader("🧭 How to read the flow")

        if buy_pct >= 55 and imbalance > 5:
            flow_state = "🟢 Buyers currently have the stronger visible + executed flow."
        elif sell_pct >= 55 and imbalance < -5:
            flow_state = "🔴 Sellers currently have the stronger visible + executed flow."
        else:
            flow_state = "🟡 Flow is mixed — wait for confirmation from price + CVD."

        st.info(flow_state)

        st.caption(
            "Important: Bid/ask orders are open intentions, not completed trades. "
            "Aggressive BUY means a taker bought from resting asks; aggressive SELL means "
            "a taker sold into resting bids. Walls can be cancelled or moved."
        )

        st.divider()
        st.caption(
            "LIVE • Read-only • Binance public market data • Auto-refresh ~5 seconds • "
            "Last update: " + datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        )

    except Exception as e:
        st.error("Live data connection failed. The app will retry automatically.")
        st.caption(str(e))

live_dashboard()
