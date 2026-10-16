"""Ensure shared services can resolve settings when tests import tool modules."""

from app.core.config import app_settings
from common.project_settings import bind_project_settings

bind_project_settings(lambda: app_settings)
