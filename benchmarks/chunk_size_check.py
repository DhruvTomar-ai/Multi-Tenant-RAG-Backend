"""Measures real chunks with the BGE tokenizer, to find a CHUNK_SIZE that fits the model's limit.

The chunker counts tokens with a different tokenizer, so CHUNK_SIZE alone says nothing about how
many tokens BGE sees. Text beyond BGE's limit is silently left out of the embedding.

    TEST_DATABASE_URL=... python -m benchmarks.chunk_size_check --sizes 400 360 320 280 240 --cache corpus.json
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

from benchmarks.hnsw_benchmark import REPORT_DIR, collect_paragraphs, log
from app.core.config import settings
from app.services.documents.processing import ExtractedSection, chunk_sections
from app.services.embeddings import model as embedding_model


def stress_documents() -> dict[str, str]:
    """Text that tokenizes very differently from English prose."""
    rng = random.Random(7)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/"
    return {
        "chinese": "。".join("".join(chr(rng.randint(0x4E00, 0x9FA5)) for _ in range(30)) for _ in range(600)),
        "number_table": "\n".join(" | ".join(f"{rng.randint(0, 99999)}.{rng.randint(0, 99)}" for _ in range(12)) for _ in range(800)),
        "source_code": "\n".join(f"def fn_{i}(x_{i}, y_{i}): return some_module.call_{i}(x_{i}[{i}], key='value_{i}') + {i * 7}" for i in range(1500)),
        "urls": " ".join(f"https://example.com/a{rng.randint(0, 9999)}/b{rng.randint(0, 9999)}?id={rng.randint(0, 99999)}&t=x{i}" for i in range(1500)),
        "base64_blob": "".join(rng.choice(alphabet) for _ in range(60000)),
        "punctuation_heavy": " ".join("a.b,c;d:e!f?g(h)i[j]k{l}m" for _ in range(3000)),
    }


def measure(tokenizer, chunks: list[str]) -> dict:
    lengths = [len(ids) for ids in tokenizer(chunks, add_special_tokens=True, truncation=False)["input_ids"]]
    ordered = sorted(lengths)
    limit = embedding_model.get_model().max_seq_length
    over = sum(1 for length in lengths if length > limit)
    return {
        "chunks": len(lengths),
        "max": ordered[-1],
        "p99": ordered[int(0.99 * (len(ordered) - 1))],
        "mean": round(statistics.mean(lengths), 1),
        "over_limit": over,
        "percent_over_limit": round(100 * over / len(lengths), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[400, 360, 320, 280, 240])
    parser.add_argument("--cache", required=True, help="Stores the corpus, so later runs reuse exactly the same text.")
    args = parser.parse_args()

    cache = Path(args.cache)
    if cache.exists():
        paragraphs = json.loads(cache.read_text(encoding="utf-8"))
    else:
        log("Collecting real text")
        paragraphs = collect_paragraphs(46_000_000)
        cache.write_text(json.dumps(paragraphs), encoding="utf-8")
    log(f"{len(paragraphs)} paragraphs")

    tokenizer = embedding_model.get_model().tokenizer
    report = {"model_limit": embedding_model.get_model().max_seq_length, "overlap": settings.chunk_overlap, "sizes": {}}
    stress = stress_documents()
    for size in args.sizes:
        chunks: list[str] = []
        for start in range(0, len(paragraphs), 32):
            sections = [ExtractedSection(text="\n\n".join(paragraphs[start:start + 32]))]
            chunks.extend(chunk.text for chunk in chunk_sections(sections, chunk_size=size, chunk_overlap=settings.chunk_overlap))
        entry = {"real_text": measure(tokenizer, chunks), "stress": {}}
        for name, content in stress.items():
            pieces = [chunk.text for chunk in chunk_sections([ExtractedSection(text=content)], chunk_size=size, chunk_overlap=settings.chunk_overlap)]
            entry["stress"][name] = measure(tokenizer, pieces)
        report["sizes"][str(size)] = entry
        log(f"CHECK size={size} real={entry['real_text']} stress_max={ {name: value['max'] for name, value in entry['stress'].items()} }")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "chunk_size_check.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    log("CHECK_FINISHED")


if __name__ == "__main__":
    main()
