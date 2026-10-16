# Shopify Knowledge Base Product Cards Plan

## Goal

When a Shopify website is connected as knowledge base content, product cards must come from first-class product nodes in Milvus. Page chunks and shop summary nodes must never be used as card fallbacks.

If matching product nodes are retrieved, `search_knowledgebase` returns the normal `Knowledge Base` artifact plus a `Shopify` / `Get Products` artifact so the widget renders cards. If no product node is retrieved, the assistant should say no matching products were found instead of inventing products from page text or model knowledge.

## Root Cause From Trace

Trace `tmp/Trace-1569fd-2026-05-20 01_40_45.json` showed:

- only `search_knowledgebase` was exposed; this was not web search and not `get_products`
- retrieved Milvus data was scoped, but it was a Shopify website/page node
- product information existed only inside `shopify_products_summary.products_preview`
- no individual `record_type: "product"` node was retrieved, so no card artifact was produced
- final LLM context was very large, with about 119k input tokens in the final `openai.chat`

The bug is ingestion shape, not the frontend. Runtime cannot reliably build product cards from scraped page text or summary previews.

## Production Fix

### 1. Store Product Nodes During Shopify Website Ingestion

During `ShopifyWebsitesCrawlWorkflow`, keep the existing page ingestion, but also write one compact product node per Shopify product.

The product nodes must be emitted by ingestion before embedding:

```json
{
  "content": "compact searchable product text",
  "metadata": {
    "connector_type": "SHOPIFY_WEBSITES",
    "record_type": "product",
    "website_id": "selected-parent-website-id",
    "item_id": "selected-parent-website-id",
    "source_id": "source-or-agent-association-id",
    "id": "123",
    "externalId": "gid://shopify/Product/123",
    "title": "Idika Handmade Diary",
    "description": "Clean product description",
    "handle": "idika-handmade-diary",
    "onlineStoreUrl": "https://store/products/idika-handmade-diary",
    "product_url": "https://store/products/idika-handmade-diary",
    "image_url": "https://cdn.shopify.com/...",
    "image_urls": ["https://cdn.shopify.com/..."],
    "minVariantPrice": "270.00",
    "maxVariantPrice": "270.00",
    "currencyCode": "INR",
    "availableForSale": true,
    "variants": []
  }
}
```

Important scope rule:

- `item_id` must be the selected Shopify website entity ID, not only the child product URL row ID.
- This makes product nodes retrievable when the agent searches the parent Shopify website knowledge source.
- Re-ingestion can delete and recreate all vectors for that parent entity ID.
- Shopify product URL page chunks remain `record_type: "page"` and are not card-eligible.
- The separate compact product nodes are the only `record_type: "product"` records.
- Product nodes must bypass generic website chunking. One Shopify product should create one product vector row so one retrieved product maps to one product card candidate.
- Because Shopify website extraction is batched, any batch containing the parent shop URL must fetch the full catalog, not only product handles present in that batch.

### 2. Keep Metadata Minimal

Do not store noisy trace-derived fields or raw Shopify payloads in Milvus metadata.

Keep only:

- product identity: `id`, `externalId`, `handle`, `title`
- card fields: URL, image URLs, price range, currency, availability
- small variant card fields
- scope/provenance: `connector_type`, `record_type`, `website_id`, `item_id`, `source_id`

Avoid:

- `shopify_products_summary.products_preview`
- raw `product_data`
- trace/debug metadata copied from Grafana spans
- raw page HTML
- full product JSON
- non-card fields such as `vendor` and `productType` in Milvus metadata
- large image objects when only URLs are needed

Store descriptive product text in the compact embedding content, not as large card metadata. Product descriptions stored for card display should be short and stripped of HTML.

### 3. Do Not Add Product-First Retrieval

`search_knowledgebase` is shared by uploaded files, websites, Q&A, and normal KB search. Do not change global retrieval into a product-first path.

Instead:

1. Keep existing scoped hybrid retrieval.
2. Let product nodes participate in normal Milvus search because their `content` is compact and product-specific.
3. After retrieval, build cards only from nodes whose metadata has `record_type: "product"`.
4. Ignore page chunks and shop summary nodes for product cards.

### 4. Runtime Card Builder Contract

`search_knowledgebase` should produce a Shopify artifact only when retrieved nodes contain explicit product records:

```json
{
  "name": "Shopify",
  "tool": "Get Products",
  "data": []
}
```

Accepted product node conditions:

- `connector_type` is `SHOPIFY_WEBSITES` or `shopify`
- `record_type` is exactly `product`
- title exists
- product URL exists
- at least one product image exists

This prevents summary/page nodes from rendering unsafe cards.

### 5. Latency Fixes

Primary latency issue in the trace was LLM context size, not Milvus alone.

Production hardening:

- keep product node `content` compact
- emit exactly one vector row per product node
- do not send raw product metadata arrays to the LLM
- cap KB text snippets before returning tool content or artifacts
- strip noisy page footer/social/image URL text from LLM-facing KB snippets
- add trace attributes for raw tool chars before and after compaction

Target:

- product-card query p95 under 6 seconds after product nodes exist
- normal KB query p95 under 8 seconds
- final widget `openai.chat` input under 12k tokens for normal chats

## Implementation Status

Implemented code changes:

- Shopify website extraction emits compact `additional_nodes` product records under the parent website entity ID.
- Website ingestion embeds product `additional_nodes` as single product vectors, bypassing generic website chunking.
- Website ingestion does not copy workflow-level metadata onto product nodes; product metadata stays limited to card fields and explicit scope/provenance.
- Shopify product app ingestion marks product vectors with `record_type: "product"`.
- `search_knowledgebase` card builder only accepts explicit product nodes.
- `search_knowledgebase` caps LLM-facing KB text per result and across both tool content and the KB artifact while preserving Shopify card artifacts from metadata.
- `search_knowledgebase` sets trace attributes for retrieved node count and raw/compacted LLM text size when OpenTelemetry is available.
- Agent prompt guidance recognizes `search_knowledgebase` product-node artifacts as valid product cards and forbids turning page text or summaries into product recommendations.

Verified locally:

- Extraction test proves a parent Shopify website payload emits full-catalog product `additional_nodes` without `shopify_products_summary` or `product_data`.
- Ingestion test proves `ingest_websites` embeds product `additional_nodes` without chunking and sends product nodes to Milvus with parent `entityId`.
- Ingestion regression test proves mixed parent/child website batches preserve parent and child product `entityId` values without chunking product nodes.
- Ingestion tests prove workflow-level metadata is not copied onto product nodes.
- Metadata test proves long descriptions are capped and non-card metadata is not persisted for product cards.
- Runtime tests prove KB text returned to the LLM is compacted with hard per-result and total budgets.
- Runtime tests prove KB compaction metrics are attached to the current trace span.
- Runtime tests prove page/summary nodes do not build `Shopify` artifacts, while explicit product nodes do.
- Agent service test proves KB-backed `Shopify` sources are converted into product/action data for non-streaming channel responses.
- Logs now expose Shopify product-node emission, product-node ingestion, and KB Shopify artifact counts for staging trace validation.

Re-ingestion is required for existing staging data because old Milvus rows do not contain parent-scoped product nodes.

## Acceptance Criteria

- Re-ingesting a Shopify website creates page nodes plus product nodes.
- Product nodes have `record_type: "product"`.
- Product nodes use the parent selected website ID as `item_id` / Milvus `entityId`.
- Each product node is one vector row, not multiple page chunks.
- Page nodes and summary nodes do not create product cards.
- Querying a known product such as `Idika Handmade Diary` retrieves a product node and returns a `Shopify` / `Get Products` artifact.
- Querying a missing category such as `perfumes` returns no cards and no generic product recommendations.
- Grafana traces show product nodes returned and Shopify artifact count.

## Rollout

1. Deploy ingestion and `ai-backend` runtime changes to staging.
2. Re-ingest the affected Shopify website knowledge base.
3. Inspect Milvus rows for the selected parent website `entityId`.
4. Verify at least one row has `metadata.record_type == "product"`.
5. Run widget queries for matching and non-matching products.
6. Promote to production after traces show cards are produced only from product nodes.

## Milvus Re-Ingestion Validation

After deploying and re-ingesting the Shopify website knowledge base, inspect rows scoped to the selected parent website entity ID.

Expected parent website rows:

- `entityId == "<selected-parent-website-id>"`
- `associationId == "<source-id>"`
- at least one row with `metadata.record_type == "page"`
- at least one row with `metadata.record_type == "product"`
- product rows have `metadata.item_id == "<selected-parent-website-id>"`
- product rows have `metadata.connector_type == "SHOPIFY_WEBSITES"`
- product rows have card fields: `title`, `onlineStoreUrl` or `product_url`, and `image_url` or `image_urls`
- product rows do not have `shopify_products_summary`, `product_data`, raw page HTML, or workflow-level metadata copied onto them

Expected child product URL rows, when child product URLs are selected independently:

- `entityId == "<child-product-website-id>"`
- page chunk has `metadata.record_type == "page"`
- compact product node has `metadata.record_type == "product"`
- compact product node has `metadata.item_id == "<child-product-website-id>"`

If parent website rows have only `record_type: "page"` after re-ingestion, the ingestion deployment did not run the new product-node path or the parent shop batch did not fetch the full catalog.

If product rows exist under child product URL entity IDs only, but not under the selected parent website entity ID, the agent will still miss cards when the parent website source is selected.

## Trace Validation Checklist

For a known product query such as `Idika Handmade Diary`, the staging trace should show:

- `search_knowledgebase` called before any Shopify fallback.
- `retrieve_multiple` receives the selected parent website ID in `entity_ids` or the selected folder ID in `folder_ids`.
- Milvus search filter includes the same selected scope.
- At least one retrieved row has `entityId` equal to the selected parent website ID.
- At least one retrieved row has metadata with `connector_type: "SHOPIFY_WEBSITES"` and `record_type: "product"`.
- `Knowledgebase Shopify artifact built` log has `product_nodes_returned > 0` and `artifact_count > 0`.
- The active span has `knowledgebase.retrieved_nodes`, `knowledgebase.llm_text.raw_chars`, `knowledgebase.llm_text.compacted_chars`, and `knowledgebase.llm_text.compacted`.
- The stream/source artifacts include `{"name":"Shopify","tool":"Get Products"}` with product card data.
- Final widget response renders product cards and keeps text short.

For a missing product/category query such as `perfumes`, the staging trace should show:

- `search_knowledgebase` still uses the selected `entity_ids` / `folder_ids`.
- Retrieved page rows may appear, but no retrieved row with `record_type: "product"` should match the query.
- `Knowledgebase Shopify artifact skipped` should show `product_nodes_returned=0` or `valid_artifacts=0`.
- The active span has `knowledgebase.llm_text.compacted_chars` well below the raw retrieved text size when page rows are returned.
- No `Shopify` / `Get Products` artifact should be returned from `search_knowledgebase`.
- If the standalone Shopify app is not connected, `get_products` should not be called.
- The assistant should say no matching products were found instead of listing products from page text, summary text, web search, or general model knowledge.

If cards still do not render for a known product after re-ingestion, inspect these fields first:

- retrieved Milvus row `entityId`
- retrieved Milvus row `metadata.record_type`
- retrieved Milvus row `metadata.title`
- retrieved Milvus row `metadata.onlineStoreUrl` or `metadata.product_url`
- retrieved Milvus row `metadata.image_url` or `metadata.image_urls`
- stream artifact name/tool: must be `Shopify` / `Get Products`
