"""CrewAI orchestrator: Quant Analyst -> (financial context) -> Strategist Analyst."""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from crewai import LLM, Agent, Crew, Process, Task

from app.config import get_settings
from app.tools import FirecrawlScrapeTool, FirecrawlSearchTool, YahooFinanceTool

logger = logging.getLogger("quant.crew")


@dataclass
class AnalysisResult:
    report_markdown: str
    quant_output: str
    strategy_output: str


def _llm() -> LLM:
    settings = get_settings()
    return LLM(model=settings.llm_model, temperature=settings.llm_temperature)


def build_crew() -> Crew:
    """Build a fresh crew per request (agents keep state, so don't share across threads)."""
    llm = _llm()

    quant_analyst = Agent(
        role="Senior Quantitative Analyst",
        goal=(
            "Produce a rigorous, numbers-first quantitative assessment of {ticker} covering "
            "performance, risk, technical momentum and valuation."
        ),
        backstory=(
            "You spent a decade on a systematic equity desk. You trust data over narratives, "
            "always cite exact figures, and flag when data is missing or unreliable."
        ),
        tools=[YahooFinanceTool()],
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    strategist = Agent(
        role="Chief Investment Strategist",
        goal=(
            "Combine the quantitative context with current news and market developments to "
            "deliver an actionable investment view on {ticker} for a {horizon} horizon."
        ),
        backstory=(
            "You are a former hedge-fund PM who turns quant signals and qualitative research "
            "into clear, balanced investment theses. You always cite sources with links."
        ),
        tools=[FirecrawlSearchTool(), FirecrawlScrapeTool()],
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    quant_task = Task(
        description=(
            "Call the yahoo_finance_quant_metrics tool for ticker '{ticker}' (period '1y'; also "
            "'5y' if longer-term context helps). Analyse the results and write a structured "
            "quantitative brief covering:\n"
            "1. Price performance (1m/3m/6m/1y returns, CAGR) and how it compares to the benchmark.\n"
            "2. Risk profile: volatility, Sharpe, Sortino, max/current drawdown, VaR/CVaR, beta.\n"
            "3. Technicals: trend vs SMA50/SMA200, RSI regime (overbought >70, oversold <30), MACD.\n"
            "4. Valuation and quality: P/E, forward P/E, margins, ROE, growth, leverage.\n"
            "5. The 3-5 most important quantitative signals, each labelled bullish/bearish/neutral.\n"
            "If the tool returns an error, report it clearly instead of inventing numbers."
        ),
        expected_output=(
            "A markdown brief with a 'Key Metrics' table (metric | value | interpretation) "
            "followed by short sections for performance, risk, technicals, valuation and key signals."
        ),
        agent=quant_analyst,
    )

    strategy_task = Task(
        description=(
            "Using the Quant Analyst's brief as your financial context, research the latest news, "
            "earnings, guidance, analyst actions, competitive and macro developments for '{ticker}' "
            "using firecrawl_web_search (run 2-4 targeted searches) and firecrawl_scrape_page for "
            "the most relevant articles. Then write the FINAL investment report in markdown with "
            "exactly these sections:\n"
            "## Executive Summary\n"
            "## Quantitative Snapshot  (a compact table of the most important metrics from the brief)\n"
            "## Market & News Context  (cite each source as a markdown link)\n"
            "## Bull Case\n"
            "## Bear Case\n"
            "## Key Risks & Catalysts\n"
            "## Strategy & Positioning  (entry considerations, position sizing given volatility, "
            "what would change the view — for a {horizon} horizon)\n"
            "## Rating  (one of Strong Buy / Buy / Hold / Sell / Strong Sell, with confidence "
            "Low/Medium/High and a one-paragraph justification)\n"
            "## Disclaimer  (not financial advice)\n"
            "Ground every claim in the quant brief or a cited source."
        ),
        expected_output="A complete, well-formatted markdown investment report with all required sections.",
        agent=strategist,
        context=[quant_task],  # passes financial context from the quant analyst
    )

    return Crew(
        agents=[quant_analyst, strategist],
        tasks=[quant_task, strategy_task],
        process=Process.sequential,
        verbose=True,
    )


def _strip_code_fence(text: str) -> str:
    """LLMs sometimes wrap the whole answer in ```markdown fences."""
    match = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*)\n```\s*", text, flags=re.DOTALL)
    return match.group(1) if match else text.strip()


def run_analysis(ticker: str, horizon: str, request_id: str) -> AnalysisResult:
    ticker = ticker.upper().strip()
    logger.info("Starting crew for %s (request_id=%s)", ticker, request_id)

    result = build_crew().kickoff(inputs={"ticker": ticker, "horizon": horizon})

    quant_output = _strip_code_fence(result.tasks_output[0].raw)
    strategy_output = _strip_code_fence(result.tasks_output[-1].raw)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    report = (
        f"# Investment Report: {ticker}\n\n"
        f"| | |\n|---|---|\n"
        f"| **Generated** | {generated_at} |\n"
        f"| **Horizon** | {horizon} |\n"
        f"| **Model** | {get_settings().llm_model} |\n"
        f"| **Request ID** | `{request_id}` |\n\n"
        f"{strategy_output}\n\n"
        f"---\n\n"
        f"# Appendix: Quantitative Analyst Brief\n\n"
        f"{quant_output}\n"
    )
    return AnalysisResult(report, quant_output, strategy_output)
