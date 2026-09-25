"""Firecrawl tools for the Strategist agent: web search + page scraping (REST API)."""
import json
import logging
from typing import Type

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger("quant.tools.firecrawl")

MAX_CHARS_PER_PAGE = 6000  # keep LLM context under control
TIMEOUT_SECONDS = 60


def _post(endpoint: str, payload: dict) -> dict:
    settings = get_settings()
    if not settings.firecrawl_api_key:
        raise RuntimeError("FIRECRAWL_API_KEY is not configured.")
    resp = requests.post(
        f"{settings.firecrawl_base_url.rstrip('/')}/{endpoint}",
        headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
        json=payload,
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success", False):
        raise RuntimeError(f"Firecrawl error: {data.get('error', 'unknown error')}")
    return data


class FirecrawlSearchInput(BaseModel):
    query: str = Field(..., description="Search query, e.g. 'NVIDIA earnings guidance 2026'.")
    limit: int = Field(5, ge=1, le=10, description="Number of results to return.")


class FirecrawlSearchTool(BaseTool):
    name: str = "firecrawl_web_search"
    description: str = (
        "Searches the web via Firecrawl and returns the top results (title, URL, snippet and "
        "a markdown excerpt of each page). Use it to find recent news, earnings coverage, "
        "analyst commentary and industry developments."
    )
    args_schema: Type[BaseModel] = FirecrawlSearchInput

    def _run(self, query: str, limit: int = 5) -> str:
        try:
            data = _post(
                "search",
                {"query": query, "limit": limit, "scrapeOptions": {"formats": ["markdown"]}},
            )
            results = [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "description": item.get("description"),
                    "content": (item.get("markdown") or "")[: MAX_CHARS_PER_PAGE // 2],
                }
                for item in data.get("data", [])
            ]
            return json.dumps(results, indent=2)
        except Exception as exc:
            logger.exception("Firecrawl search failed: %s", query)
            return json.dumps({"error": str(exc), "query": query})


class FirecrawlScrapeInput(BaseModel):
    url: str = Field(..., description="Full URL of the page to scrape.")


class FirecrawlScrapeTool(BaseTool):
    name: str = "firecrawl_scrape_page"
    description: str = (
        "Scrapes a single web page via Firecrawl and returns its main content as markdown. "
        "Use it to read a specific article or investor-relations page in full."
    )
    args_schema: Type[BaseModel] = FirecrawlScrapeInput

    def _run(self, url: str) -> str:
        try:
            data = _post("scrape", {"url": url, "formats": ["markdown"], "onlyMainContent": True})
            page = data.get("data", {})
            return json.dumps(
                {
                    "url": url,
                    "title": (page.get("metadata") or {}).get("title"),
                    "content": (page.get("markdown") or "")[:MAX_CHARS_PER_PAGE],
                },
                indent=2,
            )
        except Exception as exc:
            logger.exception("Firecrawl scrape failed: %s", url)
            return json.dumps({"error": str(exc), "url": url})
