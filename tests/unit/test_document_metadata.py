import json

import pytest

from app.services.documents.metadata import (
    MetadataValidationError,
    build_metadata_input,
    parse_custom_metadata,
    parse_tags,
    validate_access_level,
)


def test_unset_fields_stay_none_so_a_new_version_can_inherit_them():
    metadata = build_metadata_input()
    assert metadata.document_type is None and metadata.tags is None and metadata.custom_metadata is None


def test_text_is_trimmed():
    assert build_metadata_input(department="  HR  ").department == "HR"


@pytest.mark.parametrize(("raw", "expected"), [("a, B ,a", ["a", "b"]), ('["X","y"]', ["x", "y"]), ("", [])])
def test_tags_are_normalized(raw, expected):
    assert parse_tags(raw) == expected


@pytest.mark.parametrize("raw", ['["a"', "[1]", "x" * 65, ",".join(f"t{i}" for i in range(51))], ids=["bad_json", "not_strings", "too_long", "too_many"])
def test_invalid_tags(raw):
    with pytest.raises(MetadataValidationError):
        parse_tags(raw)


def test_custom_metadata_allows_arbitrary_business_fields():
    value = {"region": "EU", "fiscal.year": 2026, "reviewed": True, "owners_note": None, "labels": ["a", "b"], "nested": {"cost-center": "42"}}
    assert parse_custom_metadata(json.dumps(value)) == value


@pytest.mark.parametrize(
    "value",
    [
        {"tenant_id": 2}, {"TENANT_ID": 2}, {"team_id": 2}, {"owner": "me"}, {"uploaded_by": 1}, {"role": "admin"},
        {"permissions": ["all"]}, {"access_level": "public"}, {"is_admin": True}, {"nested": {"tenant_id": 2}},
        {"list": [{"team_id": 2}]}, {"a": {"b": {"c": {"user_id": 1}}}},
    ],
    ids=lambda value: json.dumps(value)[:40],
)
def test_security_fields_are_refused_at_any_depth(value):
    with pytest.raises(MetadataValidationError):
        parse_custom_metadata(json.dumps(value))


@pytest.mark.parametrize(
    "raw",
    ["not json", "[1, 2]", '"text"', "5", '{"bad key": 1}', '{"$where": 1}', '{"x": "a\\u0000b"}', '{"x": NaN}', json.dumps({"x": "y" * 20000}), json.dumps({"a": {"b": {"c": {"d": {"e": 1}}}}}), json.dumps({f"k{i}": 1 for i in range(101)})],
    ids=["not_json", "array", "string", "number", "space_in_key", "operator_key", "nul", "nan", "too_large", "too_deep", "too_many_keys"],
)
def test_invalid_custom_metadata(raw):
    with pytest.raises(MetadataValidationError):
        parse_custom_metadata(raw)


@pytest.mark.parametrize("field", ["document_type", "department", "category", "source"])
@pytest.mark.parametrize("value", ["   ", "x" * 256, "bad\x00value"], ids=["blank", "too_long", "nul"])
def test_invalid_text_fields(field, value):
    with pytest.raises(MetadataValidationError):
        build_metadata_input(**{field: value})


def test_access_level_only_accepts_known_labels():
    assert validate_access_level("restricted") == "restricted"
    with pytest.raises(MetadataValidationError):
        validate_access_level("public")
