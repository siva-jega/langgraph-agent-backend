"""Response and data models for token operations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class TokenResponse:
    """Result of get_user_data: token, metadata, expiry, is_refreshed, optional error."""

    token: str
    metadata: Dict[str, Any]
    expires_at: Optional[datetime]
    is_refreshed: bool = False
    error: Optional[str] = None
