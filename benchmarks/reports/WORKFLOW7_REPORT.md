# Workflow 7: RAG chat and Groq integration

Status: complete and verified in the test environment. Not yet usable in the development environment until the three steps in section 1 are done.

All results below come from executed runs on 2026-11-04. Nothing from this workflow is committed.

## 1. Before chat works in development

1. Change `.env` to a model this key can use. The configured `GROQ_MODEL=llama-3.1-8b-instant` returns `404 model_not_found`, so every question that reaches Groq currently fails with a safe 502. Groq's deprecations page lists a shutdown date of 2026-08-16 for it, and it is not in this key's model list.
   Suggested line: `GROQ_MODEL=openai/gpt-oss-120b`
2. Run `pip install -e .` if `groq` is missing, then `alembic upgrade head`. The development database is still at `c3e8a1f47d20`, so this applies both the Workflow 6 and Workflow 7 migrations.
3. Restart the API and workers.

## 2. Files

New application files:

- `alembic/versions/e5c9a3d7b210_workflow_7_conversations_and_messages.py`
- `app/api/routes/chat.py`
- `app/db/models/conversation.py`
- `app/schemas/chat.py`
- `app/services/rag/__init__.py`, `citations.py`, `context.py`, `conversations.py`, `groq_client.py`, `prompts.py`, `service.py`

Changed application files:

- `app/core/config.py`, `app/main.py`, `app/db/models/__init__.py`
- `app/services/embeddings/search.py` (adds `page_label` and chunk metadata to search hits)
- `.env.example`, `pyproject.toml`

New test and benchmark files:

- `api_tests/test_m_chat.py`, `api_tests/test_n_groq_live.py`, `tests/unit/test_rag_units.py`
- `benchmarks/relevance_threshold.py`, `benchmarks/reports/relevance_threshold.json`, this report

Changed test files:

- `api_tests/conftest.py`, `api_tests/report.py`, `api_tests/support.py`, `api_tests/test_a_schema.py`, `api_tests/test_b_auth.py`, `api_tests/test_z_real_stack.py`, `tests/conftest.py`
- `api_tests/reports/api_test_report.{csv,json,md}` (regenerated)

## 3. Groq configuration

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | none, required | Held as a `SecretStr`. Only read from the environment. |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Model name |
| `GROQ_TEMPERATURE` | `0.1` | |
| `GROQ_MAX_TOKENS` | `2048` | |
| `GROQ_TIMEOUT_SECONDS` | `30` | Per attempt |
| `GROQ_MAX_RETRIES` | `2` | Transient failures only |
| `RAG_TOP_K` | `5` | Chunks retrieved |
| `RAG_MIN_SIMILARITY` | `0.50` | Relevance threshold, see section 8 |
| `RAG_MAX_CONTEXT_TOKENS` | `3000` | Context limit (estimated tokens) |
| `RAG_HISTORY_MESSAGES` | `6` | History window |
| `RAG_MAX_HISTORY_TOKENS` | `1000` | History limit (estimated tokens) |
| `RAG_RETRIEVAL_HISTORY_QUESTIONS` | `1` | Earlier questions added to a follow-up's search |

Only `GROQ_API_KEY` is required. One Groq client is created and reused. The embedding model is unchanged.

## 4. Endpoints

The tenant stays in the path to match the existing routes, which differs from the suggested `/api/v1/chat/...`. All routes sit under `/api/v1/tenants/{tenant_id}/chat/conversations`.

| Method and path | Purpose |
|---|---|
| `POST /` | Create a conversation |
| `GET /` | List your own conversations |
| `GET /{conversation_id}` | Conversation details |
| `PATCH /{conversation_id}` | Rename or archive (added beyond the brief) |
| `GET /{conversation_id}/messages` | History, paginated |
| `POST /{conversation_id}/messages` | Ask a question |

Error responses when asking:

| Situation | Status | `code` |
|---|---|---|
| Key missing | 503 | `llm_not_configured` |
| Key rejected | 503 | `llm_authentication_failed` |
| Rate limited | 429, with `Retry-After` | `llm_rate_limited` |
| Timeout | 504 | `llm_timeout` |
| Groq service failure | 503 | `llm_unavailable` |
| Retired model or rejected request | 502 | `llm_error` |
| Unusable, cut-short or filtered reply | 502 | `llm_bad_response` |
| Retrieval or database failure while answering | 502 | `llm_error`, saved with reason `internal_error` |
| Archived conversation, or same `client_message_id` in progress or with different text | 409 | |

## 5. Real example

From the live run on `openai/gpt-oss-120b`. Timestamps and null fields are trimmed.

Request: `POST /api/v1/tenants/{tenant_id}/chat/conversations/1/messages`

```json
{ "content": "How many days of annual leave do employees get?" }
```

Optional body fields: `team_ids`, `document_ids`, `filters`, `client_message_id`.

Response (200):

```json
{
  "conversation_id": 1,
  "status": "completed",
  "replayed": false,
  "user_message": { "id": 1, "role": "user", "content": "How many days of annual leave do employees get?", "status": "completed" },
  "assistant_message": {
    "id": 2,
    "role": "assistant",
    "content": "Employees receive 24 days of paid annual leave each year [chunk:1].",
    "status": "completed",
    "reply_to_message_id": 1,
    "citations": [{
      "chunk_id": 1, "chunk_index": 0, "document_id": 4, "document_title": "leave.txt", "team_id": 1,
      "similarity_score": 0.867641, "page_label": null,
      "metadata": { "version": 1, "file_type": ".txt" }
    }],
    "sources": [{ "document_id": 4, "document_title": "leave.txt", "chunk_ids": [1] }],
    "content_hidden": false,
    "llm_model": "openai/gpt-oss-120b"
  }
}
```

An unanswerable question is also a 200, with `"status": "insufficient_evidence"`, the fixed wording from the brief, and empty `citations` and `sources` on `assistant_message`.

## 6. Flow

1. The route checks the token, tenant membership, conversation ownership and team scope.
2. The question and a `pending` answer row are committed, so no transaction stays open during slow calls and a crash cannot leave an answer marked completed.
3. `search_text` from Workflow 6 runs fresh for every question, so document access is rechecked each time. A follow-up adds the previous question to the search only when its wording depends on it. Earlier answers are never searched.
4. No hits, or none above the threshold: the answer is saved as insufficient evidence and Groq is not called.
5. Passages are deduplicated by chunk ID and normalized text, and limited by token budget. Document text is escaped and sent as untrusted data, never in the system message.
6. History comes from this conversation only, is bounded, and leaves out failed, insufficient and hidden turns.
7. Groq is called in JSON mode. A reply that stopped early is treated as a failure.
8. Citations are validated, and the final status is saved.
9. Current access is applied to the answer before it is returned.

## 7. Citation validation

- The model returns `cited_chunk_ids` and `[chunk:ID]` markers.
- The backend keeps only IDs from this request's passages. Other IDs and their markers are removed, including real chunks the user cannot read.
- Title, document ID, team, score and metadata come from the retrieved row, never from the model.
- `page_label` appears only if the file recorded one.
- An answer left with no valid citation is not returned (`no_valid_citations`).

## 8. Insufficient evidence

Each case stores its own `status_reason`:

| Reason | Meaning | Groq called |
|---|---|---|
| `no_results` | Nothing retrieved | No |
| `low_relevance` | Retrieved, but all below the threshold | No |
| `model_insufficient_evidence` | The model declined | Yes |
| `no_valid_citations` | The answer had no valid citation | Yes |

`similarity_score` is 1 minus cosine distance. The threshold was measured with the real embedding model on 24 answerable and 24 unrelated questions against short passages (`benchmarks/reports/relevance_threshold.json`):

| Questions | Lowest | Median | Highest |
|---|---|---|---|
| Answerable | 0.627 | 0.747 | 0.850 |
| Unrelated | 0.310 | 0.435 | 0.542 |

0.50 keeps 24 of 24 answerable questions and blocks 22 of 24 unrelated ones. 0.55 blocked all 24, but real chunks are longer and likely score lower, so the lower value was chosen and the LLM acts as a second gate.

## 9. Persistence

- `conversations`: tenant, creator, optional team, title, active or archived status, timestamps.
- `messages`: conversation, tenant, role, content, status, reason, reply link, `client_message_id`, citations and context document IDs as JSONB, model, timestamps.
- Composite foreign keys stop a message or team crossing tenants at the database level. A question can have only one answer.
- The migration only adds tables. The downgrade and upgrade round trip passed.
- Retries: sending the same `client_message_id` returns the saved answer without a second Groq call. A failed or abandoned answer can be taken over by one request.

## 10. Security decisions and results

- Conversations are private to their creator, including from tenant admins. No sharing model was added.
- Team scope only narrows retrieval, is checked at creation and on every message, and cannot be widened.
- Another user's conversation and a missing one return the same 404. A tenant the user does not belong to returns 403.
- If any document behind an answer becomes unreadable, the answer and its citations are hidden with `content_hidden: true`. They come back if access returns. Hidden answers are never sent to Groq and cannot be replayed through a retry.

All of these passed on real PostgreSQL:

- Cross-user, cross-tenant and wrong-path access, for each chat route.
- Identity fields in a request are refused.
- Filters cannot reach other teams or tenants.
- Inactive membership blocks chat.
- Loss of access by team removal, revoked grant and document lifecycle changes.
- Only authorized text reaches Groq.
- No raw vectors or secrets appear in responses, and the key does not appear in logs or error bodies.

## 11. Test results

| Run | Passed | Failed | Skipped |
|---|---|---|---|
| Existing tests (`tests/`) on SQLite | 285 | 0 | 0 |
| Same tests on PostgreSQL | 285 | 0 | 0 |
| Full API suite (`api_tests/`) | 1672 | 36 | 4 live tests, no key in that run |
| Chat tests rerun with 2 new database-failure tests | 144 | 0 | 0 |
| Live Groq, `openai/gpt-oss-20b` | 5 | 0 | 0 |
| Live Groq, `openai/gpt-oss-120b` | 5 | 0 | 0 |

Distinct tests without Groq: 1,995 (285 + 1,710). 1,959 passed and 36 failed.

- **The 36 failures:** compared by name with the previous run: no new failures and none fixed. No chat test is among them. They are the known ones from `WORKFLOW5_REPORT.md`, section 7.
- **Timing of the last changes:** after the full run I added only tests: 2 database tests, 1 live test. No application code changed, and the full suite was not repeated.
- **Report counts:** `api_test_report.md` shows 1,710 cases and 1,670 passed. The gap to 1,672 is 2 tests that send no HTTP request, so the report does not record them. The saved report also predates the 2 new database tests and the new live test.

### Scenario coverage

Mocked Groq unless noted. The mock sits at the HTTP layer, so the real SDK still runs.

| # | Scenario | Test evidence | Result |
|---|---|---|---|
| 1 | Successful response | `test_1_successful_groq_response`, live | Pass |
| 2 | Missing key | `test_2_missing_key_*` | Pass |
| 3 | Invalid key | `test_groq_failures_*[3_invalid_key]` | Pass, mocked 401 only |
| 4 | Timeout | `test_4_groq_timeout_is_a_504` | Pass |
| 5 | Rate limit | `test_groq_failures_*[5_rate_limit]` | Pass, mocked only |
| 6 | Service failure | `test_groq_failures_*[6_*]` | Pass |
| 7 | Retrieval through Workflow 6 | `test_7_and_8_*` | Pass |
| 8 | Context reaches Groq | `test_7_and_8_*`, `test_8_*` | Pass |
| 9 | Answer uses context | `test_9_*`, live | Pass |
| 10 | No relevant chunks | `test_10_*` | Pass |
| 11 | Unrelated context | `test_11_*`, off-topic test, live decline | Pass |
| 12 | Prompt injection | `test_12_*`, live | Pass, one attack on each model |
| 13 | Duplicate chunks | `test_13_*`, unit | Pass |
| 14 | Valid citations | `test_14_and_16_*`, live | Pass |
| 15 | Fabricated IDs | `test_15_*`, unit | Pass |
| 16 | Correct documents | `test_14_and_16_*`, live | Pass |
| 17 | Unauthorized sources | `test_17_*` | Pass |
| 18 | Creation | `test_18_*` | Pass |
| 19 | Persistence | `test_19_*` | Pass |
| 20 | History | `test_20_*` | Pass |
| 21 | Follow-ups | `test_21_*`, live | Pass |
| 22 | Unauthorized access | `test_22_and_24_*` | Pass |
| 23 | Cross-tenant | `test_23_*` | Pass |
| 24 | Cross-user | `test_22_and_24_*` | Pass |
| 25 | Permissions rechecked | `test_25_*`, revoked grant, lifecycle | Pass |

### Live results

Both models gave correct, cited answers to the leave and expense questions, declined the contractors-in-Tokyo question, and ignored instructions planted in a document. Both also answered "How many of them can be carried forward?" correctly after an earlier question. No API key appeared in the captured replies or logs.

These tests cannot choose between the models. `gpt-oss-20b` gave the more precise expense answer ("within 30 days of travel"), and speed per model was not measured.

## 12. Limitations

No Workflow 7 bug is known to be open. One real design bug was found and fixed during testing: adding the previous question to every search let an off-topic question borrow relevance and reach Groq. Regression tests now cover it.

Evidence limits:

- Live checks are few: 5 tests on short synthetic documents and one injection attack. They show the integration works, not how often a model stays grounded on real documents.
- The live tests call the API in-process. The test on a real uvicorn server covers chat only with no key configured.
- An invalid key and a rate limit have only been tested with mocked responses.
- In the outage test, the in-process client substitutes its own generic 500, so its "no leak" check proves little. The row state check in that test is real.

Behaviour limits:

- The threshold was calibrated on short passages and should be rechecked on real documents. A vague question can exceed 0.50 against a very short document, and 2 of 24 unrelated questions passed it, so the LLM is the second gate there.
- Follow-up detection is English only. An off-topic question containing a word such as "it" also searches with the earlier question, leaving the LLM to decline.
- Token counts are estimates, not exact model counts.
- If the database fails while saving an answer and also while recording the failure, the row stays `pending`. A retry with the same `client_message_id` can take it over after about 150 seconds by default. Without that ID, the row stays `pending`.
- `[chunk:ID]` markers stay in the answer text for a frontend to render.
- The API has no streaming and no rate limiting on chat or search.

Carried over:

- 36 known failures remain open, including HIGH-severity unauthenticated access to the `/users` routes.
- From Workflow 6: the first search is cold (about 7.5 s), grants do not follow new document versions, former members keep their uploads with no API to remove tenant members, and text duplicates are searchable only through the original.
- Frontend work remains blocked and out of scope.
