"""read_memory_file: load a memory entry's full body by filename.

The LLM sees the MEMORY.md index in the system prompt (one-liner per entry).
When a one-liner indicates a fuller note is relevant, it can call this tool
to fetch the full body without bloating every prompt.
"""

from __future__ import annotations

from peek.tools import Tool, ToolContext, register


SCHEMA = {
    "name": "read_memory_file",
    "description": (
        "Read the full body of a memory entry by filename. The MEMORY.md "
        "index in your system prompt lists every available filename. Use only "
        "when an entry's one-line description suggests it contains relevant "
        "detail you don't already have."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Basename of the entry, e.g. 'feedback_no_glaze.md'.",
            },
        },
        "required": ["filename"],
    },
}


async def run(ctx: ToolContext, filename: str) -> str:
    entry = ctx.store.read_entry(filename)
    if entry is None:
        return f"error: no memory entry named {filename!r}"
    return (
        f"# {entry.name} ({entry.type})\n"
        f"_{entry.description}_\n\n"
        f"{entry.body}"
    )


register(Tool(name=SCHEMA["name"], schema=SCHEMA, fn=run))
