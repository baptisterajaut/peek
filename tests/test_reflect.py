"""Reflect/flush tests with a fake backend that returns canned JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from peek.config import Config
from peek.memory.reflect import reflect
from peek.memory.store import MemoryStore


@dataclass
class FakeBackend:
    response: str = "{\"ops\": []}"
    calls: list[dict] = field(default_factory=list)

    async def chat_once(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.response


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.memory_dir = tmp_path / "memory"
    cfg.model = "fake"
    return cfg


@pytest.fixture
def store(cfg: Config) -> MemoryStore:
    return MemoryStore(cfg.memory_dir)


def messages_with_one_user() -> list[dict]:
    return [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "tu glaze trop"},
        {"role": "assistant", "content": "noted"},
    ]


async def test_skips_when_scratch_empty(cfg, store):
    """Reflect now requires at least one note in scratch to fire."""
    backend = FakeBackend()
    result = await reflect(cfg, store, messages=messages_with_one_user(),
                           scratch=[], backend=backend)
    assert result.applied == []
    assert backend.calls == []  # short-circuited, never called


async def test_add_op_creates_entry(cfg, store):
    backend = FakeBackend(response='{"ops": [{"action": "add", "type": "feedback", '
                                   '"name": "No glazing", "description": "user dislikes praise", '
                                   '"body": "**Why:** style.\\n**How to apply:** keep responses dry"}]}')
    result = await reflect(cfg, store, messages=messages_with_one_user(),
                           scratch=["user said tu glaze trop"], backend=backend)
    # all reflect tests below this point pass scratch with at least one note
    # so the short-circuit (empty scratch → no call) doesn't fire.
    assert result.errors == []
    assert len(result.applied) == 1
    entries = store.list_entries()
    assert len(entries) == 1
    assert entries[0].name == "No glazing"
    assert entries[0].type == "feedback"


async def test_update_op_modifies_existing(cfg, store):
    e = store.write_entry("Role", "engineer", "user", "Senior.")
    backend = FakeBackend(response=(
        '{"ops": [{"action": "update", "filename": "' + e.filename + '", '
        '"type": "user", "name": "Role", "description": "senior engineer at MyU", '
        '"body": "Senior at MyUnisoft."}]}'
    ))
    result = await reflect(cfg, store, messages=messages_with_one_user(),
                           scratch=["a note"], backend=backend)
    assert result.errors == []
    loaded = store.read_entry(e.filename)
    assert loaded is not None
    assert "MyUnisoft" in loaded.body


async def test_delete_op_removes_entry(cfg, store):
    e = store.write_entry("Stale", "old", "project", "body")
    backend = FakeBackend(response=f'{{"ops": [{{"action": "delete", "filename": "{e.filename}"}}]}}')
    result = await reflect(cfg, store, messages=messages_with_one_user(),
                           scratch=["a note"], backend=backend)
    assert result.errors == []
    assert store.read_entry(e.filename) is None


async def test_invalid_json_recorded_as_error(cfg, store):
    backend = FakeBackend(response="not json at all")
    result = await reflect(cfg, store, messages=messages_with_one_user(),
                           scratch=["a note"], backend=backend)
    assert result.applied == []
    assert any("parse" in e.lower() for e in result.errors)


async def test_strips_code_fences(cfg, store):
    backend = FakeBackend(response='```json\n{"ops": []}\n```')
    result = await reflect(cfg, store, messages=messages_with_one_user(),
                           scratch=["a note"], backend=backend)
    assert result.errors == []
    assert result.applied == []


async def test_validates_op_fields(cfg, store):
    # Missing required field 'body'
    backend = FakeBackend(response='{"ops": [{"action": "add", "type": "feedback", '
                                   '"name": "X", "description": "y"}]}')
    result = await reflect(cfg, store, messages=messages_with_one_user(),
                           scratch=["a note"], backend=backend)
    assert result.applied == []
    assert any("missing body" in e for e in result.errors)


async def test_invalid_type_rejected(cfg, store):
    backend = FakeBackend(response='{"ops": [{"action": "add", "type": "garbage", '
                                   '"name": "X", "description": "y", "body": "z"}]}')
    result = await reflect(cfg, store, messages=messages_with_one_user(),
                           scratch=["a note"], backend=backend)
    assert result.applied == []
    assert any("invalid type" in e for e in result.errors)
