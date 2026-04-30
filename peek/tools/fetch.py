"""fetch: GET a URL and return text content (HTML stripped).

Stdlib HTML parsing — no bs4 dep. Crude but enough for "quote me the gist".
"""

from __future__ import annotations

from html.parser import HTMLParser

import httpx

from peek.tools import Tool, ToolContext, register


SCHEMA = {
    "name": "fetch",
    "description": (
        "Fetch a URL and return its text content with HTML stripped. Use to "
        "read a specific page found via websearch or known a priori. Result "
        "is truncated to max_chars (default 4000)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute URL to fetch."},
            "max_chars": {
                "type": "integer",
                "description": "Truncate body. Default 4000, max 16000.",
                "default": 4000,
            },
        },
        "required": ["url"],
    },
}


_SKIP_TAGS = {"script", "style", "noscript", "head", "nav", "aside", "footer", "form"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ARG002
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in {"p", "br", "li", "div", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        # Collapse runs of whitespace per line, drop empty lines.
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


async def run(ctx: ToolContext, url: str, max_chars: int = 4000) -> str:
    max_chars = max(200, min(int(max_chars), 16000))
    async with httpx.AsyncClient(
        timeout=20.0, follow_redirects=True,
        headers={"User-Agent": "peek/0.1"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "").lower()
        body = resp.text

    if "html" in ctype or body.lstrip().startswith("<"):
        parser = _TextExtractor()
        parser.feed(body)
        text = parser.text()
    else:
        text = body

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[truncated, {len(text) - max_chars} more chars]"
    return text or "(empty body)"


register(Tool(name=SCHEMA["name"], schema=SCHEMA, fn=run))
