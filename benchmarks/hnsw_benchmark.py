"""Measures embedding throughput and HNSW search on a dedicated test database.

Everything reported is measured here. The corpus is real text (documentation from installed
packages), split by the real chunker and embedded by the real EmbeddingService with the real
model. Search goes through the same authorized statement Workflow 6 will use.

    TEST_DATABASE_URL=postgresql+psycopg://rag_test:rag_test@127.0.0.1:5434/rag_api_test \
    python -m benchmarks.hnsw_benchmark --chunks 20000 --queries 100

The target database is wiped, so the same guard as the test suite applies.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import random
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
URL = os.environ.get("TEST_DATABASE_URL")
if not URL:
    sys.exit("TEST_DATABASE_URL is not set.")
_target = make_url(URL)
_dev = dotenv_values(ROOT / ".env").get("DATABASE_URL")
_dev_url = make_url(_dev) if _dev else None
if "test" not in (_target.database or "") or _target.port == 5433 or (_dev_url and (_dev_url.port, _dev_url.database) == (_target.port, _target.database)):
    sys.exit("Refusing to run: this is not a dedicated test database.")

os.environ.update(
    DATABASE_URL=URL,
    JWT_SECRET_KEY="benchmark-secret-key-long-enough-for-hs256-signing",
    JWT_REFRESH_SECRET_KEY="benchmark-refresh-key-long-enough-for-hs256-signing",
    CELERY_BROKER_URL="redis://127.0.0.1:1/15",
    DOCUMENT_STORAGE_ROOT=tempfile.mkdtemp(prefix="rag-benchmark-"),
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402

import app.db.models  # noqa: E402,F401
from app.core.config import settings  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.db.models.document import Document  # noqa: E402
from app.db.models.document_chunk import DocumentChunk  # noqa: E402
from app.db.models.team import Team  # noqa: E402
from app.db.models.tenant import Tenant  # noqa: E402
from app.db.models.user import User  # noqa: E402
from app.db.models.user_team_membership import UserTeamMembership  # noqa: E402
from app.db.models.user_tenant_membership import UserTenantMembership  # noqa: E402
from app.security.permissions import seed_default_roles  # noqa: E402
from app.services.documents.processing import ExtractedSection, chunk_sections  # noqa: E402
from app.services.embeddings import model as embedding_model  # noqa: E402
from app.services.embeddings.search import SearchFilters, apply_hnsw_settings, build_search_statement, hnsw_index_status  # noqa: E402
from app.services.embeddings.service import EmbeddingService  # noqa: E402

REPORT_DIR = ROOT / "benchmarks" / "reports"
SEED = 20261104
# Share of documents per tenant. Team shares are within the tenant.
TENANTS = {
    "large": (0.80, {"t0": 0.5, "t1": 0.3, "t2": 0.15, "t3": 0.05}),
    "medium": (0.18, {"t0": 0.7, "t1": 0.3}),
    "small": (0.02, {"t0": 1.0}),
}
DEPARTMENTS = [("people", 0.5), ("engineering", 0.3), ("finance", 0.15), ("legal", 0.05)]
REGIONS = [("eu", 0.6), ("us", 0.3), ("apac", 0.1)]


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def rebuild_database() -> None:
    engine = create_engine(URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("CREATE EXTENSION vector"))
    engine.dispose()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")


def collect_paragraphs(limit_chars: int) -> list[str]:
    """Docstrings and prose from installed packages: real text with real topical variety."""
    site = ROOT / ".venv" / "Lib" / "site-packages"
    packages = sorted(p for p in site.iterdir() if p.is_dir() and not p.name.endswith(("dist-info", "__pycache__")))
    rng = random.Random(SEED)
    rng.shuffle(packages)
    seen: set[str] = set()
    paragraphs: list[str] = []
    total = 0
    for package in packages:
        for path in sorted(package.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except (SyntaxError, ValueError, RecursionError, MemoryError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node)
                    if not doc:
                        continue
                    for block in re.split(r"\n\s*\n", doc):
                        cleaned = " ".join(block.split())
                        if 120 <= len(cleaned) <= 3000 and cleaned not in seen and sum(ch.isalpha() for ch in cleaned) > 0.7 * len(cleaned):
                            seen.add(cleaned)
                            paragraphs.append(cleaned)
                            total += len(cleaned)
            if total >= limit_chars:
                rng.shuffle(paragraphs)
                return paragraphs
    rng.shuffle(paragraphs)
    return paragraphs


def weighted(rng: random.Random, items: list[tuple[str, float]]) -> str:
    return rng.choices([name for name, _ in items], [weight for _, weight in items])[0]


def build_world(session) -> dict:
    world: dict = {}
    for tenant_name, (_, teams) in TENANTS.items():
        tenant = Tenant(name=tenant_name, slug=f"bench-{tenant_name}")
        session.add(tenant)
        session.flush()
        roles = seed_default_roles(session, tenant.id)
        admin = User(email=f"admin@{tenant_name}.example", username=f"admin-{tenant_name}", password_hash="unused")
        session.add(admin)
        session.flush()
        session.add(UserTenantMembership(user_id=admin.id, tenant_id=tenant.id, role_id=roles["admin"].id))
        team_ids = {}
        members = {}
        for team_name in teams:
            team = Team(tenant_id=tenant.id, name=team_name)
            member = User(email=f"{team_name}@{tenant_name}.example", username=f"{team_name}-{tenant_name}", password_hash="unused")
            session.add_all([team, member])
            session.flush()
            session.add(UserTenantMembership(user_id=member.id, tenant_id=tenant.id, role_id=roles["member"].id))
            session.flush()
            session.add(UserTeamMembership(user_id=member.id, team_id=team.id, tenant_id=tenant.id, role_id=roles["member"].id))
            team_ids[team_name] = team.id
            members[team_name] = member.id
        world[tenant_name] = {"tenant_id": tenant.id, "admin_id": admin.id, "team_ids": team_ids, "members": members}
    session.commit()
    return world


def load_world(session) -> dict:
    """Rebuild the tenant details from the database when resuming."""
    world: dict = {}
    for tenant_name in TENANTS:
        tenant = session.scalar(select(Tenant).where(Tenant.slug == f"bench-{tenant_name}"))
        admin = session.scalar(select(User).where(User.email == f"admin@{tenant_name}.example"))
        teams = session.scalars(select(Team).where(Team.tenant_id == tenant.id)).all()
        world[tenant_name] = {"tenant_id": tenant.id, "admin_id": admin.id, "team_ids": {team.name: team.id for team in teams}, "members": {}}
    session.rollback()
    return world


def load_documents(session, world: dict, paragraphs: list[str], target_chunks: int, max_documents: int | None = None) -> tuple[list[int], list[str]]:
    """Group paragraphs into documents and split them with the real chunker."""
    rng = random.Random(SEED + 1)
    tenant_names = list(TENANTS)
    shares = [TENANTS[name][0] for name in tenant_names]
    document_ids: list[int] = []
    samples: list[str] = []
    chunk_total = 0
    position = 0
    index = 0
    while chunk_total < target_chunks and position < len(paragraphs) and (max_documents is None or index < max_documents):
        size = rng.randint(4, 60)
        body = paragraphs[position:position + size]
        position += size
        chunks = chunk_sections([ExtractedSection(text="\n\n".join(body))], chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
        tenant_name = rng.choices(tenant_names, shares)[0]
        team_name = rng.choices(list(TENANTS[tenant_name][1]), list(TENANTS[tenant_name][1].values()))[0]
        info = world[tenant_name]
        department = weighted(rng, DEPARTMENTS)
        region = weighted(rng, REGIONS)
        document = Document(
            tenant_id=info["tenant_id"],
            team_id=info["team_ids"][team_name],
            original_filename=f"doc-{index}.txt",
            stored_filename=f"doc-{index}.txt",
            content_type="text/plain",
            file_size=sum(len(p) for p in body),
            file_hash=f"{index:064x}",
            file_extension=".txt",
            status="completed",
            embedding_status="pending",
            chunk_count=len(chunks),
            metadata_json={},
            department=department,
            document_type=rng.choice(["policy", "contract", "report", "manual"]),
            tags=[department, region],
            custom_metadata={"region": region},
        )
        session.add(document)
        session.flush()
        session.add_all(
            DocumentChunk(document_id=document.id, tenant_id=document.tenant_id, team_id=document.team_id, chunk_index=i, chunk_text=chunk.text, chunk_hash=chunk.hash)
            for i, chunk in enumerate(chunks)
        )
        document_ids.append(document.id)
        if len(samples) < 2000:
            samples.extend(chunk.text for chunk in chunks[:2])
        chunk_total += len(chunks)
        index += 1
        if index % 50 == 0:
            session.commit()
            log(f"  {chunk_total} chunks in {index} documents")
    session.commit()
    return document_ids, samples


def measure_truncation(samples: list[str]) -> dict:
    model = embedding_model.get_model()
    tokenizer = model.tokenizer
    limit = model.max_seq_length
    lengths = [len(tokenizer.encode(text, add_special_tokens=True, truncation=False)) for text in samples[:1000]]
    over = sum(1 for length in lengths if length > limit)
    return {
        "sampled_chunks": len(lengths),
        "model_max_tokens": limit,
        "chunks_over_limit": over,
        "percent_over_limit": round(100 * over / len(lengths), 1) if lengths else 0,
        "max_tokens_seen": max(lengths, default=0),
        "mean_tokens": round(statistics.mean(lengths), 1) if lengths else 0,
    }


def explain(session, statement, analyze: bool = False) -> str:
    """EXPLAIN the exact statement with its real bound values, processed as SQLAlchemy would."""
    compiled = statement.compile(dialect=session.get_bind().dialect, compile_kwargs={"render_postcompile": True})
    processors = compiled._bind_processors
    params = {name: (processors[name](value) if name in processors else value) for name, value in compiled.params.items()}
    prefix = "EXPLAIN (ANALYZE, BUFFERS) " if analyze else "EXPLAIN "
    return "\n".join(row[0] for row in session.connection().exec_driver_sql(prefix + str(compiled), params))


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))] if ordered else 0.0


def run_query(session, statement, *, ef, mode, exact=False, force_hnsw=False, max_scan=None, mem=None):
    apply_hnsw_settings(session, ef_search=ef, iterative_scan=mode, max_scan_tuples=max_scan, scan_mem_multiplier=mem)
    if exact:
        session.execute(text("SET LOCAL enable_indexscan = off"))
        session.execute(text("SET LOCAL enable_indexonlyscan = off"))
    if force_hnsw:
        session.execute(text("SET LOCAL enable_seqscan = off"))
        session.execute(text("SET LOCAL enable_bitmapscan = off"))
        session.execute(text("SET LOCAL enable_sort = off"))
    start = time.perf_counter()
    rows = session.execute(statement).all()
    elapsed = (time.perf_counter() - start) * 1000
    return [row.embedding_id for row in rows], elapsed


def statement_for(user_id, tenant_id, vector, k, filters):
    return build_search_statement(user_id=user_id, tenant_id=tenant_id, query_vector=vector, limit=k, filters=filters)


def evaluate(session, *, name, user_id, tenant_id, vectors, filters, truth, ef, mode, force_hnsw=False, max_scan=None, mem=None):
    recalls = {5: [], 10: []}
    returned = []
    latencies = []
    for vector, expected in zip(vectors, truth):
        ids, elapsed = run_query(session, statement_for(user_id, tenant_id, vector, 10, filters), ef=ef, mode=mode, force_hnsw=force_hnsw, max_scan=max_scan, mem=mem)
        session.rollback()
        latencies.append(elapsed)
        returned.append(len(ids))
        for k in (5, 10):
            wanted = set(expected[:k])
            if wanted:
                recalls[k].append(len(wanted & set(ids[:k])) / len(wanted))
    apply_hnsw_settings(session, ef_search=ef, iterative_scan=mode, max_scan_tuples=max_scan, scan_mem_multiplier=mem)
    if force_hnsw:
        session.execute(text("SET LOCAL enable_seqscan = off"))
        session.execute(text("SET LOCAL enable_bitmapscan = off"))
        session.execute(text("SET LOCAL enable_sort = off"))
    plan = explain(session, statement_for(user_id, tenant_id, vectors[0], 10, filters))
    session.rollback()
    return {
        "scenario": name,
        "ef_search": ef,
        "iterative_scan": mode,
        "force_hnsw": force_hnsw,
        "max_scan_tuples": max_scan,
        "scan_mem_multiplier": mem,
        "queries": len(latencies),
        "recall_at_5": round(statistics.mean(recalls[5]), 4) if recalls[5] else None,
        "recall_at_10": round(statistics.mean(recalls[10]), 4) if recalls[10] else None,
        "avg_rows_returned_of_10": round(statistics.mean(returned), 2),
        "queries_with_too_few_rows": sum(1 for count, want in zip(returned, truth) if count < len(want)),
        "p50_ms": round(percentile(latencies, 0.5), 2),
        "p95_ms": round(percentile(latencies, 0.95), 2),
        "uses_hnsw": "ix_embeddings_embedding_hnsw" in plan,
    }


def exact_truth(session, *, user_id, tenant_id, vectors, filters):
    truth = []
    latencies = []
    for vector in vectors:
        ids, elapsed = run_query(session, statement_for(user_id, tenant_id, vector, 10, filters), ef=40, mode="off", exact=True)
        session.rollback()
        truth.append(ids)
        latencies.append(elapsed)
    return truth, {"p50_ms": round(percentile(latencies, 0.5), 2), "p95_ms": round(percentile(latencies, 0.95), 2), "queries": len(latencies)}


def save_report(report: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "workflow5_benchmark.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=int, default=20000)
    parser.add_argument("--queries", type=int, default=100)
    parser.add_argument("--resume", action="store_true", help="Keep the existing data and finish embedding it, instead of starting over.")
    parser.add_argument("--documents", type=int, default=None, help="Stop loading after this many documents.")
    parser.add_argument("--cache", default=None, help="A corpus file from chunk_size_check, so a resumed run uses exactly the same text and queries.")
    parser.add_argument("--load-only", action="store_true", help="Stop after embedding, leaving the data for search_latency. Writes no report.")
    args = parser.parse_args()

    report: dict = {"arguments": vars(args), "generated": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not args.resume:
        log("Rebuilding the test database from migrations")
        rebuild_database()

    if args.cache:
        paragraphs = json.loads(Path(args.cache).read_text(encoding="utf-8"))
    else:
        log("Collecting real text")
        paragraphs = collect_paragraphs(args.chunks * 2300 + 500_000)
    query_paragraphs = paragraphs[: args.queries]
    corpus = paragraphs[args.queries:]
    log(f"  {len(paragraphs)} paragraphs")

    session = db_session.SessionLocal()
    if args.resume:
        world = load_world(session)
        document_ids = list(session.scalars(select(Document.id).where(Document.embedding_status != "completed").order_by(Document.id)))
        samples = list(session.scalars(select(DocumentChunk.chunk_text).order_by(DocumentChunk.id).limit(1000)))
        session.rollback()
    else:
        world = build_world(session)
        log("Chunking with the real chunker")
        start = time.perf_counter()
        document_ids, samples = load_documents(session, world, corpus, args.chunks, args.documents)
        report["chunking_seconds"] = round(time.perf_counter() - start, 1)
    report["tenants"] = {name: {"tenant_id": info["tenant_id"]} for name, info in world.items()}

    log("Loading the model")
    start = time.perf_counter()
    embedding_model.get_model()
    report["model_load_seconds"] = round(time.perf_counter() - start, 2)
    report["model"] = {"name": settings.embedding_model_name, "revision": settings.embedding_model_revision, "device": settings.embedding_device, "batch_size": settings.embedding_batch_size}
    report["truncation"] = measure_truncation(samples)
    log(f"  Truncation: {report['truncation']}")

    log(f"Embedding {len(document_ids)} documents through EmbeddingService, with the HNSW index maintained on every insert")
    service = EmbeddingService(session)
    embeddings_before = session.execute(text("SELECT count(*) FROM embeddings")).scalar_one()
    session.rollback()
    start = time.perf_counter()
    for number, document_id in enumerate(document_ids, 1):
        service.embed_document(document_id)
        if number % 100 == 0:
            log(f"  {number}/{len(document_ids)} documents, {round(time.perf_counter() - start)}s")
    embed_seconds = time.perf_counter() - start

    def scalar(sql, **params):
        value = session.execute(text(sql), params).scalar_one()
        session.rollback()
        return value

    totals = {
        "documents": scalar("SELECT count(*) FROM documents"),
        "chunks": scalar("SELECT count(*) FROM document_chunks"),
        "embeddings": scalar("SELECT count(*) FROM embeddings"),
        "failed_documents": scalar("SELECT count(*) FROM documents WHERE embedding_status <> 'completed'"),
        "wrong_dimension": scalar("SELECT count(*) FROM embeddings WHERE vector_dims(embedding) <> 384"),
    }
    report["totals"] = totals
    created = totals["embeddings"] - embeddings_before
    report["embedding"] = {"embeddings_created_this_run": created, "seconds": round(embed_seconds, 1), "embeddings_per_second": round(created / embed_seconds, 1) if embed_seconds else None}
    log(f"  {report['embedding']}")

    session.execute(text("ANALYZE"))
    session.commit()
    if args.load_only:
        log(f"LOAD_FINISHED {totals}")
        return
    per_tenant = session.execute(text("SELECT tenant_id, count(*) FROM embeddings GROUP BY tenant_id")).all()
    session.rollback()
    by_id = dict(per_tenant)
    for name, info in world.items():
        report["tenants"][name]["embeddings"] = by_id.get(info["tenant_id"], 0)

    report["index"] = hnsw_index_status(session)
    report["storage_bytes"] = {
        "hnsw_index": scalar("SELECT pg_relation_size('ix_embeddings_embedding_hnsw')"),
        "embeddings_table": scalar("SELECT pg_table_size('embeddings')"),
        "embeddings_total_with_indexes": scalar("SELECT pg_total_relation_size('embeddings')"),
        "document_chunks_total": scalar("SELECT pg_total_relation_size('document_chunks')"),
        "documents_total": scalar("SELECT pg_total_relation_size('documents')"),
    }
    session.rollback()

    log("Rebuilding the index once to time a full build")
    connection = session.connection().connection
    start = time.perf_counter()
    session.execute(text("REINDEX INDEX ix_embeddings_embedding_hnsw"))
    session.commit()
    report["index_rebuild_seconds"] = round(time.perf_counter() - start, 1)
    report["maintenance_work_mem"] = scalar("SHOW maintenance_work_mem")
    del connection

    log("Encoding queries")
    vectors = [embedding_model.encode_query(paragraph[:300]).tolist() for paragraph in query_paragraphs]

    filter_sets = {
        "no_filter": None,
        "department_people_50pct": SearchFilters(department="people"),
        "department_legal_5pct": SearchFilters(department="legal"),
        "tags_legal_apac": SearchFilters(tags=["legal", "apac"]),
        "custom_region_apac_10pct": SearchFilters(custom_metadata={"region": "apac"}),
    }
    results = []
    exact_latency = {}
    for tenant_name, info in world.items():
        for filter_name, filters in filter_sets.items():
            scenario = f"{tenant_name}/{filter_name}"
            log(f"Scenario {scenario}: exact ground truth")
            truth, latency = exact_truth(session, user_id=info["admin_id"], tenant_id=info["tenant_id"], vectors=vectors, filters=filters)
            exact_latency[scenario] = {**latency, "avg_truth_rows": round(statistics.mean(len(t) for t in truth), 2)}
            for ef in (40, 100, 200):
                for mode in ("off", "strict_order", "relaxed_order"):
                    results.append(evaluate(session, name=scenario, user_id=info["admin_id"], tenant_id=info["tenant_id"], vectors=vectors, filters=filters, truth=truth, ef=ef, mode=mode))
            # What happens if the planner does walk the shared graph for this filter.
            for mode in ("off", "strict_order", "relaxed_order"):
                results.append(evaluate(session, name=scenario, user_id=info["admin_id"], tenant_id=info["tenant_id"], vectors=vectors, filters=filters, truth=truth, ef=100, mode=mode, force_hnsw=True))
            report["exact"] = exact_latency
            report["results"] = results
            save_report(report)

    log("Sweeping max_scan_tuples and scan_mem_multiplier on the most selective case")
    small = world["small"]
    selective = filter_sets["department_legal_5pct"]
    truth, _ = exact_truth(session, user_id=small["admin_id"], tenant_id=small["tenant_id"], vectors=vectors, filters=selective)
    sweep = []
    for max_scan in (500, 2000, 5000, 20000, 100000):
        for mem in (1.0, 4.0):
            sweep.append(evaluate(session, name="small/department_legal_5pct", user_id=small["admin_id"], tenant_id=small["tenant_id"], vectors=vectors, filters=selective, truth=truth, ef=100, mode="strict_order", force_hnsw=True, max_scan=max_scan, mem=mem))
    report["iterative_sweep"] = sweep

    log("Capturing EXPLAIN ANALYZE plans")
    plans = {}
    large = world["large"]
    for name, info, filters, force in (
        ("large tenant, no filter", large, None, False),
        ("large tenant, department filter", large, filter_sets["department_people_50pct"], False),
        ("small tenant, no filter", small, None, False),
        ("small tenant, selective filter, forced onto HNSW", small, selective, True),
    ):
        apply_hnsw_settings(session)
        if force:
            session.execute(text("SET LOCAL enable_seqscan = off"))
            session.execute(text("SET LOCAL enable_bitmapscan = off"))
            session.execute(text("SET LOCAL enable_sort = off"))
        plans[name] = explain(session, statement_for(info["admin_id"], info["tenant_id"], vectors[0], 10, filters), analyze=True)
        session.rollback()
    report["explain_analyze"] = plans
    report["complete"] = True
    save_report(report)
    log("BENCHMARK_FINISHED")


if __name__ == "__main__":
    main()
