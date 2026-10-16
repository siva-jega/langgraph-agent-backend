"""Re-exports portable helpers from ``shared`` for backwards compatibility."""

from common.core.serialization import to_dict
from common.tools.slug_map import (
    map_slug_to_provider_name,
    map_slugs_to_provider_names,
)

__all__ = [
    "map_slug_to_provider_name",
    "map_slugs_to_provider_names",
    "to_dict",
]
