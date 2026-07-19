"""Tavily web-search skill for secondpass.

Mirrors the synapse-ai search helper pattern: load env eagerly, fail clearly
when TAVILY_API_KEY is missing, and expose a simple imperative search call.
"""

from __future__ import annotations

import os
from typing import TypedDict

from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()


class WebResult(TypedDict):
    title: str
    url: str
    snippet: str


def search_web(query: str, max_results: int = 3) -> list[WebResult]:
    """Search the web with Tavily and return normalized title/url/snippet hits."""
    api_key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "TAVILY_API_KEY not found in environment. Check your .env file."
        )

    client = TavilyClient(api_key=api_key)
    response = client.search(query=query, max_results=max_results)
    results: list[WebResult] = []
    for hit in response.get("results") or []:
        results.append(
            {
                "title": str(hit.get("title") or ""),
                "url": str(hit.get("url") or ""),
                "snippet": str(hit.get("content") or hit.get("snippet") or ""),
            }
        )
    return results
