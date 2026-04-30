"""forget_memory: permanently delete a memory entry.

Used when the user explicitly asks the model to forget something, or when
the model has confirmed an entry is wrong/outdated and wants to clean up
without waiting for the post-conversation reflect step.

The system prompt seen by the rest of THIS turn still lists the deleted
entry's one-liner (the index isn't refreshed mid-turn). That's fine —
the next session gets a clean view.
"""

from __future__ import annotations

from peek.tools import Tool, ToolContext, register


SCHEMA = {
    "name": "forget_memory",
    "description": (
        "Permanently delete a memory entry by filename. Use when the user "
        "explicitly asks you to forget something they previously told you, "
        "or when you've confirmed an entry is wrong / outdated and should be "
        "removed now rather than at session end. Filenames are listed in the "
        "MEMORY.md index in your system prompt."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Basename of the entry to delete, e.g. 'feedback_no_glaze.md'.",
            },
        },
        "required": ["filename"],
    },
}


async def run(ctx: ToolContext, filename: str) -> str:
    if ctx.store.delete_entry(filename):
        return f"forgotten: {filename}"
    return f"error: no memory entry named {filename!r}"


register(Tool(name=SCHEMA["name"], schema=SCHEMA, fn=run))
