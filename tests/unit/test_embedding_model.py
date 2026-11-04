"""Model handling, with a fake model so these run without PyTorch or a database."""
import sys
import threading
import types

import numpy as np
import pytest

from app.services.embeddings import model as embedding_model
from app.services.embeddings.model import EmbeddingDimensionError, validate_vectors


class FakeModel:
    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        vectors = np.ones((len(texts), 384), dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


@pytest.fixture
def fake_library(monkeypatch):
    monkeypatch.setattr(embedding_model, "_model", None)
    monkeypatch.setattr(embedding_model, "load_count", 0)
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = FakeModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)


def test_model_loads_once_across_calls(fake_library):
    first = embedding_model.get_model()
    assert embedding_model.get_model() is first
    assert embedding_model.encode_documents(["a"]).shape == (1, 384)
    assert embedding_model.encode_query("b").shape == (384,)
    assert embedding_model.load_count == 1


def test_model_loads_once_across_threads(fake_library):
    threads = [threading.Thread(target=embedding_model.get_model) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert embedding_model.load_count == 1


def test_documents_and_queries_use_the_same_model_but_only_queries_get_the_prefix(fake_library):
    embedding_model.encode_documents(["passage text"])
    embedding_model.encode_query("question")
    calls = embedding_model.get_model().calls
    assert calls[0][0] == ["passage text"]
    assert calls[1][0][0].endswith("question") and calls[1][0][0] != "question"
    assert all(call[1]["normalize_embeddings"] is True for call in calls)


def test_importing_the_module_does_not_load_the_library():
    assert "load_count" in vars(embedding_model)
    source = open(embedding_model.__file__, encoding="utf-8").read().split("def get_model", 1)[0]
    assert "import SentenceTransformer" not in source


@pytest.mark.parametrize(
    ("vectors", "count"),
    [
        (np.zeros((2, 383)), 2),
        (np.zeros((2, 385)), 2),
        (np.zeros((1, 384)), 2),
        (np.zeros(384), 1),
        (np.full((1, 384), np.nan), 1),
        (np.full((1, 384), np.inf), 1),
    ],
    ids=["too_short", "too_long", "wrong_count", "not_a_batch", "nan", "infinity"],
)
def test_invalid_vectors_are_rejected_before_storage(vectors, count):
    with pytest.raises(EmbeddingDimensionError):
        validate_vectors(vectors, count)


def test_valid_vectors_pass():
    assert validate_vectors(np.zeros((2, 384)), 2).shape == (2, 384)
