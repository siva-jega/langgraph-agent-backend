"""Test helpers for workflow_service — fake repositories used by unit and integration tests.

Lives inside the main package so the import path is always stable regardless
of which directory pytest is invoked from.
"""

from __future__ import annotations

from typing import Any


class FakeRunRepo:
    """In-memory RunRepository for unit tests."""

    def __init__(self, run: dict | None = None, preloaded_steps: list[dict] | None = None):
        self._run = run
        self._step_counter = 0
        self._nodes_created: list[str] = []
        self._step_id_to_node: dict[str, str] = {}
        self._node_to_step_id: dict[str, str] = {}
        self._step_updates: dict[str, list[dict]] = {}
        self.status_history: list[str] = []
        self._preloaded_steps: list[dict] = preloaded_steps or []
        for s in self._preloaded_steps:
            sid = str(s["id"])
            nid = s["nodeId"]
            self._step_id_to_node[sid] = nid
            self._node_to_step_id[nid] = sid
            self._step_updates[sid] = []

    async def get_by_id(self, run_id: str) -> dict | None:
        return self._run

    async def update_status(self, run_id: str, status: str, **kwargs: Any) -> None:
        self.status_history.append(status)

    async def create_run_step(self, run_id: str, node_id: str) -> dict:
        self._step_counter += 1
        step_id = f"step-{self._step_counter}"
        self._nodes_created.append(node_id)
        self._step_id_to_node[step_id] = node_id
        self._node_to_step_id[node_id] = step_id
        self._step_updates[step_id] = []
        return {"id": step_id}

    async def update_run_step(self, step_id: str, **kwargs: Any) -> None:
        if step_id not in self._step_updates:
            self._step_updates[step_id] = []
        self._step_updates[step_id].append(dict(kwargs))

    async def get_run_steps(self, run_id: str) -> list[dict]:
        return self._preloaded_steps

    async def get_run_step_by_node(self, run_id: str, node_id: str) -> dict | None:
        for s in self._preloaded_steps:
            if s.get("nodeId") == node_id:
                return s
        return None

    # -- assertion helpers --

    def final_step_status(self, node_id: str) -> str | None:
        step_id = self._node_to_step_id.get(node_id)
        if not step_id:
            return None
        statuses = [u["status"] for u in self._step_updates.get(step_id, []) if "status" in u]
        return statuses[-1] if statuses else None

    def step_output(self, node_id: str) -> dict | None:
        step_id = self._node_to_step_id.get(node_id)
        if not step_id:
            return None
        for u in reversed(self._step_updates.get(step_id, [])):
            if "output_data" in u:
                return u["output_data"]
        return None

    def retry_count(self, node_id: str) -> int:
        step_id = self._node_to_step_id.get(node_id)
        if not step_id:
            return 0
        for u in reversed(self._step_updates.get(step_id, [])):
            if "retry_count" in u:
                return u["retry_count"]
        return 0


class FakeWorkflowRepo:
    """In-memory WorkflowRepository for unit tests."""

    def __init__(
        self,
        workflow: dict | None = None,
        nodes: list[dict] | None = None,
        edges: list[dict] | None = None,
    ):
        self._workflow = workflow if workflow is not None else {"workspaceId": "ws-test"}
        self._nodes = nodes or []
        self._edges = edges or []

    async def get_by_id(self, workflow_id: str) -> dict | None:
        return self._workflow

    async def get_nodes(self, workflow_id: str) -> list[dict]:
        return self._nodes

    async def get_edges(self, workflow_id: str) -> list[dict]:
        return self._edges
