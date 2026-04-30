"""Tiny markdown → Qt-RichText HTML converter.

Handles only what we need for chat bubbles:
- # / ## / ### headings (line-prefix)
- **bold**, *italic* / _italic_
- `inline code`
- ``` fenced code blocks ``` (multi-line, optional language hint, ignored)
- > blockquote (line-prefix; consecutive lines fold into one block)

Streaming-safe: partial / unclosed markers fall through as raw text and
auto-fix on the next render. Tables are NOT supported.
"""

from __future__ import annotations

import html
import re

# Patterns running on already-escaped text (so > is &gt;).
_BOLD = re.compile(r"\*\*([^*\n]+?)\*\*")
_ITALIC_STAR = re.compile(r"(?<![*\w])\*([^*\n]+?)\*(?![*\w])")
_ITALIC_UNDER = re.compile(r"(?<![_\w])_([^_\n]+?)_(?![_\w])")
_INLINE_CODE = re.compile(r"`([^`\n]+?)`")
_QUOTE_LINE = re.compile(r"^&gt; ?(.*)$")
_HEADING = re.compile(r"^(#{1,3})\s+(.+)$")

# Fenced code block — matched BEFORE html.escape, so we escape the
# captured content separately when restoring. The opening fence allows an
# optional inline language hint (```python\n…), which the regex consumes
# but does not capture. Models that emit the lang on its OWN line (\`\`\`\n
# python\ncode\n\`\`\`) are handled by `_strip_lang_hint` below.
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_LANG_LINE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+#._-]*$")


def _strip_lang_hint(content: str) -> str:
    """Drop a leading bare-identifier line (e.g. 'python').

    Only fires if the first line is a short single token of language-name
    shape AND there is at least one more line after it (so we don't eat
    the only line of code).
    """
    if "\n" not in content:
        return content
    first, rest = content.split("\n", 1)
    if rest and _LANG_LINE.match(first.strip()) and len(first.strip()) <= 20:
        return rest
    return content

_INLINE_CODE_STYLE = (
    "font-family: monospace; "
    "background-color: #0d0d0d; "
    "color: #f6c177; "
    "padding: 1px 4px; "
    "border-radius: 3px;"
)
_BLOCK_CODE_STYLE = (
    "background-color: #0d0d0d; "
    "color: #f6c177; "
    "padding: 8px 10px; "
    "border-radius: 6px; "
    "font-family: monospace;"
)
_QUOTE_STYLE = (
    "border-left: 3px solid #555; "
    "margin: 4px 0; "
    "padding-left: 8px; "
    "color: #999;"
)
_HEADING_STYLE = {
    1: "font-size: 16px; font-weight: bold; margin: 6px 0 3px 0;",
    2: "font-size: 14px; font-weight: bold; margin: 5px 0 3px 0;",
    3: "font-size: 13px; font-weight: bold; margin: 4px 0 2px 0;",
}


def _apply_inline_emphasis(s: str) -> str:
    s = _BOLD.sub(r"<b>\1</b>", s)
    s = _ITALIC_STAR.sub(r"<i>\1</i>", s)
    s = _ITALIC_UNDER.sub(r"<i>\1</i>", s)
    return s


def to_html(text: str) -> str:
    """Convert markdown-flavored plaintext to Qt-RichText HTML."""
    if not text:
        return ""

    # 1. Stash fenced code blocks BEFORE escaping; the captured content keeps
    #    its raw form (including newlines and special chars) until restore.
    block_chunks: list[str] = []

    def _stash_block(m: re.Match) -> str:
        block_chunks.append(_strip_lang_hint(m.group(1)))
        return f"\x00BLOCK{len(block_chunks) - 1}\x00"

    text = _FENCE.sub(_stash_block, text)

    # 2. Escape HTML for the rest.
    text = html.escape(text)

    # 3. Stash inline code so emphasis markers inside don't fire.
    code_chunks: list[str] = []

    def _stash_inline(m: re.Match) -> str:
        code_chunks.append(m.group(1))
        return f"\x00CODE{len(code_chunks) - 1}\x00"

    text = _INLINE_CODE.sub(_stash_inline, text)

    # 4. Walk lines: headings, blockquotes (folded), then inline emphasis.
    lines = text.split("\n")
    rebuilt: list[str] = []
    in_quote = False

    def _close_quote() -> None:
        nonlocal in_quote
        if in_quote:
            rebuilt.append("</blockquote>")
            in_quote = False

    for line in lines:
        h = _HEADING.match(line)
        if h:
            _close_quote()
            level = len(h.group(1))
            content = _apply_inline_emphasis(h.group(2))
            rebuilt.append(
                f'<h{level} style="{_HEADING_STYLE[level]}">{content}</h{level}>'
            )
            continue
        q = _QUOTE_LINE.match(line)
        if q:
            if not in_quote:
                rebuilt.append(f'<blockquote style="{_QUOTE_STYLE}">')
                in_quote = True
            rebuilt.append(_apply_inline_emphasis(q.group(1)) + "<br>")
            continue
        _close_quote()
        rebuilt.append(_apply_inline_emphasis(line))
    _close_quote()
    text = "\n".join(rebuilt)

    # 5. Restore inline code spans.
    def _unstash_inline(m: re.Match) -> str:
        return (
            f'<code style="{_INLINE_CODE_STYLE}">'
            f"{code_chunks[int(m.group(1))]}</code>"
        )

    text = re.sub(r"\x00CODE(\d+)\x00", _unstash_inline, text)

    # 6. Convert leftover newlines to <br> BEFORE restoring fenced blocks
    #    (so the literal newlines inside <pre> survive intact).
    text = text.replace("\n", "<br>")

    # 7. Restore fenced blocks. Escape their content now (skipped at step 2)
    #    and wrap in <pre>. Strip a trailing newline that the regex captured.
    def _unstash_block(m: re.Match) -> str:
        raw = block_chunks[int(m.group(1))].rstrip("\n")
        escaped = html.escape(raw)
        return f'<pre style="{_BLOCK_CODE_STYLE}">{escaped}</pre>'

    text = re.sub(r"\x00BLOCK(\d+)\x00", _unstash_block, text)

    return text
