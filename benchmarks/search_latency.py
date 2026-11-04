"""Measures POST /tenants/{id}/search on data loaded by hnsw_benchmark --load-only.

Every number comes from this run: the real route through the real app, with the real model.

    TEST_DATABASE_URL=... python -m benchmarks.search_latency --cache corpus.json
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from pathlib import Path

from benchmarks import hnsw_benchmark as hb
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.document_grant import DocumentGrant
from app.db.models.user import User
from app.main import app
from app.security.auth import create_access_token
from app.services.embeddings import model as embedding_model
from app.services.embeddings.search import SearchFilters, apply_hnsw_settings

REPORT = hb.REPORT_DIR / "workflow6_search_latency.json"
SEED = 20261104


def summarize(values: list[float]) -> dict:
    return {"requests": len(values), "p50_ms": round(hb.percentile(values, 0.5), 1), "p95_ms": round(hb.percentile(values, 0.95), 1), "max_ms": round(max(values), 1)}


def headers_for(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", required=True)
    parser.add_argument("--queries", type=int, default=100)
    args = parser.parse_args()

    paragraphs = json.loads(Path(args.cache).read_text(encoding="utf-8"))
    queries = paragraphs[: args.queries]  # Held out by hnsw_benchmark, so never indexed.
    session = hb.db_session.SessionLocal()
    world = hb.load_world(session)
    large, small = world["large"], world["small"]

    member_id = session.scalar(select(User.id).where(User.email == "t1@large.example"))
    other_team_docs = session.scalars(
        select(Document.id).where(Document.tenant_id == large["tenant_id"], Document.team_id == large["team_ids"]["t0"]).order_by(Document.id).limit(5)
    ).all()
    already_granted = set(session.scalars(select(DocumentGrant.document_id).where(DocumentGrant.user_id == member_id)))
    for document_id in other_team_docs:
        if document_id not in already_granted:
            session.add(DocumentGrant(document_id=document_id, tenant_id=large["tenant_id"], team_id=large["team_ids"]["t0"], user_id=member_id))
    session.commit()
    session.execute(text("ANALYZE"))
    session.commit()
    a_team_doc = session.scalar(select(Document.id).where(Document.tenant_id == large["tenant_id"], Document.team_id == large["team_ids"]["t1"]).order_by(Document.id).limit(1))

    totals = {
        "documents": session.execute(text("SELECT count(*) FROM documents")).scalar_one(),
        "chunks": session.execute(text("SELECT count(*) FROM document_chunks")).scalar_one(),
        "embeddings": session.execute(text("SELECT count(*) FROM embeddings")).scalar_one(),
        "large_tenant_embeddings": session.execute(text("SELECT count(*) FROM embeddings WHERE tenant_id = :t"), {"t": large["tenant_id"]}).scalar_one(),
        "small_tenant_embeddings": session.execute(text("SELECT count(*) FROM embeddings WHERE tenant_id = :t"), {"t": small["tenant_id"]}).scalar_one(),
        "grants_for_member": len(other_team_docs),
    }
    session.rollback()
    report: dict = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "totals": totals}
    hb.log(f"Totals {totals}")

    client = TestClient(app)
    admin = headers_for(large["admin_id"])
    member = headers_for(member_id)
    small_admin = headers_for(small["admin_id"])
    url = f"/api/v1/tenants/{large['tenant_id']}/search"
    small_url = f"/api/v1/tenants/{small['tenant_id']}/search"

    hb.log("Cold request: the first search in this process loads the model")
    start = time.perf_counter()
    response = client.post(url, json={"query": "first request", "top_k": 5}, headers=admin)
    report["cold_first_request_ms"] = round((time.perf_counter() - start) * 1000, 1)
    assert response.status_code == 200, response.text

    hb.log("Query embedding alone")
    encode_times = []
    for query in queries:
        start = time.perf_counter()
        embedding_model.encode_query(query[:300])
        encode_times.append((time.perf_counter() - start) * 1000)
    report["query_embedding_only"] = summarize(encode_times)

    scenarios = {
        "large tenant admin, no filter": (url, admin, {}),
        "large tenant team member with 5 grants": (url, member, {}),
        "small tenant admin, no filter": (small_url, small_admin, {}),
        "large admin, team_ids filter": (url, admin, {"team_ids": [large["team_ids"]["t1"]]}),
        "large admin, department filter": (url, admin, {"filters": {"department": "people"}}),
        "large admin, selective department filter": (url, admin, {"filters": {"department": "legal"}}),
        "large admin, tag filter": (url, admin, {"filters": {"tags": ["legal", "apac"]}}),
        "large admin, custom metadata filter": (url, admin, {"filters": {"custom_metadata": {"region": "apac"}}}),
        "large admin, document_ids filter": (url, admin, {"document_ids": [a_team_doc]}),
    }
    report["route_latency"] = {}
    for name, (target, headers, extra) in scenarios.items():
        hb.log(f"Route: {name}")
        times, counts = [], []
        for query in queries:
            start = time.perf_counter()
            response = client.post(target, json={"query": query[:300], "top_k": 10, **extra}, headers=headers)
            times.append((time.perf_counter() - start) * 1000)
            assert response.status_code == 200, response.text
            counts.append(len(response.json()["results"]))
        report["route_latency"][name] = {**summarize(times), "avg_results": round(statistics.mean(counts), 2)}

    hb.log("Recall of the permission-aware query against exact search")
    vectors = [embedding_model.encode_query(query[:300]).tolist() for query in queries]
    report["recall"] = []
    for name, user_id, tenant_id, filters in (
        ("large admin, no filter", large["admin_id"], large["tenant_id"], None),
        ("team member with 5 grants", member_id, large["tenant_id"], None),
        ("large admin, department 5%", large["admin_id"], large["tenant_id"], SearchFilters(department="legal")),
        ("small admin, no filter", small["admin_id"], small["tenant_id"], None),
    ):
        truth, exact = hb.exact_truth(session, user_id=user_id, tenant_id=tenant_id, vectors=vectors, filters=filters)
        for mode in ("off", "strict_order", "relaxed_order"):
            for force in (False, True):
                result = hb.evaluate(session, name=name, user_id=user_id, tenant_id=tenant_id, vectors=vectors, filters=filters, truth=truth, ef=100, mode=mode, force_hnsw=force)
                result["exact_p50_ms"] = exact["p50_ms"]
                result["avg_truth_rows"] = round(statistics.mean(len(t) for t in truth), 2)
                report["recall"].append(result)

    hb.log("Finding a chunk from its own text")
    rng = random.Random(SEED)
    chunk_rows = session.execute(
        select(DocumentChunk.id, DocumentChunk.document_id, DocumentChunk.chunk_text).where(DocumentChunk.tenant_id == large["tenant_id"]).order_by(DocumentChunk.id)
    ).all()
    session.rollback()
    sample = rng.sample(chunk_rows, min(args.queries, len(chunk_rows)))
    top1 = top5 = 0
    for chunk_id, document_id, chunk_text in sample:
        body = client.post(url, json={"query": chunk_text[:300], "top_k": 5}, headers=admin).json()
        found = [result["chunk_id"] for result in body["results"]]
        top1 += bool(found) and found[0] == chunk_id
        top5 += chunk_id in found
    report["self_retrieval"] = {"chunks_sampled": len(sample), "hit_at_1": round(top1 / len(sample), 3), "hit_at_5": round(top5 / len(sample), 3)}

    hb.log("Capturing EXPLAIN ANALYZE")
    report["explain_analyze"] = {}
    for name, user_id, tenant_id, filters in (
        ("Large tenant admin, no filter", large["admin_id"], large["tenant_id"], None),
        ("Team member with 5 grants", member_id, large["tenant_id"], None),
        ("Small tenant admin, no filter", small["admin_id"], small["tenant_id"], None),
        ("Large tenant admin, department filter", large["admin_id"], large["tenant_id"], SearchFilters(department="people")),
    ):
        apply_hnsw_settings(session)
        report["explain_analyze"][name] = hb.explain(session, hb.statement_for(user_id, tenant_id, vectors[0], 10, filters), analyze=True)
        session.rollback()

    hb.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    hb.log("SEARCH_BENCHMARK_FINISHED")


if __name__ == "__main__":
    main()
