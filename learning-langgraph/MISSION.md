# Mission: Learn This Project's LangGraph Agent

## Why
You want to understand how this backend uses LangGraph so you can read, debug, and modify the agent flow with confidence. The goal is practical: when a chat message enters the system, you should be able to follow where it goes, what decisions the graph makes, and where tools or persistence fit.

## Success looks like
- Trace a customer chat request from `/api/agents/stream` into the LangGraph graph.
- Explain what each graph node does in this project.
- Predict when the graph calls tools versus when it ends.
- Know which files to open when debugging streaming, tools, HITL, or checkpointing.

## Constraints
- Lessons should stay short and tied to this repository.
- Prefer concrete code paths over generic LangGraph theory.
- Use small retrieval-practice checks so learning sticks.

## Out of scope
- Building a new LangGraph agent from scratch.
- Deep frontend widget behavior.
- Full production deployment details.
