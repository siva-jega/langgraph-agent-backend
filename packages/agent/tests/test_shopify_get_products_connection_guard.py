from common.tools.providers.shopify.tools import _get_shopify_connector_id


def test_get_shopify_connector_id_requires_connected_shopify_app() -> None:
    assert _get_shopify_connector_id([]) is None
    assert _get_shopify_connector_id([{"slug": "gmail", "id": "gmail-1"}]) is None


def test_get_shopify_connector_id_reads_connector_id_from_shopify_app() -> None:
    assert (
        _get_shopify_connector_id(
            [{"slug": "shopify", "connectorId": "shopify-connector-1"}]
        )
        == "shopify-connector-1"
    )
