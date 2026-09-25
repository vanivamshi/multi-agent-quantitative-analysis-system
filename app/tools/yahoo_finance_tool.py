"""Yahoo Finance tool: pulls price history + fundamentals and computes quant metrics."""
import json
import logging
import math
from typing import Any, Type

import numpy as np
import pandas as pd
import yfinance as yf
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger("quant.tools.yahoo")

TRADING_DAYS = 252

FUNDAMENTAL_KEYS = [
    "longName", "sector", "industry", "country", "currency", "marketCap",
    "enterpriseValue", "trailingPE", "forwardPE", "pegRatio", "priceToBook",
    "priceToSalesTrailing12Months", "enterpriseToEbitda", "profitMargins",
    "operatingMargins", "grossMargins", "returnOnEquity", "returnOnAssets",
    "revenueGrowth", "earningsGrowth", "debtToEquity", "currentRatio",
    "freeCashflow", "dividendYield", "payoutRatio", "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow", "targetMeanPrice", "recommendationKey",
    "numberOfAnalystOpinions",
]


def _r(value: Any, digits: int = 4) -> Any:
    """Round floats and turn NaN/inf into None so the output is valid JSON."""
    if value is None:
        return None
    if isinstance(value, (float, np.floating)):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(float(value), digits)
    if isinstance(value, np.integer):
        return int(value)
    return value


def _naive_daily_index(series: pd.Series) -> pd.Series:
    idx = series.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    series = series.copy()
    series.index = idx.normalize()
    return series


def _trailing_return(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        # A '1y' window often holds 250-251 sessions; accept up to 5% shortfall.
        if len(close) - 1 < days * 0.95:
            return None
        days = len(close) - 1
    return close.iloc[-1] / close.iloc[-days - 1] - 1


def _rsi(close: pd.Series, window: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    rs = gain / loss
    return float((100 - 100 / (1 + rs)).iloc[-1])


def compute_quant_metrics(ticker: str, period: str = "1y") -> dict:
    settings = get_settings()
    rf = settings.risk_free_rate
    ticker = ticker.upper().strip()

    tk = yf.Ticker(ticker)
    hist = tk.history(period=period, auto_adjust=True)
    if hist.empty:
        raise ValueError(f"No price history returned for '{ticker}'. Check the symbol.")

    close = hist["Close"].dropna()
    rets = close.pct_change().dropna()

    # Risk / return
    ann_vol = rets.std() * math.sqrt(TRADING_DAYS)
    cagr = (close.iloc[-1] / close.iloc[0]) ** (TRADING_DAYS / max(len(rets), 1)) - 1
    ann_mean = rets.mean() * TRADING_DAYS
    sharpe = (ann_mean - rf) / ann_vol if ann_vol else None
    downside_vol = rets[rets < 0].std() * math.sqrt(TRADING_DAYS)
    sortino = (ann_mean - rf) / downside_vol if downside_vol else None
    drawdown = close / close.cummax() - 1
    max_dd = drawdown.min()
    var_95 = np.percentile(rets, 5)
    cvar_95 = rets[rets <= var_95].mean()

    # Beta / correlation vs benchmark
    beta = corr = None
    try:
        bench = yf.Ticker(settings.benchmark_ticker).history(period=period, auto_adjust=True)["Close"]
        bench_rets = _naive_daily_index(bench.pct_change().dropna())
        aligned = pd.concat([_naive_daily_index(rets), bench_rets], axis=1, join="inner").dropna()
        if len(aligned) > 20:
            cov = np.cov(aligned.iloc[:, 0], aligned.iloc[:, 1])
            beta = cov[0, 1] / cov[1, 1]
            corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    except Exception as exc:  # benchmark is best-effort
        logger.warning("Benchmark fetch failed: %s", exc)

    # Technicals
    sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()

    # Fundamentals (Yahoo's info endpoint can be flaky)
    fundamentals: dict = {}
    try:
        info = tk.info or {}
        fundamentals = {k: _r(info.get(k)) for k in FUNDAMENTAL_KEYS if info.get(k) is not None}
    except Exception as exc:
        logger.warning("Fundamentals fetch failed for %s: %s", ticker, exc)

    last = close.iloc[-1]
    return {
        "ticker": ticker,
        "as_of": str(close.index[-1].date()),
        "period": period,
        "observations": len(close),
        "price": {
            "last_close": _r(last, 2),
            "period_high": _r(close.max(), 2),
            "period_low": _r(close.min(), 2),
            "avg_daily_volume": _r(hist["Volume"].tail(63).mean(), 0),
        },
        "returns": {
            "1m": _r(_trailing_return(close, 21)),
            "3m": _r(_trailing_return(close, 63)),
            "6m": _r(_trailing_return(close, 126)),
            "1y": _r(_trailing_return(close, 252)),
            "period_total": _r(close.iloc[-1] / close.iloc[0] - 1),
            "cagr": _r(cagr),
        },
        "risk": {
            "annualized_volatility": _r(ann_vol),
            "sharpe_ratio": _r(sharpe, 3),
            "sortino_ratio": _r(sortino, 3),
            "max_drawdown": _r(max_dd),
            "current_drawdown": _r(drawdown.iloc[-1]),
            "daily_var_95": _r(var_95),
            "daily_cvar_95": _r(cvar_95),
            "beta_vs_benchmark": _r(beta, 3),
            "correlation_vs_benchmark": _r(corr, 3),
            "benchmark": settings.benchmark_ticker,
            "risk_free_rate": rf,
        },
        "technicals": {
            "sma_50": _r(sma50, 2),
            "sma_200": _r(sma200, 2),
            "price_vs_sma50": _r(last / sma50 - 1) if sma50 else None,
            "price_vs_sma200": _r(last / sma200 - 1) if sma200 else None,
            "golden_cross": bool(sma50 > sma200) if sma50 and sma200 else None,
            "rsi_14": _r(_rsi(close), 2),
            "macd": _r(macd.iloc[-1]),
            "macd_signal": _r(macd_signal.iloc[-1]),
            "macd_histogram": _r(macd.iloc[-1] - macd_signal.iloc[-1]),
        },
        "fundamentals": fundamentals,
    }


class YahooFinanceInput(BaseModel):
    ticker: str = Field(..., description="Stock ticker symbol, e.g. 'AAPL', 'MSFT', 'RELIANCE.NS'.")
    period: str = Field("1y", description="History window: '6mo', '1y', '2y' or '5y'.")


class YahooFinanceTool(BaseTool):
    name: str = "yahoo_finance_quant_metrics"
    description: str = (
        "Fetches price history and fundamentals from Yahoo Finance for a ticker and returns "
        "computed quantitative metrics as JSON: trailing returns, CAGR, volatility, Sharpe, "
        "Sortino, max drawdown, VaR/CVaR, beta vs S&P 500, SMA50/200, RSI, MACD and valuation ratios."
    )
    args_schema: Type[BaseModel] = YahooFinanceInput

    def _run(self, ticker: str, period: str = "1y") -> str:
        try:
            return json.dumps(compute_quant_metrics(ticker, period), indent=2, default=str)
        except Exception as exc:
            logger.exception("Yahoo Finance tool failed for %s", ticker)
            return json.dumps({"error": str(exc), "ticker": ticker})
