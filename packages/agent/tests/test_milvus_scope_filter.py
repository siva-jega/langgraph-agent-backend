import pytest

from common.infrastructure.milvus import MilvusClient


def test_milvus_scope_filter_requires_entity_or_folder_ids() -> None:
    with pytest.raises(ValueError, match="requires entity_ids or folder_ids"):
        MilvusClient._build_required_scope_filter([], [])


def test_milvus_scope_filter_uses_entity_and_folder_fields() -> None:
    expr = MilvusClient._build_required_scope_filter(
        entity_ids=["entity-1", "entity-2"],
        folder_ids=["folder-1"],
    )

    assert 'entityId == "entity-1"' in expr
    assert 'entityId == "entity-2"' in expr
    assert 'associationId == "folder-1"' in expr
