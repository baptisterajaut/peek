"""websearch: generic web search across providers.

Provider is picked from ctx.config.search_provider:
  - tavily   (default; uses search_api_key)
  - brave    (uses search_api_key as X-Subscription-Token)
  - searxng  (uses searxng_url, no key needed)
"""

from __future__ import annotations

import httpx

from peek.tools import Tool, ToolContext, register


SCHEMA = {
    "name": "websearch",
    "description": (
        "Search the web for recent or external information. Returns up to k "
        "results with title, URL, and a short snippet. Use only when the "
        "answer needs information beyond what is already in memory or in the "
        "conversation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "k": {
                "type": "integer",
                "description": "Number of results to return. Default 5, max 10.",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}


async def _tavily(query: str, k: int, key: str) -> list[dict]:
    if not key:
        return [{"error": "no Tavily API key configured (set [search] api_key)"}]
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={"query": query, "max_results": k, "api_key": key},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "snippet": r.get("content", "")}
        for r in data.get("results", [])[:k]
    ]


async def _brave(query: str, k: int, key: str) -> list[dict]:
    if not key:
        return [{"error": "no Brave API key configured"}]
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": k},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "snippet": r.get("description", "")}
        for r in data.get("web", {}).get("results", [])[:k]
    ]


async def _searxng(query: str, k: int, base_url: str) -> list[dict]:
    if not base_url:
        return [{"error": "no SearXNG URL configured (set [search] searxng_url)"}]
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{base_url.rstrip('/')}/search",
            params={"q": query, "format": "json"},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "snippet": r.get("content", "")}
        for r in data.get("results", [])[:k]
    ]


def _format(results: list[dict]) -> str:
    if not results:
        return "no results"
    if "error" in results[0]:
        return results[0]["error"]
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        lines.append(f"    {r['url']}")
        snippet = r.get("snippet", "").strip()
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines)


async def run(ctx: ToolContext, query: str, k: int = 5) -> str:
    k = max(1, min(int(k), 10))
    provider = ctx.config.search_provider.lower()
    if provider == "tavily":
        results = await _tavily(query, k, ctx.config.search_api_key)
    elif provider == "brave":
        results = await _brave(query, k, ctx.config.search_api_key)
    elif provider == "searxng":
        results = await _searxng(query, k, ctx.config.searxng_url)
    else:
        return f"error: unknown search provider {provider!r}"
    return _format(results)


register(Tool(name=SCHEMA["name"], schema=SCHEMA, fn=run))
