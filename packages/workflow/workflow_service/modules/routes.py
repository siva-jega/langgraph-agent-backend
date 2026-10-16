from fastapi import APIRouter

from workflow_service.modules.app_events.route import router as app_events_router
from workflow_service.modules.nodes.route import router as nodes_router
from workflow_service.modules.runs.route import router as runs_router
from workflow_service.modules.tools.route import router as tools_router
from workflow_service.modules.workflows.route import router as workflows_router

router = APIRouter()
router.include_router(workflows_router)
router.include_router(runs_router)
router.include_router(tools_router)
router.include_router(nodes_router)
router.include_router(app_events_router)
