# Multi-tenant RAG backend: API and isolation test results

**Run date:** 2026-11-04. **Result:** 968 test cases executed, 932 passed, 36 failed, 0 errors, 0 skipped.

**Environment:** dedicated `pgvector/pgvector:pg18` PostgreSQL built by the real Alembic migrations, plus a dedicated Redis. Most tests ran the real app in-process through FastAPI's HTTPX-based `TestClient`. 18 tests ran over real HTTP against uvicorn and a real Celery worker. The development database and Redis were never used, and a guard in `conftest.py` refuses to run against them.

**Full results:** `api_test_report.md` holds the API inventory and all 1048 checks with expected and actual results. `api_test_report.csv` and `.json` hold the same data.

## 1. Totals

| Measure | Result |
|---|---|
| APIs discovered | 30 |
| APIs tested | 30 |
| Test cases executed | 968 |
| Passed | 932 |
| Failed | 36 |
| Skipped | 0 |
| Existing `tests/` suite, separately | 146 of 146 passed on SQLite, 146 of 146 on PostgreSQL |

| Area | Test cases | Passed | Failed |
|---|---|---|---|
| Authentication | 251 | 244 | 7 |
| Tenant isolation | 124 | 122 | 2 |
| Team isolation | 182 | 178 | 4 |
| Role and permission isolation | 146 | 145 | 1 |
| Database isolation | 18 | 18 | 0 |
| Tenant and team APIs | 143 | 133 | 10 |
| Document and job APIs | 74 | 71 | 3 |
| User management | 12 | 5 | 7 |
| Real server (uvicorn, Redis, Celery) | 18 | 16 | 2 |

## 2. Isolation verdict

| Question | Executed result |
|---|---|
| Can a user read another tenant's documents, text, chunks, downloads or jobs? | **No.** All matrix cases passed, in both directions |
| Can a user read another team's documents, text, chunks, downloads or jobs? | **No.** All matrix cases passed |
| Can a user upload into another tenant or team? | **No.** All 15 cases were refused and stored nothing |
| Can changing IDs in the URL, header, query or body bypass isolation? | **No.** All such tests passed |
| Does removal from a team apply immediately to an existing token? | **Yes**, in-process and on the real server |
| Do refused requests change data? | **No.** A hash of every table and the stored file list was identical after 20 refused writes |
| Is every user's data protected from anonymous callers? | **No.** `GET /api/v1/users` returns every user in every tenant (HIGH-1) |

The 2 tenant and 4 team failures above are not boundary crossings: 5 are lifecycle gaps on inactive tenants and teams, and 1 reveals whether a team ID exists inside the user's own tenant.

The APIs for embeddings and conversations do not exist, so they were not tested.

## 3. Permission matrix under test

Taken from `DEFAULT_ROLE_PERMISSIONS` in `app/security/permissions.py`. The role tests compute their expected results from this constant. Tests also confirmed that new tenants and the migration seed hold exactly these permissions.

| Permission | admin | manager | member | viewer | Used by |
|---|---|---|---|---|---|
| `tenant.manage` | Yes | No | No | No | Update tenant, add or list tenant members, create team |
| `team.access_all` | Yes | No | No | No | A tenant role reaching into teams it has not joined |
| `team.manage` | Yes | Yes | No | No | Update team, add or remove team members |
| `document.upload` | Yes | Yes | Yes | No | Upload |
| `document.read` | Yes | Yes | Yes | Yes | List, read, download, text, chunks, jobs |
| `document.delete` | Yes | Yes | No | No | No endpoint uses it |

Role rules confirmed by execution:

- A tenant role gives team access only with `team.access_all`.
- A team role gives no tenant-level rights, even a team-level `admin`.
- Creating a tenant needs only a login. That is how the code is written, so it was tested as the implemented rule.

## 4. Findings from executed tests

All 36 failures are listed below under one finding each. No CRITICAL findings were found.

### HIGH-1: Anyone can list every user in every tenant (security vulnerability)

- **Tested:** `GET /api/v1/users` with no token, and with Mona's token. Also confirmed on the real server.
- **Why it failed:** the route has no authentication or tenant scoping. Expected 401; got 200 with all 11 users, including Bob's email when requested by Mona.
- **Responsible:** `app/api/routes/users.py`, `list_users`.
- **Fix:** remove the route, or require a platform-admin permission. Tenant views already exist through `list_tenant_members`.
- **Failed tests (4):** `test_every_route_rejects_a_missing_token[GET /users]`, `test_anonymous_cannot_list_users`, `test_member_cannot_see_other_tenants_users`, `test_real_server_user_list_requires_a_login`.

### HIGH-2: Anyone can create accounts and block email addresses (security vulnerability)

- **Tested:** `POST /api/v1/users` with no token, then registering the same email through `/auth/register`.
- **Why it failed:** no authentication. Expected 401; got 201. The real owner's registration then gets 400 "Email already registered".
- **Responsible:** `app/api/routes/users.py`, `create_user`.
- **Fix:** delete this route. `/auth/register` already creates users.
- **Failed tests (3):** `test_every_route_rejects_a_missing_token[POST /users]`, `test_anonymous_cannot_create_users`, `test_squatted_email_does_not_lock_out_the_real_owner`.

### MEDIUM-1: A team manager can downgrade or remove a team admin or another manager (security vulnerability)

- **Tested:** Mike (manager) changing Adam (team admin) to viewer, removing Adam, and changing another manager to viewer.
- **Why it failed:** only the role being granted is checked, never the target's current role. Expected 403; got 201 and 204.
- **Responsible:** `app/services/tenants/service.py`, `assign_user_to_team` and `remove_user_from_team`.
- **Fix:** refuse the change when the target's current permissions exceed the acting user's.
- **Failed tests (3):** `test_manager_cannot_demote_a_higher_role`, `test_manager_cannot_remove_a_higher_role`, `test_manager_cannot_downgrade_another_manager`.

### MEDIUM-2: The same email can register twice with different capitalisation (security vulnerability)

- **Tested:** registering `case@example.com`, then `CASE@example.com`. Expected 400; got 201.
- **Why it matters:** an admin adding someone by email can reach the wrong account.
- **Responsible:** `app/services/users/service.py`, `get_by_email`, and the email column's case-sensitive unique constraint.
- **Fix:** normalize emails to lowercase on write and lookup, and migrate existing rows.
- **Failed test (1):** `test_register_duplicate_email_in_different_case_is_rejected`.

### MEDIUM-3: Deactivating a tenant cannot be undone (functional bug)

- **Tested:** Alice reactivating her deactivated tenant. Expected 200; got 404.
- **Why it failed:** `update_tenant` requires membership in an active tenant, so no one qualifies once it is inactive.
- **Responsible:** `app/api/routes/tenants.py`, `update_tenant`, through `AuthorizationService.is_tenant_member`.
- **Fix:** let tenant admins reach reactivation, in the same way `can_manage_team` uses `active_only=False`.
- **Failed test (1):** `test_inactive_tenant_can_be_reactivated_by_its_admin`.

### MEDIUM-4: The only tenant admin can demote themselves (functional bug)

- **Tested:** Alice, the only admin, setting her own role to member. Expected 400 or 409; got 201.
- **Why it matters:** no one can manage the tenant afterwards, and no API can recover it.
- **Responsible:** `app/services/tenants/service.py`, `add_tenant_member`.
- **Fix:** refuse to remove the last admin.
- **Failed test (1):** `test_last_tenant_admin_cannot_be_demoted`.

### MEDIUM-5: Accounts created through `POST /users` crash login (functional bug)

- **Tested:** logging in with an account from `POST /users`. Expected 200 or 401; got 500 `UnknownHashError`.
- **Why it failed:** the route throws the password away and stores `"placeholder_hash"`.
- **Responsible:** `app/api/routes/users.py`, `create_user`, and `app/services/auth/service.py`, `authenticate_user`.
- **Fix:** deleting the route (HIGH-2) removes the cause. Also delete any existing rows holding this value.
- **Failed test (1):** `test_account_from_users_route_can_log_in_or_fails_cleanly`.

### LOW-1: Inactive teams stay visible to their members (security gap)

- **Tested:** Mona reading an inactive team's details, member list and team list entry. Expected denied or hidden; got 200 with the data, including colleagues' emails.
- **Why it failed:** document routes use `has_team_permission`, which checks `Team.is_active`. The team metadata routes do not, so the code is inconsistent with itself. Documents and jobs in inactive teams were correctly blocked.
- **Responsible:** `app/security/authorization.py`, `is_team_member`; `app/services/tenants/service.py`, `can_access_team` and `list_user_teams`.
- **Fix:** apply the active check for non-admins.
- **Failed tests (3):** `test_inactive_team_details_are_hidden_from_members`, `test_inactive_team_members_are_hidden_from_members`, `test_inactive_team_is_not_listed_for_members`.

### LOW-2: Inactive tenants can still be listed and selected (security gap)

- **Tested:** `GET /tenants` and `POST /auth/tenant/select` on an inactive tenant. Data routes in that tenant were correctly blocked, but these returned it.
- **Responsible:** `app/services/tenants/service.py`, `get_user_tenants`; `app/api/routes/auth.py`, `select_active_tenant`.
- **Fix:** filter on `Tenant.is_active`, or use `is_tenant_member`.
- **Failed tests (2):** `test_inactive_tenant_is_not_listed`, `test_select_inactive_tenant_is_denied`.

### LOW-3: Responses reveal what exists (security gap)

- **Tested and failed:** `GET /tenants/{id}` returns different messages for a real and a missing tenant. Adding a member returns 400 for a registered outside email and 404 for an unknown one. A team you cannot see returns 403, and a missing team returns 404.
- **Responsible:** `app/api/routes/tenants.py`, `get_tenant` and `add_tenant_member`; `app/api/dependencies.py`, `require_team_permission`.
- **Fix:** use one message for both tenant cases, validate the role before looking up the email, and return 404 for teams the user cannot see.
- **Failed tests (3):** `test_real_and_missing_tenant_look_identical`, `test_add_member_does_not_reveal_which_outside_emails_exist`, `test_team_existence_is_not_revealed_inside_a_tenant`.

### LOW-4: Passwords are silently cut at 72 bytes (security gap)

- **Tested:** a password with the same first 72 bytes and a different ending logged in. Expected 401; got 200.
- **Responsible:** `app/schemas/auth.py` allows 255 characters, while bcrypt uses only 72 bytes.
- **Fix:** reject passwords over 72 bytes at validation.
- **Failed test (1):** `test_password_longer_than_72_bytes_is_not_silently_truncated`.

### LOW-5: Invalid input causes 500 errors instead of a clean rejection (functional bug)

No failure in this group leaked data or changed another tenant's records. Registration and `POST /users` can trigger these without logging in.

| Trigger | Actual error | Responsible | Fix | Failed tests |
|---|---|---|---|---|
| Duplicate email or username on `POST /users` | `IntegrityError` | `users.py`, `create_user` | Delete the route | `test_duplicate_email_is_handled`, `test_duplicate_username_is_handled`, `test_real_server_returns_500_for_a_duplicate_on_users_route` |
| Duplicate slug or team name on `PATCH` | `IntegrityError` | `tenants.py`, `update_tenant` and `update_team` | Catch it and return 400, as the create routes do | `test_patch_tenant_duplicate_slug`, `test_patch_team_duplicate_name` |
| Description over 1000 characters | `DataError` | `schemas/tenant.py`, `schemas/team.py` | Add `max_length=1000` | `test_create_tenant_with_too_long_description`, `test_create_team_with_too_long_description` |
| NUL character in a name | `DataError` | `schemas/auth.py`, `schemas/tenant.py` | Reject NUL at validation | `test_register_name_with_nul_character_is_rejected_cleanly`, `test_create_tenant_with_nul_character` |
| 20-digit ID in a URL | `NumericValueOutOfRange` | Path parameters in `documents.py`, `jobs.py`, `dependencies.py` | Bound IDs to the integer range | `test_out_of_range_document_id_does_not_crash`, `test_out_of_range_job_id_does_not_crash`, `test_out_of_range_tenant_id_does_not_crash` (each with the value 99999999999999999999) |
| Correctly signed token with a non-numeric `sub` | `ValueError` | `dependencies.py`, `get_current_user` | Treat it as 401. Needs the signing key to trigger | `test_signed_token_with_non_numeric_subject_is_a_401` |

## 5. Code-review findings (not pass/fail results)

These concern features that do not exist, so they are outside the test scope. An earlier run, before tests for missing features were removed, showed 25 failed logins with no throttling and an old refresh token still accepted after use.

| Severity | Finding | Location |
|---|---|---|
| HIGH | No email verification. Registering someone else's address, then being added to a tenant by that email, gives real access | `routes/auth.py`, `register`; `routes/tenants.py`, `add_tenant_member` |
| MEDIUM | Both JWT secrets in your local `.env` contain readable words and don't have the recommended generated format. This matters if they are ever reused outside local development. These tests used their own secrets | `.env` |
| MEDIUM | No login rate limiting or lockout | `routes/auth.py`, `login` |
| MEDIUM | Refresh tokens cannot be revoked and there is no logout | `routes/auth.py`, `refresh_token` |
| MEDIUM | Tenant membership cannot be removed, and users cannot be deactivated, through the API | `routes/tenants.py`, `routes/users.py` |
| LOW | Documents cannot be deleted, although `document.delete` exists | `routes/documents.py` |
| LOW | No row-level security, so reads rely entirely on application filters | Migrations |
| LOW | Multipart bodies are parsed before login is checked | FastAPI behaviour on `upload_document` |
| LOW | `get_current_tenant` and `get_optional_current_user` are unused | `api/dependencies.py` |

## 6. Missing test coverage

- A user who belongs to two tenants at once. Every test user here belongs to one.
- Tokens that expire during real use. Expired tokens were built with a past expiry.
- Real Celery retries and worker crashes. Failure paths ran in-process, while the real worker only processed successful jobs.
- Concurrency, such as simultaneous identical uploads or an upload racing a removal.
- Migration from an older database with existing data, and downgrades. Migrations ran on an empty database only.
- Very large uploads over real HTTP. The size limit was tested with the limit lowered to 1 MB.
- Password-protected or malformed PDFs in the worker, and zip-bomb DOCX files.
- Load and performance.

## 7. Basis for expected results

Most expected results come directly from the code or from your brief. These are my judgment, because the code states no rule for them, so you may decide some behaviour is acceptable:

- Treating email addresses as case-insensitive.
- Keeping at least one tenant admin.
- Hiding inactive teams and tenants from listings.
- Giving identical responses for hidden and missing resources.
- Rejecting passwords over 72 bytes.

The test accounts were created fresh through the API, because the testing guide is not in the repository.
