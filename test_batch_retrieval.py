"""
Test script: validates OpenAI batch embeddings + Milvus batch dense search.

Run from repo root:
    python test_batch_retrieval.py

Tests:
  1. OpenAI embeddings.create(input=[str1, str2, str3]) — one call, N embeddings back
  2. Milvus AsyncMilvusClient.search(data=[vec1, vec2, vec3]) — one call, N result sets back
  3. Compare batch results vs individual calls to confirm they match
"""

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "packages/shared"))

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
MILVUS_URI = os.environ["MILVUS_URI"]
MILVUS_TOKEN = os.environ.get("MILVUS_TOKEN", "")
MILVUS_TIMEOUT = int(os.environ.get("MILVUS_TIMEOUT", "30"))

TEST_TEXTS = [
    "recommend me some tees",
    "recommend me some jeans",
    "tell me about Oversized Graphic Tee",
]

ASSOCIATION_ID = "bafebe97-ff0d-4a06-9bc6-ddc5b74f80b6"
FILTER_EXPR = f'associationId in ["{ASSOCIATION_ID}"]'

SEPARATOR = "-" * 60


# ------------------------------------------------------------------ #
# 1. OpenAI batch embeddings                                          #
# ------------------------------------------------------------------ #

async def test_openai_batch():
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_API_BASE)

    print(f"\n{SEPARATOR}")
    print("TEST 1: OpenAI batch embeddings")
    print(SEPARATOR)

    # --- individual calls ---
    t0 = time.perf_counter()
    individual_embeddings = []
    for text in TEST_TEXTS:
        resp = await client.embeddings.create(input=text, model=OPENAI_EMBEDDING_MODEL)
        individual_embeddings.append(resp.data[0].embedding)
    t_individual = time.perf_counter() - t0
    print(f"  Individual calls ({len(TEST_TEXTS)}x): {t_individual:.3f}s")

    # --- batch call ---
    t0 = time.perf_counter()
    batch_resp = await client.embeddings.create(input=TEST_TEXTS, model=OPENAI_EMBEDDING_MODEL)
    t_batch = time.perf_counter() - t0
    batch_embeddings = [e.embedding for e in sorted(batch_resp.data, key=lambda x: x.index)]
    print(f"  Batch call (1x):             {t_batch:.3f}s")
    print(f"  Speedup:                     {t_individual / t_batch:.1f}x")

    # --- verify same results (use cosine similarity — float precision differs across calls) ---
    print(f"\n  Verifying embeddings match (cosine similarity, threshold > 0.9999):")
    all_match = True
    for i, (ind, bat) in enumerate(zip(individual_embeddings, batch_embeddings)):
        dot = sum(a * b for a, b in zip(ind, bat))
        norm_a = sum(a * a for a in ind) ** 0.5
        norm_b = sum(b * b for b in bat) ** 0.5
        cos_sim = dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
        match = cos_sim > 0.9999
        status = f"OK (cos={cos_sim:.8f})" if match else f"MISMATCH (cos={cos_sim:.8f})"
        print(f"    [{i}] {TEST_TEXTS[i][:40]!r}  → {status}")
        if not match:
            all_match = False

    print(f"\n  Result: {'PASS — all embeddings match' if all_match else 'FAIL — embeddings differ'}")
    return batch_embeddings


# ------------------------------------------------------------------ #
# 2. Milvus batch dense search                                        #
# ------------------------------------------------------------------ #

async def _diagnose_milvus(http_client, collection_name: str, association_id: str) -> bool:
    """Run connectivity + collection diagnostics via Zilliz REST API v2.

    http_client is an httpx.AsyncClient already configured with base_url + auth headers.
    Returns True if safe to search.
    """
    print(f"\n{'─'*60}")
    print("DIAGNOSTICS")
    print(f"{'─'*60}")

    async def _rest(method: str, path: str, *, json=None, params=None, timeout=10.0):
        resp = await asyncio.wait_for(
            http_client.request(method, path, json=json, params=params),
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"REST {path} error {body.get('code')}: {body.get('message')}")
        return body

    # 1. Basic connectivity: list collections
    try:
        t0 = time.perf_counter()
        body = await _rest("POST", "/v2/vectordb/collections/list", json={})
        cols = body.get("data", [])
        print(f"  [OK] list_collections() → {cols}  ({time.perf_counter()-t0:.2f}s)")
    except asyncio.TimeoutError:
        print("  [FAIL] list_collections() TIMEOUT — server unreachable or wrong MILVUS_URI")
        return False
    except Exception as e:
        print(f"  [FAIL] list_collections() ERROR: {e}")
        return False

    if collection_name not in cols:
        print(f"  [FAIL] collection '{collection_name}' does not exist in {cols}")
        return False

    # 2. Load state
    try:
        body = await _rest(
            "GET", "/v2/vectordb/collections/get_load_state",
            params={"collectionName": collection_name},
        )
        state = body.get("data", {}).get("loadState", "unknown")
        print(f"  [  ] load_state('{collection_name}') → {state}")
        if "loaded" not in str(state).lower():
            print(f"  [!!] Collection is NOT loaded — load it via the Zilliz console.")
        else:
            print(f"  [OK] Collection is loaded")
    except asyncio.TimeoutError:
        print(f"  [WARN] get_load_state() TIMEOUT — skipping load check")
    except Exception as e:
        print(f"  [WARN] load_state check failed: {e}")

    # 3. Row count / stats
    try:
        body = await _rest(
            "POST", "/v2/vectordb/collections/get_stats",
            json={"collectionName": collection_name},
        )
        stats = body.get("data", {})
        print(f"  [OK] collection_stats → {stats}")
    except Exception as e:
        print(f"  [WARN] collection_stats failed: {e}")

    # 4. No-filter query to check data exists
    try:
        body = await _rest(
            "POST", "/v2/vectordb/entities/query",
            json={
                "collectionName": collection_name,
                "outputFields": ["associationId"],
                "limit": 3,
            },
            timeout=15.0,
        )
        rows = body.get("data", [])
        print(f"  [OK] query(no-filter, limit=3) → {len(rows)} rows: {[r.get('associationId','?') for r in rows]}")
    except asyncio.TimeoutError:
        print(f"  [FAIL] query(no-filter) TIMEOUT — collection may be empty or index missing")
    except Exception as e:
        print(f"  [WARN] query(no-filter) failed: {e}")

    # 5. Filter query for the specific associationId
    try:
        body = await _rest(
            "POST", "/v2/vectordb/entities/query",
            json={
                "collectionName": collection_name,
                "filter": f'associationId == "{association_id}"',
                "outputFields": ["associationId", "entityId"],
                "limit": 3,
            },
            timeout=15.0,
        )
        rows = body.get("data", [])
        print(f"  [OK] query(associationId filter) → {len(rows)} rows")
        if not rows:
            print(f"  [!!] NO DATA found for associationId={association_id!r}")
            print(f"       Check ASSOCIATION_ID constant in the test — wrong tenant?")
    except asyncio.TimeoutError:
        print(f"  [FAIL] query(filter) TIMEOUT — scalar field 'associationId' may lack an index")
    except Exception as e:
        print(f"  [WARN] query(filter) failed: {e}")

    print(f"{'─'*60}\n")
    return True


async def test_milvus_batch(embeddings: list):
    from common.infrastructure.milvus import MilvusClient

    print(f"\n{SEPARATOR}")
    print("TEST 2: Milvus batch search(data=[vec1, vec2, vec3])")
    print(SEPARATOR)

    class _Settings:
        MILVUS_URI = MILVUS_URI
        MILVUS_TOKEN = MILVUS_TOKEN
        MILVUS_TIMEOUT = MILVUS_TIMEOUT

    client = MilvusClient(_Settings())
    await client.connect()

    collection_name = "b2c"
    print(f"  Using collection: {collection_name}")

    # ── Run diagnostics first ──────────────────────────────────────────
    ok = await _diagnose_milvus(client._http, collection_name, ASSOCIATION_ID)
    if not ok:
        print("  [ABORT] Diagnostics failed — fix connectivity/collection before benchmarking.")
        await client.disconnect()
        return

    SEARCH_TIMEOUT = 20.0  # hard asyncio wall-clock timeout per operation

    async def _safe(coro, label):
        try:
            return await asyncio.wait_for(coro, timeout=SEARCH_TIMEOUT)
        except asyncio.TimeoutError:
            print(f"  TIMEOUT ({SEARCH_TIMEOUT}s): {label}")
            return None
        except Exception as e:
            print(f"  ERROR [{label}]: {e}")
            return None

    entity_ids = []
    folder_ids = [ASSOCIATION_ID]
    top_k = 5

    # --- individual searches ---
    print(f"\n  Running individual searches...")
    t0 = time.perf_counter()
    individual_results = []
    for i, vec in enumerate(embeddings):
        print(f"    search [{i}] '{TEST_TEXTS[i]}'...")
        res = await _safe(
            client.search_by_embedding_and_entity_and_folder_ids(
                collection_name=collection_name,
                embedding=vec,
                entity_ids=entity_ids,
                folder_ids=folder_ids,
                top_k=top_k,
            ),
            f"search[{i}]",
        )
        hits = list(res) if res else []
        individual_results.append(hits)
        print(f"    search [{i}] done — result sets: {len(hits)}, hits[0]: {len(hits[0]) if hits else 0}")
    t_individual = time.perf_counter() - t0
    print(f"\n  Individual searches ({len(embeddings)}x): {t_individual:.3f}s")

    # --- batch search: one REST call with data=[v1,v2,v3] returning N result sets ---
    print(f"\n  Running batch search (data=[v1,v2,v3] in one call)...")
    t0 = time.perf_counter()
    batch_results_raw = await _safe(
        client.search_by_embeddings_batch(
            collection_name=collection_name,
            embeddings=embeddings,
            entity_ids=entity_ids,
            folder_ids=folder_ids,
            top_k=top_k,
        ),
        "batch search",
    )
    if batch_results_raw is None:
        await client.disconnect()
        return
    batch_results = list(batch_results_raw)
    print(f"  Batch search done — result sets: {len(batch_results)}")
    t_batch = time.perf_counter() - t0
    print(f"\n  Batch search (1x):           {t_batch:.3f}s")
    print(f"  Speedup:                     {t_individual / t_batch:.1f}x")
    print(f"  Result sets returned:        {len(batch_results)} (expected {len(embeddings)})")
    for i, hits in enumerate(batch_results):
        print(f"    [{i}] hits: {len(hits)}")

    # --- verify same top hit per query ---
    print(f"\n  Verifying batch[i] matches individual[i] (top hit id):")
    all_match = True
    for i in range(len(embeddings)):
        ind_hits = individual_results[i][0] if individual_results[i] else []
        bat_hits = batch_results[i] if i < len(batch_results) else []

        ind_top = ind_hits[0].get("id") if ind_hits else None
        bat_top = bat_hits[0].get("id") if bat_hits else None
        match = ind_top == bat_top
        status = "OK" if match else f"MISMATCH (ind={ind_top}, bat={bat_top})"
        print(f"    [{i}] top_hit_id={bat_top}  → {status}")
        if not match:
            all_match = False

    print(f"\n  Result: {'PASS — batch results match individual' if all_match else 'FAIL — results differ'}")
    await client.disconnect()


# ------------------------------------------------------------------ #
# Main                                                                #
# ------------------------------------------------------------------ #

async def main():
    print("Batch retrieval validation script")
    print(f"  Model:      {OPENAI_EMBEDDING_MODEL}")
    print(f"  Milvus URI: {MILVUS_URI}")
    print(f"  Texts:      {len(TEST_TEXTS)}")

    embeddings = await test_openai_batch()
    await test_milvus_batch(embeddings)

    print(f"\n{SEPARATOR}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
