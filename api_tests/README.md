# API and isolation test suite

Black-box API tests that run the real app against PostgreSQL with the real Alembic migrations.
Tests assert the secure behaviour, so a failure means the application fell short. Nothing is
marked xfail or skipped.

## Run

```bash
docker compose -p rag-api-test -f api_tests/docker-compose.test.yml up -d --wait

TEST_DATABASE_URL=postgresql+psycopg://rag_test:rag_test@127.0.0.1:5434/rag_api_test \
TEST_REDIS_URL=redis://127.0.0.1:6380/0 \
.venv/Scripts/python.exe -m pytest api_tests -p no:cacheprovider -q

docker compose -p rag-api-test -f api_tests/docker-compose.test.yml down -v
```

The run writes `api_tests/reports/api_test_report.md`, plus `.csv` and `.json` versions.

## Safety

`conftest.py` wipes the target database, so it refuses to start unless the database name contains
`test`, the port is not 5433, and the target differs from `DATABASE_URL` in `.env`. It also
overrides the JWT secrets, broker and document storage folder, so the run never uses the
development values.
