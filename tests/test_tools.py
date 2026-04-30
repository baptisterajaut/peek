from pathlib import Path

import pytest

from peek.config import Config
from peek.memory.store import MemoryStore
from peek.tools import ToolContext, all_schemas, dispatch


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        config=Config(),
        store=MemoryStore(tmp_path / "memory"),
        scratch=[],
    )


def test_all_schemas_exposes_expected_tools():
    schemas = all_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {
        "websearch", "fetch", "note_for_later",
        "read_memory_file", "forget_memory",
    }
    for s in schemas:
        assert s["type"] == "function"
        assert "parameters" in s["function"]


async def test_forget_memory_deletes_existing(ctx):
    e = ctx.store.write_entry("Doomed", "to be deleted", "user", "body")
    out = await dispatch(ctx, "forget_memory", f'{{"filename": "{e.filename}"}}')
    assert "forgotten" in out
    assert ctx.store.read_entry(e.filename) is None


async def test_forget_memory_missing(ctx):
    out = await dispatch(ctx, "forget_memory", '{"filename": "nope.md"}')
    assert "error" in out


async def test_note_for_later_appends_to_scratch(ctx: ToolContext):
    out = await dispatch(ctx, "note_for_later", '{"content": "user prefers terse"}')
    assert "noted" in out
    assert ctx.scratch == ["user prefers terse"]


async def test_note_for_later_rejects_empty(ctx: ToolContext):
    out = await dispatch(ctx, "note_for_later", '{"content": "  "}')
    assert "error" in out
    assert ctx.scratch == []


async def test_read_memory_file_round_trip(ctx: ToolContext):
    e = ctx.store.write_entry("Role", "user is engineer", "user", "Senior.")
    out = await dispatch(ctx, "read_memory_file", f'{{"filename": "{e.filename}"}}')
    assert "Role" in out and "Senior." in out


async def test_read_memory_file_missing(ctx: ToolContext):
    out = await dispatch(ctx, "read_memory_file", '{"filename": "nope.md"}')
    assert "error" in out


async def test_unknown_tool(ctx: ToolContext):
    out = await dispatch(ctx, "doesnotexist", "{}")
    assert "unknown tool" in out


async def test_invalid_json_args(ctx: ToolContext):
    out = await dispatch(ctx, "note_for_later", "not json")
    assert "invalid JSON" in out


async def test_websearch_no_key_returns_helpful_error(ctx: ToolContext):
    # default config = tavily + empty key
    out = await dispatch(ctx, "websearch", '{"query": "anything"}')
    assert "Tavily" in out or "api_key" in out
