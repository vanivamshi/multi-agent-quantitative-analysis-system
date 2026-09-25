"""Streamlit dashboard: triggers analyses via the FastAPI server and shows reports/history."""
import os
import time

import pandas as pd
import requests
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
POLL_INTERVAL_SECONDS = 5
MAX_WAIT_SECONDS = 20 * 60

st.set_page_config(page_title="Quant Multi-Agent Analyst", page_icon="📈", layout="wide")


def api_get(path: str, **params):
    resp = requests.get(f"{API_BASE_URL}{path}", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def render_report(data: dict) -> None:
    cols = st.columns(4)
    cols[0].metric("Ticker", data["ticker"])
    cols[1].metric("Status", data["status"])
    cols[2].metric("Duration", f"{(data.get('duration_seconds') or 0):.0f}s")
    cols[3].metric("Size", f"{(data.get('report_size_bytes') or 0) / 1024:.1f} KB")
    if data.get("report_markdown"):
        st.download_button(
            "⬇️ Download report (.md)",
            data=data["report_markdown"],
            file_name=f"{data['ticker']}_{str(data['request_id'])[:8]}.md",
            mime="text/markdown",
        )
        st.markdown(data["report_markdown"])
    if data.get("error_message"):
        st.error(data["error_message"])


# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ System")
    try:
        health = api_get("/health")
        st.success(f"API: {health['status']}")
        st.caption(f"Model: `{health['model']}`")
    except Exception as exc:
        st.error(f"API unreachable at {API_BASE_URL}")
        st.caption(str(exc))
    st.markdown(
        "**Pipeline**\n\n"
        "1. 🧮 Quant Analyst → Yahoo Finance\n"
        "2. 🧭 Strategist → Firecrawl\n"
        "3. ☁️ Report → Azure Blob\n"
        "4. 🗄️ Log → Azure PostgreSQL"
    )

st.title("📈 Multi-Agent Quantitative Analysis")
tab_new, tab_history = st.tabs(["New Analysis", "Report History"])

# ---------- New analysis ----------
with tab_new:
    with st.form("analyze"):
        c1, c2 = st.columns([1, 2])
        ticker = c1.text_input("Ticker symbol", value="AAPL", max_chars=20).strip().upper()
        horizon = c2.selectbox(
            "Investment horizon",
            ["short-term (1-3 months)", "medium-term (6-12 months)", "long-term (3+ years)"],
            index=1,
        )
        submitted = st.form_submit_button("🚀 Run analysis", type="primary")

    if submitted and ticker:
        try:
            resp = requests.post(
                f"{API_BASE_URL}/analyze", json={"ticker": ticker, "horizon": horizon}, timeout=30
            )
            resp.raise_for_status()
            job = resp.json()
        except requests.HTTPError:
            st.error(f"Request rejected: {resp.text}")
            st.stop()
        except Exception as exc:
            st.error(f"Could not reach API: {exc}")
            st.stop()

        st.session_state["last_request_id"] = job["request_id"]
        started = time.time()
        with st.status(f"Agents analysing {ticker}…", expanded=True) as status_box:
            st.write(f"Request ID: `{job['request_id']}`")
            data = None
            while time.time() - started < MAX_WAIT_SECONDS:
                data = api_get(f"/analyze/{job['request_id']}", include_report=True)
                elapsed = int(time.time() - started)
                status_box.update(label=f"{data['status']} — {elapsed}s elapsed")
                if data["status"] in ("COMPLETED", "FAILED"):
                    break
                time.sleep(POLL_INTERVAL_SECONDS)
            state = "complete" if data and data["status"] == "COMPLETED" else "error"
            status_box.update(label=f"{data['status'] if data else 'TIMEOUT'} for {ticker}", state=state)
        if data:
            render_report(data)

# ---------- History ----------
with tab_history:
    c1, c2 = st.columns([1, 3])
    filter_ticker = c1.text_input("Filter by ticker", value="").strip().upper()
    if c2.button("🔄 Refresh"):
        st.rerun()
    try:
        rows = api_get("/reports", limit=200, **({"ticker": filter_ticker} if filter_ticker else {}))
    except Exception as exc:
        st.error(f"Could not load history: {exc}")
        rows = []

    if rows:
        df = pd.DataFrame(rows)
        df["created_at"] = pd.to_datetime(df["created_at"])
        m1, m2, m3 = st.columns(3)
        m1.metric("Total runs", len(df))
        m2.metric("Success rate", f"{(df['status'] == 'COMPLETED').mean() * 100:.0f}%")
        avg_duration = df["duration_seconds"].dropna().mean()
        m3.metric("Avg duration", f"{avg_duration:.0f}s" if pd.notna(avg_duration) else "—")
        st.dataframe(
            df[["created_at", "ticker", "horizon", "status", "duration_seconds", "llm_model", "request_id"]],
            use_container_width=True,
            hide_index=True,
        )
        completed = df[df["status"] == "COMPLETED"]
        if not completed.empty:
            choice = st.selectbox(
                "Open report",
                completed["request_id"].astype(str),
                format_func=lambda rid: (
                    f"{completed.loc[completed['request_id'].astype(str) == rid, 'ticker'].iloc[0]} — {rid[:8]}"
                ),
            )
            if choice:
                render_report(api_get(f"/analyze/{choice}", include_report=True))
    else:
        st.info("No reports yet. Run an analysis from the first tab.")
