# LangGraph Project Resources

## Knowledge

- [Local: Agent Flow](../docs/agent-flow.md)
  Use for: this repository's intended LangGraph structure, HITL flow, checkpointing, and streaming behavior.
- [Local: Architecture](../docs/architecture.md)
  Use for: understanding how FastAPI, agent, copilot, tools, Postgres, Redis, and Milvus fit together.
- [Local: Agent Graph](../packages/agent/app/modules/agents/graph/graph.py)
  Use for: the compiled `StateGraph`, streaming loop, checkpointer use, and interrupt handling.
- [Local: Agent Nodes](../packages/agent/app/modules/agents/graph/nodes.py)
  Use for: `GraphState`, message trimming, LLM calls, dynamic tool binding, and `ToolNode`.
- [LangGraph `tools_condition` reference](https://reference.langchain.com/python/langgraph.prebuilt/tool_node/tools_condition)
  Use for: understanding the conditional route from the LLM node to tool execution or graph completion.
- [LangChain tools and `ToolNode` docs](https://docs.langchain.com/oss/python/langchain/tools)
  Use for: understanding why `ToolNode` is the standard executor for model-requested tool calls.
- [LangGraph interrupts docs](https://docs.langchain.com/oss/python/langgraph/interrupts)
  Use for: understanding pause/resume and human-in-the-loop behavior.

## Wisdom (Communities)

- [LangGraph GitHub Discussions](https://github.com/langchain-ai/langgraph/discussions)
  Use for: real-world design questions, strange checkpointing behavior, and agent architecture discussions.
