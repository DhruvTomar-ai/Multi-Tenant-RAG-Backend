import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.search import SearchRequest


def test_minimal_request_uses_defaults():
    request = SearchRequest(query="leave policy")
    assert request.top_k == settings.search_default_top_k
    assert (request.team_ids, request.document_ids, request.offset) == ([], [], 0)


def test_tags_are_normalized_like_uploads():
    assert SearchRequest(query="x", filters={"tags": ["EU", "eu", "Handbook"]}).filters.tags == ["eu", "handbook"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": "bad\x00query"},
        {"query": "x" * 2001},
        {"query": "x", "top_k": 0},
        {"query": "x", "top_k": settings.search_max_top_k + 1},
        {"query": "x", "offset": -1},
        {"query": "x", "offset": settings.search_max_offset + 1},
        {"query": "x", "team_ids": [0]},
        {"query": "x", "team_ids": [99999999999999999999]},
        {"query": "x", "document_ids": ["abc"]},
        {"query": "x", "team_ids": list(range(1, 52))},
        {"query": "x", "tenant_id": 1},
        {"query": "x", "user_id": 1},
        {"query": "x", "chunk_ids": [1]},
        {"query": "x", "filters": {"unknown": 1}},
        {"query": "x", "filters": {"tenant_id": 1}},
        {"query": "x", "filters": {"access_level": "public"}},
        {"query": "x", "filters": {"custom_metadata": {"tenant_id": 1}}},
        {"query": "x", "filters": {"custom_metadata": {"nested": {"team_id": 1}}}},
        {"query": "x", "filters": {"tags": ["y" * 65]}},
    ],
    ids=[
        "empty", "blank_empty", "blank_spaces", "nul", "too_long", "top_k_0", "top_k_too_large", "negative_offset", "offset_too_large",
        "team_id_0", "team_id_out_of_range", "document_id_text", "too_many_teams", "tenant_id_field", "user_id_field", "chunk_ids_field",
        "unknown_filter", "tenant_id_filter", "bad_access_level", "reserved_custom_key", "nested_reserved_custom_key", "long_tag",
    ],
)
def test_invalid_requests_are_refused(body):
    with pytest.raises(ValidationError):
        SearchRequest(**body)
