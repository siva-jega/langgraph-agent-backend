from __future__ import annotations

from typing import Any

import pytest

from app.modules.agents.graph.graph import Graph
from app.modules.channels.ai.engine import ChannelGraph


def test_agent_graph_avoids_process_local_cache_and_tool_timeout() -> None:
    graph = Graph().graph

    assert graph.cache is None
    assert graph.nodes["trim"].cache_policy is None
    assert graph.nodes["trim"].timeout is None
    assert graph.nodes["tools"].cache_policy is None
    assert graph.nodes["tools"].timeout is None
    assert graph.nodes["llm"].cache_policy is None
    assert graph.nodes["llm"].timeout is None
    assert graph.nodes["escalation_check"].cache_policy is None
    assert graph.nodes["escalation_check"].timeout is None


def test_channel_graph_avoids_process_local_cache_and_tool_timeout() -> None:
    graph = ChannelGraph().graph

    assert graph.cache is None
    assert graph.nodes["trim"].cache_policy is None
    assert graph.nodes["trim"].timeout is None
    assert graph.nodes["tools"].cache_policy is None
    assert graph.nodes["tools"].timeout is None
    assert graph.nodes["llm"].cache_policy is None
    assert graph.nodes["llm"].timeout is None


class _EmptyStreamGraph:
    def __init__(self) -> None:
        self.args: tuple[Any, ...] | None = None
        self.kwargs: dict[str, Any] | None = None

    async def astream(self, *_args: Any, **kwargs: Any):
        self.args = _args
        self.kwargs = kwargs
        if False:
            yield {}


@pytest.mark.asyncio
async def test_agent_graph_passes_persona_to_runtime_config(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _inactive_handoff(_chat_id: str) -> bool:
        return False

    monkeypatch.setattr(
        "app.modules.agents.graph.graph.check_active_handoff_api",
        _inactive_handoff,
    )

    runtime = Graph()
    fake_graph = _EmptyStreamGraph()
    runtime.graph = fake_graph

    async for _event in runtime.run(
        chat_id="chat-1",
        session_id="session-1",
        input_text="hello",
        additional_params={
            "config": {"persona": {"model": "gpt-4o-mini"}},
            "agent_id": "agent-1",
        },
    ):
        pass

    assert fake_graph.args is not None
    assert fake_graph.kwargs is not None
    runnable_config = fake_graph.args[1]
    assert runnable_config["configurable"]["persona"] == {"model": "gpt-4o-mini"}


@pytest.mark.asyncio
async def test_channel_graph_uses_exit_durability_at_stream_call() -> None:
    runtime = ChannelGraph()
    fake_graph = _EmptyStreamGraph()
    runtime.graph = fake_graph

    await runtime.run_non_streaming(
        "hello",
        {
            "chat_id": "chat-1",
            "session_id": "session-1",
            "channel_system_prompt": "You are helpful.",
        },
    )

    assert fake_graph.kwargs is not None
    assert fake_graph.kwargs["durability"] == "exit"
    assert fake_graph.kwargs["stream_mode"] == ["messages", "updates", "custom"]
    assert fake_graph.kwargs["version"] == "v2"
