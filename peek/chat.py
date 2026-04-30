"""Chat orchestrator — system prompt assembly, streaming, tool-call loop.

Yields typed events to the UI:

    ("reasoning", str)   incremental reasoning tokens (thinking mode)
    ("delta", str)       incremental assistant content
    ("tool_call", name, args)   tool about to run
    ("tool_result", name, result_text)  tool result (truncated for display)
    ("done", final_text)        end of an assistant turn
    ("error", message)          something blew up
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass

from peek.backend import Backend
from peek.config import Config
from peek.memory.store import MemoryStore
from peek.personality import load as load_personality
from peek.tools import ToolContext, all_schemas, dispatch

ONBOARDING_BLOCK = """\
[FIRST-CONTACT MODE — memory is empty]

You don't know who this user is yet. This is more important than any other \
behavioral guideline below: in EACH of your replies, weave in ONE short, \
natural question to learn about them — their role, what they work on, how \
they like to work, what they care about. Don't run a checklist of questions; \
ask one, then react to the answer. Be genuinely curious, not interview-y.

When you learn something stable about them, IMMEDIATELY call note_for_later \
with the fact. Be liberal with notes during this mode — the post-conversation \
reflect step decides what gets persisted.

This mode lifts as soon as memory has entries; the user-set tone in the \
personality below applies normally otherwise.\
"""


def assemble_system_prompt(personality_text: str, memory_index: str) -> str:
    if memory_index.strip():
        return (
            personality_text.strip()
            + "\n\n---\n\n# Memory\n\n"
            + memory_index.strip()
        )
    # Empty memory → put onboarding at the TOP as a behavioral override,
    # and keep the personality below for tone/style.
    return (
        ONBOARDING_BLOCK
        + "\n\n---\n\n"
        + personality_text.strip()
        + "\n\n---\n\n# Memory\n\n(empty — see FIRST-CONTACT MODE above)"
    )


@dataclass
class Chat:
    config: Config
    store: MemoryStore
    backend: Backend
    messages: list[dict]
    scratch: list[str]
    personality_text: str

    @classmethod
    def create(cls, config: Config, store: MemoryStore,
               backend: Backend | None = None) -> "Chat":
        backend = backend or Backend(
            host=config.host, verify_ssl=config.verify_ssl, api_key=config.api_key,
        )
        personality_text = load_personality(config.personalities_dir, config.personality)
        system = assemble_system_prompt(personality_text, store.assemble_for_prompt())
        return cls(
            config=config, store=store, backend=backend,
            messages=[{"role": "system", "content": system}],
            scratch=[],
            personality_text=personality_text,
        )

    def reset(self) -> None:
        """Clear conversation but keep system prompt."""
        self.messages = self.messages[:1]
        self.scratch.clear()

    def refresh_system_prompt(self) -> None:
        """Re-assemble system prompt from current memory state."""
        system = assemble_system_prompt(
            self.personality_text, self.store.assemble_for_prompt(),
        )
        self.messages[0] = {"role": "system", "content": system}

    @property
    def tool_ctx(self) -> ToolContext:
        return ToolContext(config=self.config, store=self.store, scratch=self.scratch)

    async def send(self, user_message: str) -> AsyncIterator[tuple]:
        """Send a user message, yield streaming events through to completion.

        Iterates the tool-call loop: model emits tool calls → we dispatch →
        append tool result messages → call again. Stops when finish_reason
        != 'tool_calls'. Hard cap on iterations as a safety net.
        """
        self.messages.append({"role": "user", "content": user_message})

        ctx = self.tool_ctx
        tools_schema = all_schemas()

        for _iteration in range(8):
            stream = self.backend.chat_stream(
                model=self.config.model,
                messages=self.messages,
                tools=tools_schema or None,
                temperature=self.config.temperature,
                thinking=self.config.thinking,
            )

            assistant_content = ""
            assistant_reasoning = ""
            tool_calls: list[dict] = []

            # aclosing() guarantees the underlying HTTP stream is released
            # even if we exit the loop early via cancellation or exception.
            try:
                async with aclosing(stream) as gen:
                    async for delta in gen:
                        if delta.reasoning:
                            assistant_reasoning += delta.reasoning
                            yield ("reasoning", delta.reasoning)
                        if delta.content:
                            assistant_content += delta.content
                            yield ("delta", delta.content)
                        if delta.tool_calls:
                            tool_calls = delta.tool_calls
                        if delta.finish_reason and delta.finish_reason != "tool_calls":
                            break
            except Exception as e:  # noqa: BLE001
                yield ("error", f"{type(e).__name__}: {e}")
                return

            if tool_calls:
                # Append the assistant turn carrying the tool_calls, then each
                # tool's result as a 'tool' message keyed by tool_call_id.
                self.messages.append({
                    "role": "assistant",
                    "content": assistant_content or None,
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    name = tc["function"]["name"]
                    args_json = tc["function"].get("arguments", "{}")
                    try:
                        args_preview = json.loads(args_json)
                    except json.JSONDecodeError:
                        args_preview = args_json
                    yield ("tool_call", name, args_preview)
                    result = await dispatch(ctx, name, args_json)
                    yield ("tool_result", name, result)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
                    # NOTE: tools that mutate memory (forget_memory) deliberately
                    # do NOT refresh the system prompt here. Mutating messages[0]
                    # invalidates the server's prefix KV-cache and forces a full
                    # re-process. The tool result already tells the model what
                    # changed; next session re-assembles the prompt cleanly.
                continue  # let the model react to tool results

            # No tool calls: this turn is final.
            self.messages.append({
                "role": "assistant", "content": assistant_content,
            })
            yield ("done", assistant_content)
            return

        yield ("error", "tool-call loop exceeded 8 iterations — bailing")
