import json

import pytest

from common.modules.retrieval import RetrievalResponse
from common.tools.providers.knowledgebase import tools as knowledgebase_tools
from common.tools.providers.knowledgebase.tools import (
    KNOWLEDGEBASE_LLM_TEXT_LIMIT_CHARS,
    KNOWLEDGEBASE_LLM_TOTAL_TEXT_LIMIT_CHARS,
    _build_knowledgebase_artifact_data,
    _build_knowledgebase_responses,
    _build_shopify_product_artifact,
    _compact_text_for_llm,
    _is_shopify_product_node,
    search_knowledgebase,
)


def _node(metadata: dict, text: str = "retrieved content") -> RetrievalResponse:
    return RetrievalResponse(
        text=text,
        metadata=json.dumps(metadata),
        score=0.91,
        source="dense_search",
    )


def test_shopify_websites_product_metadata_builds_widget_artifact() -> None:
    node = _node(
        {
            "connector_type": "SHOPIFY_WEBSITES",
            "record_type": "product",
            "id": "9269863809239",
            "externalId": "gid://shopify/Product/9269863809239",
            "title": "Crystal Multicolor Stone Bracelet",
            "description": "Elegant bracelet",
            "onlineStoreUrl": "https://salty.co.in/products/crystal-multicolor-stone-bracelet-pack-of-1",
            "image_url": "https://cdn.shopify.com/product-main.png",
            "image_urls": [
                "https://cdn.shopify.com/product-main.png",
                "https://cdn.shopify.com/product-alt.png",
            ],
            "minVariantPrice": "519.00",
            "maxVariantPrice": "519.00",
            "currencyCode": "INR",
            "variants": [
                {
                    "id": "48635918647511",
                    "externalId": "gid://shopify/ProductVariant/48635918647511",
                    "title": "Default Title",
                    "displayName": "Default Title",
                    "price": "519.00",
                    "compareAtPrice": "1039.00",
                    "availableForSale": True,
                    "inventoryQuantity": 7,
                }
            ],
        }
    )

    artifact = _build_shopify_product_artifact([node])

    assert artifact is not None
    assert artifact["name"] == "Shopify"
    assert artifact["tool"] == "Get Products"
    assert len(artifact["data"]) == 1

    product = artifact["data"][0]
    assert product["name"] == "Crystal Multicolor Stone Bracelet"
    assert (
        product["product_url"]
        == "https://salty.co.in/products/crystal-multicolor-stone-bracelet-pack-of-1"
    )
    assert product["images"] == [
        "https://cdn.shopify.com/product-main.png",
        "https://cdn.shopify.com/product-alt.png",
    ]
    assert product["price_range"] == "INR 519.00"
    assert product["availableForSale"] is True
    assert product["variants"][0]["externalId"] == (
        "gid://shopify/ProductVariant/48635918647511"
    )


def test_shopify_websites_index_summary_does_not_build_product_artifact() -> None:
    node = _node(
        {
            "website_id": "199118fe-da91-4e11-860b-4dace05cc674",
            "link": "https://yellownaturals.com",
            "title": "Shopify products index",
            "item_id": "199118fe-da91-4e11-860b-4dace05cc674",
            "connector_type": "SHOPIFY_WEBSITES",
            "shopify_products_summary": {
                "shop_url": "https://yellownaturals.com",
                "products_count": 14,
                "handles": ["hydrate-fun-body-lotion-for-kids"],
            },
        }
    )

    assert _is_shopify_product_node(node) is False
    assert _build_shopify_product_artifact([node]) is None


def test_shopify_card_fields_without_product_record_type_do_not_build_artifact() -> None:
    node = _node(
        {
            "connector_type": "SHOPIFY_WEBSITES",
            "id": "123",
            "externalId": "gid://shopify/Product/123",
            "title": "Hydrating Body Wash",
            "onlineStoreUrl": "https://store.test/products/body-wash",
            "image_urls": ["https://cdn.shopify.com/body-wash.png"],
            "minVariantPrice": "299.00",
            "maxVariantPrice": "399.00",
            "currencyCode": "INR",
        }
    )

    assert _is_shopify_product_node(node) is False
    assert _build_shopify_product_artifact([node]) is None


def test_shopify_page_chunk_with_card_fields_does_not_build_artifact() -> None:
    node = _node(
        {
            "connector_type": "SHOPIFY_WEBSITES",
            "record_type": "page",
            "id": "123",
            "externalId": "gid://shopify/Product/123",
            "title": "Hydrating Body Wash",
            "onlineStoreUrl": "https://store.test/products/body-wash",
            "image_urls": ["https://cdn.shopify.com/body-wash.png"],
            "minVariantPrice": "299.00",
            "maxVariantPrice": "399.00",
            "currencyCode": "INR",
        }
    )

    assert _is_shopify_product_node(node) is False
    assert _build_shopify_product_artifact([node]) is None


def test_non_product_knowledgebase_metadata_does_not_build_shopify_artifact() -> None:
    node = _node(
        {
            "website_id": "5b0aeec5-577b-4a2d-a544-e16341c97b9f",
            "link": "https://quanutrition.com/post-sitemap.xml",
            "title": None,
            "item_id": "5b0aeec5-577b-4a2d-a544-e16341c97b9f",
            "connector_type": "tavily",
            "chunk_index": 19,
            "source_id": "ff57c4ec-de33-40e0-8af3-ef15b1ab3761",
        }
    )

    assert _is_shopify_product_node(node) is False
    assert _build_shopify_product_artifact([node]) is None


def test_mixed_results_only_include_shopify_product_hits() -> None:
    non_product_node = _node(
        {
            "connector_type": "SHOPIFY_WEBSITES",
            "title": "Shopify products index",
            "link": "https://store.test",
            "shopify_products_summary": {"products_count": 1},
        }
    )
    product_node = _node(
        {
            "connector_type": "SHOPIFY_WEBSITES",
            "record_type": "product",
            "id": "123",
            "title": "Hydrating Body Wash",
            "onlineStoreUrl": "https://store.test/products/body-wash",
            "image_urls": ["https://cdn.shopify.com/body-wash.png"],
            "minVariantPrice": "299.00",
            "maxVariantPrice": "399.00",
            "currencyCode": "INR",
        }
    )

    artifact = _build_shopify_product_artifact([non_product_node, product_node])

    assert artifact is not None
    assert len(artifact["data"]) == 1
    assert artifact["data"][0]["name"] == "Hydrating Body Wash"


def test_knowledgebase_text_is_compacted_for_llm_context() -> None:
    text = "  ".join(["very-long-page-text"] * 400)

    compacted = _compact_text_for_llm(text)

    assert len(compacted) <= KNOWLEDGEBASE_LLM_TEXT_LIMIT_CHARS
    assert "\n" not in compacted
    assert compacted.endswith("...")


def test_knowledgebase_artifact_data_has_total_text_budget() -> None:
    nodes = [
        _node({"title": f"Page {index}"}, text=" ".join(["content"] * 700))
        for index in range(20)
    ]

    artifact_data = _build_knowledgebase_artifact_data(nodes)

    assert sum(len(item["text"]) for item in artifact_data) <= (
        KNOWLEDGEBASE_LLM_TOTAL_TEXT_LIMIT_CHARS
    )
    assert len(artifact_data) < len(nodes)


def test_knowledgebase_responses_reuse_bounded_artifact_text() -> None:
    nodes = [
        _node({"title": f"Page {index}"}, text=" ".join(["content"] * 700))
        for index in range(20)
    ]

    artifact_data = _build_knowledgebase_artifact_data(nodes)
    responses = _build_knowledgebase_responses(artifact_data)

    assert [response.text for response in responses] == [
        item["text"] for item in artifact_data
    ]
    assert sum(len(response.text) for response in responses) <= (
        KNOWLEDGEBASE_LLM_TOTAL_TEXT_LIMIT_CHARS
    )


@pytest.mark.asyncio
async def test_search_knowledgebase_returns_bounded_kb_text_and_shopify_artifact(monkeypatch) -> None:
    product_node = _node(
        {
            "connector_type": "SHOPIFY_WEBSITES",
            "record_type": "product",
            "id": "123",
            "title": "Hydrating Body Wash",
            "onlineStoreUrl": "https://store.test/products/body-wash",
            "image_urls": ["https://cdn.shopify.com/body-wash.png"],
            "minVariantPrice": "299.00",
            "maxVariantPrice": "399.00",
            "currencyCode": "INR",
        },
        text=" ".join(["product content"] * 500),
    )
    page_nodes = [
        _node({"title": f"Page {index}"}, text=" ".join(["page content"] * 700))
        for index in range(20)
    ]
    captured_retrieval_kwargs: dict = {}
    captured_span_attributes: dict = {}

    class FakeRetrieval:
        async def retrieve_multiple(self, **kwargs):
            captured_retrieval_kwargs.update(kwargs)
            return [product_node, *page_nodes]

    class FakeSpan:
        def is_recording(self):
            return True

        def set_attribute(self, key, value):
            captured_span_attributes[key] = value

    class FakeTrace:
        @staticmethod
        def get_current_span():
            return FakeSpan()

    monkeypatch.setattr(knowledgebase_tools, "get_retrieval", lambda: FakeRetrieval())
    monkeypatch.setattr(knowledgebase_tools, "emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(knowledgebase_tools, "otel_trace", FakeTrace)

    responses, artifacts = await search_knowledgebase.coroutine(
        query="body wash",
        query_text_for_sparse_search="body wash",
        text_list=["body wash"],
        config={
            "configurable": {
                "collection_name": "test_collection",
                "entity_ids": ["parent-website-id"],
                "folder_ids": [],
            }
        },
    )

    knowledgebase_artifact = artifacts[0]
    shopify_artifact = artifacts[1]

    assert knowledgebase_artifact["name"] == "Knowledge Base"
    assert captured_retrieval_kwargs["entity_ids"] == ["parent-website-id"]
    assert captured_retrieval_kwargs["folder_ids"] == []
    assert captured_retrieval_kwargs["collection_name"] == "test_collection"
    assert captured_span_attributes["knowledgebase.retrieved_nodes"] == 21
    assert captured_span_attributes["knowledgebase.llm_text.raw_chars"] > (
        captured_span_attributes["knowledgebase.llm_text.compacted_chars"]
    )
    assert captured_span_attributes["knowledgebase.llm_text.compacted"] is True
    assert sum(len(response.text) for response in responses) <= (
        KNOWLEDGEBASE_LLM_TOTAL_TEXT_LIMIT_CHARS
    )
    assert sum(len(item["text"]) for item in knowledgebase_artifact["data"]) <= (
        KNOWLEDGEBASE_LLM_TOTAL_TEXT_LIMIT_CHARS
    )
    assert all(len(response.text) <= KNOWLEDGEBASE_LLM_TEXT_LIMIT_CHARS for response in responses)
    assert shopify_artifact["name"] == "Shopify"
    assert shopify_artifact["tool"] == "Get Products"
    assert shopify_artifact["data"][0]["name"] == "Hydrating Body Wash"
