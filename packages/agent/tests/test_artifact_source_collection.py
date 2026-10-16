from app.modules.copilot.graph.graph import Graph
from langchain_core.messages import ToolMessage


def test_process_chunk_appends_single_tool_artifact() -> None:
    graph = Graph.__new__(Graph)
    sources = []
    seen_tool_ids: set[str] = set()
    artifact = {"name": "Knowledge Base", "tool": "Search", "data": []}
    message = ToolMessage(
        content="tool content",
        tool_call_id="tool-call-1",
        artifact=artifact,
    )

    response, last_ai_message = graph._process_chunk(
        ("messages", (message,)),
        sources,
        seen_tool_ids,
        callbacks=[],
    )

    assert response is None
    assert last_ai_message is None
    assert sources == [artifact]
    assert seen_tool_ids == {"tool-call-1"}


def test_process_chunk_flattens_list_tool_artifacts() -> None:
    graph = Graph.__new__(Graph)
    sources = []
    seen_tool_ids: set[str] = set()
    artifact = [
        {"name": "Knowledge Base", "tool": "Search", "data": []},
        {"name": "Shopify", "tool": "Get Products", "data": []},
    ]
    message = ToolMessage(
        content="tool content",
        tool_call_id="tool-call-1",
        artifact=artifact,
    )

    response, last_ai_message = graph._process_chunk(
        ("messages", (message,)),
        sources,
        seen_tool_ids,
        callbacks=[],
    )

    assert response is None
    assert last_ai_message is None
    assert sources == artifact
    assert sources[0]["name"] == "Knowledge Base"
    assert sources[1]["name"] == "Shopify"
    assert seen_tool_ids == {"tool-call-1"}


def test_process_chunk_does_not_duplicate_seen_tool_artifact() -> None:
    graph = Graph.__new__(Graph)
    sources = [{"name": "Existing", "tool": "Already Collected", "data": []}]
    seen_tool_ids: set[str] = {"tool-call-1"}
    message = ToolMessage(
        content="tool content",
        tool_call_id="tool-call-1",
        artifact={"name": "Shopify", "tool": "Get Products", "data": []},
    )

    graph._process_chunk(
        ("messages", (message,)),
        sources,
        seen_tool_ids,
        callbacks=[],
    )

    assert sources == [{"name": "Existing", "tool": "Already Collected", "data": []}]
    assert seen_tool_ids == {"tool-call-1"}
