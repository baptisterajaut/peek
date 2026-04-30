"""Chat orchestrator tests using a fake backend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from peek.backend import StreamDelta
from peek.chat import Chat, ONBOARDING_BLOCK, assemble_system_prompt
from peek.config import Config
from peek.memory.store import MemoryStore


@dataclass
class FakeBackend:
    """Replays a list of pre-canned streams in order."""

    streams: list[list[StreamDelta]] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def add_stream(self, deltas: list[StreamDelta]) -> None:
        self.streams.append(deltas)

    async def _replay(self, deltas: list[StreamDelta]) -> AsyncIterator[StreamDelta]:
        for d in deltas:
            yield d

    def chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        deltas = self.streams.pop(0)
        return self._replay(deltas)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.personalities_dir = tmp_path / "personalities"
    cfg.memory_dir = tmp_path / "memory"
    cfg.model = "fake-model"
    return cfg


@pytest.fixture
def store(cfg: Config) -> MemoryStore:
    return MemoryStore(cfg.memory_dir)


def test_assemble_uses_onboarding_when_empty():
    sys = assemble_system_prompt("You are peek.", "")
    assert ONBOARDING_BLOCK in sys
    # Onboarding must come BEFORE personality so the model treats it as
    # an override, not as content under # Memory.
    assert sys.index(ONBOARDING_BLOCK) < sys.index("You are peek.")


def test_assemble_uses_memory_when_present():
    sys = assemble_system_prompt("You are peek.", "- [Role](user_role.md) — engineer")
    assert "Role" in sys
    assert ONBOARDING_BLOCK not in sys


async def test_simple_completion_yields_done(cfg, store):
    backend = FakeBackend()
    backend.add_stream([
        StreamDelta(content="Hello "),
        StreamDelta(content="world", finish_reason="stop"),
    ])
    chat = Chat.create(cfg, store, backend=backend)

    events = [e async for e in chat.send("hi")]
    assert ("delta", "Hello ") in events
    assert ("delta", "world") in events
    assert any(e[0] == "done" and "Hello world" in e[1] for e in events)
    # User + assistant appended
    assert chat.messages[-2]["role"] == "user"
    assert chat.messages[-1]["role"] == "assistant"
    assert chat.messages[-1]["content"] == "Hello world"


async def test_tool_call_loop_dispatches(cfg, store):
    backend = FakeBackend()
    # Turn 1: model calls note_for_later
    backend.add_stream([
        StreamDelta(
            tool_calls=[{
                "id": "call_1", "type": "function",
                "function": {"name": "note_for_later",
                             "arguments": '{"content": "user is brief"}'},
            }],
            finish_reason="tool_calls",
        ),
    ])
    # Turn 2: model emits final answer
    backend.add_stream([
        StreamDelta(content="ok noted.", finish_reason="stop"),
    ])

    chat = Chat.create(cfg, store, backend=backend)
    events = [e async for e in chat.send("just be brief")]

    kinds = [e[0] for e in events]
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert any(e[0] == "done" and e[1] == "ok noted." for e in events)
    assert chat.scratch == ["user is brief"]


async def test_reasoning_events_emitted(cfg, store):
    backend = FakeBackend()
    backend.add_stream([
        StreamDelta(reasoning="thinking..."),
        StreamDelta(content="42", finish_reason="stop"),
    ])
    chat = Chat.create(cfg, store, backend=backend)
    events = [e async for e in chat.send("q")]
    assert ("reasoning", "thinking...") in events
    assert ("delta", "42") in events


async def test_refresh_system_prompt_picks_up_new_memory(cfg, store):
    backend = FakeBackend()
    chat = Chat.create(cfg, store, backend=backend)
    # Initial: empty memory → onboarding present
    assert ONBOARDING_BLOCK in chat.messages[0]["content"]

    store.write_entry("Role", "engineer at MyU", "user", "Senior engineer.")
    chat.refresh_system_prompt()
    assert "Role" in chat.messages[0]["content"]
    assert ONBOARDING_BLOCK not in chat.messages[0]["content"]
