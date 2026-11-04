"""The embedding model: loaded once per process, on first use.

sentence_transformers is imported inside the loader so the API process, which never computes
document embeddings, does not pay for importing PyTorch at startup.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.core.config import settings
from app.db.models.embedding import EMBEDDING_DIMENSION


class EmbeddingDimensionError(Exception):
    """The model produced vectors of the wrong shape. Retrying cannot fix this."""


@dataclass(frozen=True)
class ModelIdentity:
    name: str
    version: str
    dimension: int


_lock = threading.Lock()
_model: Any = None
load_count = 0


def get_model() -> Any:
    global _model, load_count
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer(
                    settings.embedding_model_name,
                    revision=settings.embedding_model_revision,
                    cache_folder=settings.embedding_cache_dir,
                    device=settings.embedding_device,
                )
                load_count += 1
    return _model


def model_identity() -> ModelIdentity:
    return ModelIdentity(settings.embedding_model_name, settings.embedding_model_revision, settings.embedding_dimension)


def validate_vectors(vectors: Any, expected_count: int) -> np.ndarray:
    """Checked before insertion, so a bad vector can never reach the database."""
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim != 2 or array.shape[0] != expected_count or array.shape[1] != EMBEDDING_DIMENSION:
        raise EmbeddingDimensionError(f"Expected {expected_count} vectors of {EMBEDDING_DIMENSION} dimensions, got shape {array.shape}")
    if not np.isfinite(array).all():
        raise EmbeddingDimensionError("The model produced a vector containing NaN or infinity")
    return array


def encode_documents(texts: list[str]) -> np.ndarray:
    vectors = get_model().encode(
        texts,
        batch_size=settings.embedding_batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return validate_vectors(vectors, len(texts))


def encode_query(text: str) -> np.ndarray:
    """For Workflow 6. Uses the same model as documents, with the prefix BGE recommends for queries."""
    vector = get_model().encode(
        [settings.embedding_query_instruction + text],
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return validate_vectors(vector, 1)[0]
