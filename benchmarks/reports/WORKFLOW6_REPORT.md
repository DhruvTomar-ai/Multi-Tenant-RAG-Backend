# Workflow 6 report: permission-aware retrieval

**Date:** 2026-11-04. **Status:** backend complete and verified. The search interface is not built, because you chose API only, so that completion criterion stays open.

All numbers below were measured in executed runs, on throwaway containers that have since been removed.

## 1. Test results

| Run | Result |
|---|---|
| `tests/` on SQLite | 227 of 227 passed |
| `tests/` on PostgreSQL | 227 of 227 passed |
| `api_tests/` on PostgreSQL, with real migrations, model, Redis, worker and server | 1,515 run: 1,479 passed, 36 failed, 0 errors, 0 skipped |
| Migration downgrade and upgrade, test database | Passed |

The 36 failures are exactly the 36 from before this workflow, compared by test name. `api_test_report.md` counts 1,513 test cases because 2 passing tests send no request and record no row.

| Area | Test cases | Passed | Failed |
|---|---|---|---|
| Permission-aware search (new) | 55 | 55 | 0 |
| Ownership and document grants (new) | 58 | 58 | 0 |
| Embeddings and HNSW | 64 | 64 | 0 |
| Document metadata | 42 | 42 | 0 |
| Database isolation | 18 | 18 | 0 |
| Tenant isolation | 197 | 195 | 2 |
| Team isolation | 273 | 269 | 4 |
| Role and permission isolation | 194 | 193 | 1 |
| Authentication | 351 | 344 | 7 |
| Tenant and team APIs | 143 | 133 | 10 |
| Document and job APIs | 77 | 74 | 3 |
| User management | 12 | 5 | 7 |
| Real server | 29 | 27 | 2 |

The 2 tenant and 4 team failures are the known lifecycle and existence-disclosure findings. None returns another tenant's or team's content.

## 2. Your security scenarios

| # | Scenario | Result |
|---|---|---|
| 1 | Tenant A searches for Tenant B's document | Passed: B's content never returned |
| 2 | Tenant A manipulates `tenant_id` | Passed: wrong tenant in the URL gets 403, `tenant_id` in the body gets 422, header, query and token claim are ignored |
| 3 | Team A searches for Team B's documents | Passed: each of 11 users got exactly their authorized documents |
| 4 | Unauthorized `team_id` | Passed: empty result, identical to a team that does not exist |
| 5 | Unauthorized `document_id` | Passed: empty result, identical to a document that does not exist |
| 6 | Unauthorized chunk | Passed: direct read refused, search does not return it, `chunk_ids` in the body gets 422 |
| 7 | User with a grant retrieves the document | Passed: in search and all 8 read endpoints |
| 8 | User without a grant | Passed: nothing returned, including when naming the document, and revoking applies at once |
| 9 | Soft-deleted document | Passed: excluded |
| 10 | Inactive chunk or embedding | Passed: inactive documents, partially embedded documents and vectors from an old model version are excluded |
| 11 | User in multiple teams | Passed: both teams returned, and `team_ids` narrows correctly |
| 12 | User in multiple tenants | Passed: no crossover, and an inactive membership blocks only that tenant |
| 13 | Changing filters cannot bypass authorization | Passed at the API level, since there is no frontend. Identical metadata in another team or tenant stays hidden |
| 14 | Empty results | Passed: 200 with an empty list |

Other confirmed behaviour:

- One SQL query reads vectors, with authorization inside it. Captured SQL confirms the conditions are present.
- The number of SQL statements is the same for 1 and 50 results, so there is no N+1 problem.
- `SET LOCAL` values do not leak past the transaction.
- The model loads once per process.
- Real and fake tenant, team and document IDs give identical responses in search.
- Search, grants and real-model embedding also passed on a real uvicorn server, with a real Celery worker and Redis.

## 3. How authorization works

Identity comes only from the JWT, and permissions are read from the database on every request. The tenant in the URL must match an active membership in an active tenant.

`readable_document_condition` in `app/security/authorization.py` is the one definition of read access. It is used by search and every document read path:

- **Team rule (existing):** the user holds `document.read` in the document's team, through a team role or a tenant role with `team.access_all`.
- **Uploader (new):** they uploaded it, and are an active member of its active tenant and the team is active.
- **Grant (new):** they hold a grant on it, under the same conditions.

Uploader and grant access are read-only and give no other right, such as uploading, listing the team or sharing. Only holders of `team.manage` on the document's team can create, list or revoke grants. Composite foreign keys make the database refuse a grant to anyone outside the tenant.

### SQL structure

```sql
SELECT embeddings.id, embeddings.chunk_id, embeddings.document_id, embeddings.team_id,
       document_chunks.chunk_index, document_chunks.chunk_text,
       embeddings.embedding <=> :query AS distance,
       documents.original_filename, documents.version, ...metadata columns...
FROM embeddings
JOIN documents ON documents.id = embeddings.document_id
JOIN document_chunks ON document_chunks.id = embeddings.chunk_id
WHERE embeddings.tenant_id = :tenant
  AND documents.tenant_id = :tenant
  AND (
        documents.team_id IN (teams where the user holds document.read)
        OR (
          documents.team_id IN (active teams in the user's active tenants)
          AND (documents.uploaded_by_user_id = :user OR EXISTS (a grant for this user on this document))
        )
      )
  AND embeddings.model_name = :model AND embeddings.model_version = :revision
  AND documents.status = 'completed' AND documents.embedding_status = 'completed'
  AND documents.is_active AND documents.deleted_at IS NULL
  AND NOT EXISTS (a newer, searchable version of this document)
  -- Optional: team_ids, document_ids and metadata filters only narrow this further.
ORDER BY distance
LIMIT :top_k_plus_one OFFSET :offset;
```

`hnsw.ef_search`, `hnsw.iterative_scan`, `hnsw.max_scan_tuples` and `hnsw.scan_mem_multiplier` are set per transaction first. The existing HNSW index was not changed.

## 4. API

**`POST /api/v1/tenants/{tenant_id}/search`**. The tenant is in the path to match your other routes, instead of your example's `/api/v1/search`.

Request fields:

| Field | Rule |
|---|---|
| `query` | Required, up to 2,000 characters |
| `team_ids` | Optional, up to 50. Empty means no filter |
| `document_ids` | Optional, up to 100. Empty means no filter |
| `top_k` | Default 5, maximum 50 (configurable) |
| `offset` | Default 0, maximum 200 (configurable) |
| `filters` | `document_type`, `department`, `category`, `source`, `access_level`, `file_type`, `tags`, `custom_metadata` |

Unknown fields get 422.

Example request:

```json
{
  "query": "What is the company's leave policy?",
  "team_ids": [2],
  "document_ids": [],
  "top_k": 5,
  "filters": {"department": "HR"}
}
```

Response shape, with illustrative values:

```json
{
  "query": "What is the company's leave policy?",
  "results": [
    {
      "chunk_id": 101,
      "chunk_index": 0,
      "document_id": 25,
      "document_title": "leave-policy.pdf",
      "team_id": 2,
      "content": "Employees are entitled to...",
      "similarity_score": 0.89,
      "metadata": {
        "document_type": "policy",
        "department": "HR",
        "category": null,
        "source": null,
        "access_level": "team",
        "file_type": ".pdf",
        "tags": ["handbook"],
        "custom_metadata": {},
        "version": 2,
        "uploaded_at": "2026-11-04T10:00:00Z"
      }
    }
  ],
  "total_results": 1,
  "top_k": 5,
  "offset": 0,
  "has_more": false,
  "score_type": "cosine_similarity"
}
```

- `similarity_score` is 1 minus pgvector's cosine distance, so 1 is most similar and it can fall to -1.
- `total_results` counts this page only, because a true total could reveal how much exists.
- `document_title` is the original filename, because documents have no title field.

**Grant endpoints**, under `/api/v1/tenants/{tenant_id}/teams/{team_id}/documents/{document_id}`:

- `POST /grants` with `{"user_id": 7}`
- `GET /grants`
- `DELETE /grants/{user_id}`

## 5. Frontend screens

None were implemented, because you chose API only. All four frontend files are still empty, and sections 4 and 5 of your brief were missing.

## 6. Retrieval measurements

**Data:** 500 documents, 4,596 chunks and embeddings. Large tenant 3,766 vectors, small tenant 108. Real text, CPU only, 100 queries per scenario, `top_k=10`, shipped settings.

**Latency through the real route** (in-process HTTP client, so no network time):

| Scenario | p50 ms | p95 ms |
|---|---|---|
| Large tenant admin, no filter | 59.7 | 73.8 |
| Team member with 5 grants | 60.4 | 74.9 |
| Small tenant admin, no filter | 58.9 | 80.2 |
| `team_ids` filter | 59.8 | 74.8 |
| Department filter (50% of documents) | 62.0 | 74.7 |
| Department filter (5% of documents) | 61.2 | 79.7 |
| Tag filter | 60.7 | 80.2 |
| Custom metadata filter | 67.8 | 80.5 |
| `document_ids` filter | 65.1 | 80.4 |

Of this, query embedding took 19.0 ms p50, and the vector query took about 5–6 ms. The rest was not broken down.

The **first search after startup took 7.5 s**, because it loads the model.

**Relevance:**

- Curated test: 5 of 5 natural-language questions put the correct document first, across 5 distinct topics.
- Finding a chunk from its own first 300 characters: 97% ranked first, 99% in the top 5 (100 sampled chunks).

**Recall against exact search, with permission conditions (`ef_search=100`, `strict_order`):**

| Scenario | Recall@5 | Recall@10 | p50 ms | Plan chosen |
|---|---|---|---|---|
| Large admin, no filter | 0.976 | 0.981 | 5.9 | HNSW |
| Team member with 5 grants | 0.984 | 0.974 | 6.0 | HNSW |
| Large admin, department 5% | 1.000 | 1.000 | 4.9 | Exact |
| Small admin, no filter | 1.000 | 1.000 | 4.8 | Exact |

Exact search on the large tenant took 11.8 ms p50.

**Iterative scans, when a filtered search is forced onto HNSW:**

| Scenario | Off: Recall@10 | Off: rows of 10 | `strict_order` Recall@10 | `relaxed_order` Recall@10 |
|---|---|---|---|---|
| Large admin, department 5% | 0.365 | 3.65 | 0.958 | 0.989 |
| Small admin, no filter | 0.225 | 2.25 | 0.976 | 0.994 |

`strict_order` stays the default because it keeps results in exact distance order.

`EXPLAIN ANALYZE` confirmed the HNSW index scan for the large tenant (about 2 ms execution), with a different plan for the small tenant (0.85 ms). Full plans are in `workflow6_search_latency.json`.

**Limits of these numbers:**

- This is a small sample, so they do not predict behaviour at millions of vectors.
- `hnsw.max_scan_tuples` and `hnsw.scan_mem_multiplier` are still unvalidated.

## 7. Remaining issues

### From this workflow

1. **Former members keep their uploads indefinitely.** This is the rule you chose, but there is no API to remove a tenant member or deactivate a user. Today, ending that access means changing the database directly.
2. **Grants do not follow new versions.** A grant is on one document row. When a new version becomes searchable, a grantee of the old one stops finding the content in search and cannot read the new one until granted again. The same applies to an uploader who has left the team, if someone else uploads the new version.
3. **Text duplicates are searchable only through the original.** A document that reuses another's chunks has no vectors of its own. A grant on the duplicate alone finds nothing, and deleting the original removes that text from search.
4. **The first search after startup takes 7.5 s.** The API process now loads PyTorch and the model on first use. Loading at startup would move that delay, and memory use was not measured.
5. **Search has no rate limit.** Each request costs CPU for embedding.

### Requires a step from you

- **Your development database is not migrated.** It is at `c3e8a1f47d20`. Run `alembic upgrade head` before using this code, or grant and search queries will fail.
- **The API process now needs the model.** It reads from `./models`, which is not committed.

### Blocked

- **Search interface:** not built, as chosen. This is the one completion criterion in your spec that remains open.
- **Sections 4 and 5 of your brief:** missing, so nothing was built from them.

### Existing failures, left open (36)

Listed by name in `WORKFLOW5_REPORT.md`, section 7. Includes the two HIGH findings: `GET /users` and `POST /users` need no login.

## 8. Behaviour changes to existing features

- **Uploaders keep read access after leaving a team**, as you chose. Tests that used an uploader to prove removal cuts access now use a non-uploader, so they still prove the same rule.
- **Tenant document lists and job lists can now include granted or owned documents** from teams the user has not joined.
- **The `/jobs` and document read routes now use `readable_document_condition`**.

## 9. Changed files

**New:**

- `alembic/versions/d4b7f2a91c36_workflow_6_document_grants.py`
- `app/api/routes/search.py`
- `app/db/models/document_grant.py`
- `app/schemas/search.py`
- `app/services/documents/grants.py`
- `benchmarks/search_latency.py`
- `tests/unit/test_search_schema.py`
- `api_tests/test_k_search.py`
- `api_tests/test_l_grants.py`

**Modified:**

- `.env.example`
- `app/api/dependencies.py`
- `app/api/routes/documents.py`
- `app/core/config.py`
- `app/db/models/__init__.py`
- `app/db/models/document.py`
- `app/main.py`
- `app/schemas/document.py`
- `app/security/authorization.py`
- `app/services/documents/service.py`
- `app/services/embeddings/search.py`
- `benchmarks/hnsw_benchmark.py`
- `tests/integration/test_document_ingestion.py`
- `api_tests/report.py`
- `api_tests/support.py`
- `api_tests/test_a_schema.py`
- `api_tests/test_b_auth.py`
- `api_tests/test_g_bypass_lifecycle.py`
- `api_tests/test_h_roles.py`
- `api_tests/test_j_embeddings.py`
- `api_tests/test_z_real_stack.py`

Nothing has been committed, including Workflow 5.
