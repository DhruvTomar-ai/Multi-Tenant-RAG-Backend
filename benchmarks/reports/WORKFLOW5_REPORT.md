# Workflow 5 report: embeddings, metadata and HNSW

**Date:** 2026-11-04. **Status:** complete and verified, except frontend integration, which is blocked because the frontend files are empty.

Results marked *measured* come from executed runs. Results marked *estimate* are calculations from those measurements.

## 1. Test results (measured)

| Run | Result |
|---|---|
| Existing `tests/` plus new unit tests, SQLite | 203 of 203 passed |
| Existing `tests/` plus new unit tests, PostgreSQL | 203 of 203 passed |
| `api_tests/`, PostgreSQL with real migrations, model and server | 1,290 run: 1,254 passed, 36 failed, 0 errors, 0 skipped |

The 36 failures are exactly the 36 that failed before Workflow 5, compared by test name. No test is new to the failing list and none left it. `api_test_report.md` counts 1,288 test cases because 2 passing tests send no request and record no row.

| Area | Test cases | Passed | Failed |
|---|---|---|---|
| Embeddings and HNSW (new) | 64 | 64 | 0 |
| Document metadata (new) | 42 | 42 | 0 |
| Database isolation | 18 | 18 | 0 |
| Tenant isolation | 164 | 162 | 2 |
| Team isolation | 253 | 249 | 4 |
| Role and permission isolation | 178 | 177 | 1 |
| Authentication | 309 | 302 | 7 |
| Tenant and team APIs | 143 | 133 | 10 |
| Document and job APIs | 77 | 74 | 3 |
| User management | 12 | 5 | 7 |
| Real server (uvicorn, Redis, Celery) | 28 | 26 | 2 |

### Your isolation scenarios (all measured, all passed)

| Scenario | Result |
|---|---|
| Tenant A cannot retrieve Tenant B's metadata, chunks or embeddings | Refused, in both directions |
| A team cannot access another team's documents | Refused, for all 8 document read endpoints |
| Manipulating document IDs cannot retrieve embeddings | 403 or 404, with no data returned |
| Metadata filters cannot expose another tenant | Only the authorized set is returned, even with identical metadata |
| Inactive or deleted documents do not appear | Hidden from every read path, job lists, search and counts |
| Removing a user from a team cuts search access | Immediate |
| Vectors are never returned by metadata endpoints | Confirmed. Those queries do not read the vector column |

### Other verification (measured)

- **Migration:** downgrade and upgrade both succeed on a test database. Migrations match the models with no differences.
- **Development database:** backed up, then migrated to `c3e8a1f47d20`. Row counts match the earlier check (9 users, 4 tenants, 2 teams). It holds no documents, so it holds no vectors yet.
- **Actual index on development:** `USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')`, on `vector(384)`.
- **Backfill command:** run for real with a real worker and Redis. It queued 1 document, 2 of 2 chunks were embedded, and a second run queued 0.
- **Real stack:** an upload through uvicorn was ingested and embedded by a real Celery worker with the real model.

## 2. Chunk size

BGE reads at most 512 tokens and silently ignores the rest. The chunker counts tokens differently, so `CHUNK_SIZE` had to be measured with BGE's own tokenizer.

| `CHUNK_SIZE` | Longest real chunk | Real chunks over 512 | Longest stress chunk | Fits? |
|---|---|---|---|---|
| 512 (old) | 705 | 87.8% (sample of 1,000) | Not measured | No |
| 400 | 626 | 1.98% | 635 | No |
| 360 | 593 | 0.07% | 579 | No |
| 320 | 499 | 0 | 524 | Not for URL-heavy text |
| **300 (chosen)** | **498** | **0** | **494** | **Yes** |
| 280 | 446 | 0 | 458 | Yes |
| 240 | 396 | 0 | 377 | Yes |

The real text was about 75,800 paragraphs of package documentation. Stress text covered Chinese, number tables, source code, URLs, base64 and heavy punctuation. A permanent test now checks this with the real tokenizer.

## 3. Performance (measured)

**Sample:** 500 documents, 4,596 chunks, 4,596 embeddings. All 384 dimensions, none failed. The data is real text, on a CPU with 12 logical cores, using PostgreSQL 18.6, pgvector 0.8.7 and an in-memory database. There were 100 held-out queries per configuration.

| Measure | Result |
|---|---|
| Embedding time | 430.6 s |
| Throughput, including database writes | 10.7 embeddings/s |
| Model load | 7.2 s |
| HNSW index | `m=16`, `ef_construction=64`, cosine |
| Index size | 9.4 MB (2,050 bytes per vector) |
| Table size | 9.5 MB (2,057 bytes per vector) |
| Embeddings with all indexes | 19.7 MB (4,290 bytes per vector) |
| Full index rebuild | 0.5 s |

**Recall against exact search, with the shipped settings (`ef_search=100`, `strict_order`):**

| Tenant (vectors) | Filter | Recall@5 | Recall@10 | p50 ms | p95 ms | Plan chosen |
|---|---|---|---|---|---|---|
| Large (3,766) | None | 0.980 | 0.984 | 5.6 | 7.9 | HNSW |
| Large | Department, 50% of docs | 0.984 | 0.974 | 4.9 | 6.8 | HNSW |
| Large | Department, 5% of docs | 1.000 | 1.000 | 4.0 | 5.5 | Exact |
| Large | Custom field, 10% of docs | 1.000 | 1.000 | 4.8 | 6.1 | Exact |
| Medium (722) | None | 1.000 | 1.000 | 5.3 | 6.7 | Exact |
| Small (108) | None | 1.000 | 1.000 | 4.1 | 7.0 | Exact |

Exact search on the large tenant with no filter took 9.5 ms p50 and 14.2 ms p95.

**`hnsw.ef_search`, large tenant, no filter:**

| `ef_search` | Recall@5 | Recall@10 | p50 ms | p95 ms |
|---|---|---|---|---|
| 40 (pgvector default) | 0.928 | 0.931 | 4.2 | 5.7 |
| **100 (shipped)** | **0.980** | **0.984** | **5.6** | **7.9** |
| 200 | 0.992 | 0.995 | 6.5 | 10.3 |

**Iterative scans, when a filtered query walks the shared graph (forced onto HNSW, `ef_search=100`):**

| Case | Off: Recall@10 | Off: rows of 10 | `strict_order` Recall@10 | `relaxed_order` Recall@10 | p50 ms, off then strict |
|---|---|---|---|---|---|
| Large tenant, tags | 0.043 | 0.43 | 0.985 | 1.000 | 4.9, 11.2 |
| Large tenant, department 5% | 0.366 | 3.66 | 0.959 | 0.990 | 5.0, 6.3 |
| Medium tenant, department 5% | 0.067 | 0.67 | 0.986 | 1.000 | 4.1, 8.4 |
| Small tenant, no filter | 0.224 | 2.24 | 0.975 | 0.994 | 5.5, 7.4 |

**What this shows:** without iterative scanning, a filtered search over the shared index can return far fewer rows than requested. In the planner's own choice, medium tenant at `ef_search=40` with scanning off returned too few rows on 95 of 100 queries (Recall@10 0.538). Iterative scanning fixes this for about 1.3 to 2.3 times the latency, so it is on by default. `relaxed_order` scored higher than `strict_order`, but its results can be slightly out of distance order, so Workflow 6 would need to re-sort them.

### Not measured or too small to be meaningful

- **`hnsw.max_scan_tuples` and `hnsw.scan_mem_multiplier`:** the sweep used a scenario with no matching rows, so it measured latency but no recall. The table also has fewer rows than the default limit of 20,000, so the limit could not be reached. The settings are configurable and set through `SET LOCAL`, but the 20,000 and 1 defaults are unvalidated.
- **Scenarios with no matching rows:** small tenant with department 5%, small and medium tenants with the tag combination. They are in the JSON but have no recall value.
- **Large-scale behaviour:** with 4,596 vectors, exact search is already fast, and the planner often avoids HNSW. These numbers show relative behaviour, not performance at millions of vectors.

## 4. Scaling (estimates, assuming linear growth)

Storage depends on chunks, not documents. Per chunk: about 4.3 KB for the embedding and its indexes plus 1.5 KB for the chunk row.

| Chunks | Embeddings and indexes | Chunk rows | Total | HNSW index, ideally in RAM | Embedding time at 10.7/s |
|---|---|---|---|---|---|
| 100,000 | 0.43 GB | 0.15 GB | 0.6 GB | 0.2 GB | 2.6 hours |
| 1,000,000 | 4.3 GB | 1.5 GB | 5.8 GB | 2 GB | 26 hours |
| 10,000,000 | 43 GB | 15 GB | 58 GB | 20 GB | 11 days |

There is no document-count limit in the code. Upload size stays configurable through `MAX_UPLOAD_SIZE_MB`.

## 5. Partitioning: not recommended yet

The shared index is sufficient at this scale. Small tenants already avoid the graph, because the planner chooses the tenant index and an exact sort, which gave recall of 1.0. Iterative scanning covers the rest.

Partitioning `embeddings` by tenant would give each partition its own smaller HNSW graph, but it has real costs:

- **Migration:** the primary key and unique constraint must include `tenant_id`, and existing data must be copied into a new table with a maintenance window.
- **Operations:** hash partitions are fixed in advance, while one partition per tenant means tables and indexes multiply and must be managed as tenants join.
- **Performance:** queries must always include `tenant_id` to prune partitions, and memory for many indexes is hard to predict.

Consider it if one tenant reaches millions of vectors and filtered recall or latency measurably degrades, or if tenants need physical separation. Try a partial HNSW index for that one tenant first, because it needs no table rewrite.

## 6. Decisions to review

- **`access_level` is a label only.** It can be filtered on but does not change who can read a document. Team membership stays the only rule. Making `restricted` enforce anything would be a new authorization feature.
- **`is_active` and `deleted_at` are honoured but nothing sets them yet.** Every read path respects them and tests set them directly, but no endpoint changes them. There is no delete endpoint.
- **Added beyond your endpoint list:** `POST .../embeddings/retry` and `python -m app.workers.backfill_embeddings`, because you required retry support and existing documents need a way to be queued.
- **HNSW status needs `tenant.manage` and hides index size.** The index is shared, so its size reveals platform-wide volume.
- **CPU-only PyTorch.** Your disk had about 10 GB free, and it now has 9 GB free.

## 7. Open issues

**Blocked:** frontend integration, because all frontend files are 0 bytes.

**Chunk limit margin:** 300 fits everything measured, with a margin of only 14 tokens on real text. Unusual documents could still exceed 512, and nothing detects that at runtime. Making the splitter count with BGE's tokenizer would remove the risk.

**Requires a step from you:** restart any running Celery worker to pick up the new task. New environments must install the new dependencies and will download the model (129 MB) into `./models`, which is not committed.

**Existing failures, left open as you asked (36):**

| Finding | Tests |
|---|---|
| HIGH: `GET /users` needs no login (4) | `test_every_route_rejects_a_missing_token[GET /users]`, `test_anonymous_cannot_list_users`, `test_member_cannot_see_other_tenants_users`, `test_real_server_user_list_requires_a_login` |
| HIGH: `POST /users` needs no login (3) | `test_every_route_rejects_a_missing_token[POST /users]`, `test_anonymous_cannot_create_users`, `test_squatted_email_does_not_lock_out_the_real_owner` |
| MEDIUM: managers can change higher or equal roles (3) | `test_manager_cannot_demote_a_higher_role`, `test_manager_cannot_remove_a_higher_role`, `test_manager_cannot_downgrade_another_manager` |
| MEDIUM: email case creates a second account (1) | `test_register_duplicate_email_in_different_case_is_rejected` |
| MEDIUM: deactivated tenant cannot be reactivated (1) | `test_inactive_tenant_can_be_reactivated_by_its_admin` |
| MEDIUM: the only admin can demote themselves (1) | `test_last_tenant_admin_cannot_be_demoted` |
| MEDIUM: `POST /users` accounts crash login (1) | `test_account_from_users_route_can_log_in_or_fails_cleanly` |
| LOW: inactive teams visible to members (3) | `test_inactive_team_details_are_hidden_from_members`, `test_inactive_team_members_are_hidden_from_members`, `test_inactive_team_is_not_listed_for_members` |
| LOW: inactive tenants listed and selectable (2) | `test_inactive_tenant_is_not_listed`, `test_select_inactive_tenant_is_denied` |
| LOW: responses reveal what exists (3) | `test_real_and_missing_tenant_look_identical`, `test_add_member_does_not_reveal_which_outside_emails_exist`, `test_team_existence_is_not_revealed_inside_a_tenant` |
| LOW: passwords cut at 72 bytes (1) | `test_password_longer_than_72_bytes_is_not_silently_truncated` |
| LOW: bad input returns 500 (13) | `test_duplicate_email_is_handled`, `test_duplicate_username_is_handled`, `test_real_server_returns_500_for_a_duplicate_on_users_route`, `test_patch_tenant_duplicate_slug`, `test_patch_team_duplicate_name`, `test_create_tenant_with_too_long_description`, `test_create_team_with_too_long_description`, `test_register_name_with_nul_character_is_rejected_cleanly`, `test_create_tenant_with_nul_character`, `test_out_of_range_document_id_does_not_crash`, `test_out_of_range_job_id_does_not_crash`, `test_out_of_range_tenant_id_does_not_crash`, `test_signed_token_with_non_numeric_subject_is_a_401` |

Full explanations and fixes are in `api_tests/reports/SECURITY_TEST_REPORT.md`. Its test totals predate Workflow 5.

## 8. Changed files

**Application:** `app/api/dependencies.py`, `app/api/routes/documents.py`, `app/core/config.py`, `app/db/models/__init__.py`, `app/db/models/document.py`, `app/db/models/document_chunk.py`, `app/main.py`, `app/schemas/document.py`, `app/services/documents/errors.py`, `app/services/documents/ingestion.py`, `app/services/documents/service.py`, `app/services/documents/task_queue.py`, `app/workers/celery_app.py`, `app/workers/tasks.py`

**New application files:** `alembic/versions/c3e8a1f47d20_workflow_5_embeddings_and_metadata.py`, `app/api/routes/embeddings.py`, `app/db/models/embedding.py`, `app/services/documents/metadata.py`, `app/services/embeddings/__init__.py`, `app/services/embeddings/model.py`, `app/services/embeddings/service.py`, `app/services/embeddings/search.py`, `app/workers/backfill_embeddings.py`

**Configuration:** `.env.example`, `pyproject.toml`. `.env` was not changed.

**Tests:** `tests/conftest.py`, `tests/unit/test_embedding_model.py`, `tests/unit/test_document_metadata.py`, and in `api_tests/`: `conftest.py`, `support.py`, `report.py`, `test_a_schema.py`, `test_b_auth.py`, `test_h_roles.py`, `test_z_real_stack.py`, `test_i_metadata.py`, `test_j_embeddings.py`, plus regenerated reports

**Benchmarks:** `benchmarks/__init__.py`, `benchmarks/hnsw_benchmark.py`, `benchmarks/chunk_size_check.py`, `benchmarks/reports/`

Nothing has been committed.
