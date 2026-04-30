"""Reflect/flush — at session close, decide what to persist into long-term memory.

Inputs:
- The conversation transcript
- The scratch buffer (notes the model dropped via note_for_later)
- The current set of memory entries

Output: a list of operations applied (add/update/delete) plus any errors.

The model returns JSON. We parse strictly — if it produces garbage, we fail
the flush rather than corrupt memory.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from peek.backend import Backend
from peek.config import Config
from peek.memory.store import VALID_TYPES, Entry, MemoryStore

REFLECT_SYSTEM = """\
You are peek's memory curator. After a conversation, you decide what (if \
anything) is worth keeping in long-term memory across future sessions.

You output JSON only. No prose, no markdown, no fences. Schema:

{"ops": [
  {"action": "add", "type": "user|feedback|project|reference",
   "name": "Short title", "description": "One-line hook (<=150 chars)",
   "body": "Markdown body"},
  {"action": "update", "filename": "<existing filename>",
   "type": "...", "name": "...", "description": "...", "body": "..."},
  {"action": "delete", "filename": "<existing filename>"}
]}

If nothing is worth changing, return {"ops": []}.

Memory types:
- user:      stable facts about who the user is, their role, expertise, goals
- feedback:  rules about how to work with them ("don't glaze", "use TDD here").
             For feedback and project entries, structure body with:
                 <rule or fact>
                 **Why:** <reason — often a past incident or strong preference>
                 **How to apply:** <when this kicks in>
- project:   ongoing work, deadlines, decisions whose motivation isn't in code
- reference: pointers to external systems (URLs, dashboards, ticket projects)

Heuristics:
- Save when surprising or non-obvious. Skip what's already obvious from the
  conversation flow alone.
- Save user corrections AND validated approaches (if the user accepted a
  non-obvious choice without pushback, that's a signal too).
- Convert relative dates to absolute dates.
- Don't save: code patterns, file paths, project structure, ephemeral state,
  one-off debugging recipes.
- Prefer updating an existing entry over creating a duplicate. Use the
  EXISTING ENTRIES section to check for overlap before adding.
- Delete entries you can confirm from this conversation are wrong/outdated.
"""

REFLECT_USER_TEMPLATE = """\
EXISTING ENTRIES:
{existing}

NOTES THE MODEL DROPPED DURING THE CONVERSATION (note_for_later):
{notes}

CONVERSATION TRANSCRIPT:
{transcript}

Output JSON only.\
"""


@dataclass
class ReflectResult:
    applied: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw_response: str = ""


def _format_existing(entries: list[Entry]) -> str:
    if not entries:
        return "(none)"
    lines = []
    for e in entries:
        lines.append(f"- {e.filename} [{e.type}] {e.name} — {e.description}")
    return "\n".join(lines)


def _format_transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role")
        if role in ("system", "tool"):
            continue
        if role not in ("user", "assistant"):
            continue
        content = m.get("content") or ""
        if not content.strip():
            continue
        lines.append(f"[{role}] {content.strip()}")
    return "\n\n".join(lines) if lines else "(empty)"


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Models love to wrap in fences despite instructions.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last ditch: grab the largest {...} block.
        m = _JSON_BLOCK_RE.search(text)
        if not m:
            raise
        return json.loads(m.group(0))


def _validate_op(op: Any) -> tuple[dict, str | None]:
    if not isinstance(op, dict):
        return {}, "op is not an object"
    action = op.get("action")
    if action == "add":
        for k in ("type", "name", "description", "body"):
            if not op.get(k):
                return op, f"add missing {k}"
        if op["type"] not in VALID_TYPES:
            return op, f"invalid type {op['type']!r}"
        return op, None
    if action == "update":
        for k in ("filename", "type", "name", "description", "body"):
            if not op.get(k):
                return op, f"update missing {k}"
        if op["type"] not in VALID_TYPES:
            return op, f"invalid type {op['type']!r}"
        return op, None
    if action == "delete":
        if not op.get("filename"):
            return op, "delete missing filename"
        return op, None
    return op, f"unknown action {action!r}"


async def reflect(
    config: Config,
    store: MemoryStore,
    messages: list[dict],
    scratch: list[str],
    backend: Backend | None = None,
) -> ReflectResult:
    """Run the reflect pass and apply resulting memory ops."""
    # Short-circuit if the model didn't drop any notes during the conversation.
    # The reflect call is expensive (35B inference) and asking "did anything
    # interesting happen?" without a primed signal is rarely productive — if
    # the model didn't think it was worth note_for_later mid-conversation, it's
    # unlikely to find something now.
    if not scratch:
        return ReflectResult()

    backend = backend or Backend(
        host=config.host, verify_ssl=config.verify_ssl, api_key=config.api_key,
    )
    existing = store.list_entries()
    user_prompt = REFLECT_USER_TEMPLATE.format(
        existing=_format_existing(existing),
        notes="\n".join(f"- {n}" for n in scratch) if scratch else "(none)",
        transcript=_format_transcript(messages),
    )

    try:
        raw = await backend.chat_once(
            model=config.model,
            messages=[
                {"role": "system", "content": REFLECT_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            thinking=False,
        )
    except Exception as e:  # noqa: BLE001
        return ReflectResult(errors=[f"backend error: {e}"])

    result = ReflectResult(raw_response=raw)
    try:
        parsed = _extract_json(raw)
    except json.JSONDecodeError as e:
        result.errors.append(f"could not parse JSON from model: {e}")
        return result

    ops = parsed.get("ops", [])
    if not isinstance(ops, list):
        result.errors.append("'ops' is not a list")
        return result

    for op in ops:
        clean, err = _validate_op(op)
        if err:
            result.errors.append(err)
            continue
        try:
            if clean["action"] == "add":
                store.write_entry(
                    name=clean["name"], description=clean["description"],
                    type_=clean["type"], body=clean["body"],
                )
                result.applied.append(clean)
            elif clean["action"] == "update":
                store.write_entry(
                    name=clean["name"], description=clean["description"],
                    type_=clean["type"], body=clean["body"],
                    filename=clean["filename"],
                )
                result.applied.append(clean)
            elif clean["action"] == "delete":
                if not store.delete_entry(clean["filename"]):
                    result.errors.append(f"delete: no such file {clean['filename']!r}")
                else:
                    result.applied.append(clean)
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"applying {clean.get('action')}: {e}")

    return result
