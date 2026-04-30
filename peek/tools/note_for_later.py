"""note_for_later: model drops a free-form note that the post-conversation
reflect step considers when deciding what to persist into memory.

This is the model's "I want to remember this for next time" signal.
"""

from __future__ import annotations

from peek.tools import Tool, ToolContext, register


SCHEMA = {
    "name": "note_for_later",
    "description": (
        "Drop a short note about something worth remembering between sessions: "
        "a user preference, a recurring fact about them, a project context, or "
        "an external resource pointer. The note is reviewed at session end and "
        "may be promoted to long-term memory. Use sparingly — only when the "
        "signal is clear and the information is non-obvious."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "The note. Lead with the fact or rule. If it stems from a "
                    "specific user statement, quote or paraphrase that statement."
                ),
            },
        },
        "required": ["content"],
    },
}


async def run(ctx: ToolContext, content: str) -> str:
    content = content.strip()
    if not content:
        return "error: empty note"
    ctx.scratch.append(content)
    return f"noted ({len(ctx.scratch)} pending)"


register(Tool(name=SCHEMA["name"], schema=SCHEMA, fn=run))
