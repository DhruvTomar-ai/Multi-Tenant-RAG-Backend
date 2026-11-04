"""Writes the expected-vs-actual report as Markdown, CSV and JSON. All numbers come from the executed run."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

COLUMNS = ["test", "endpoint", "user", "expected", "actual", "status"]
HEADERS = ["Test Case", "Endpoint", "User", "Expected Result", "Actual Result", "Status"]
EXPORT_COLUMNS = [
    "test", "category", "endpoint", "user", "expected_status", "actual_status",
    "expected_behavior", "actual_behavior", "result", "explanation",
]
MODULE_CATEGORY = {
    "test_a_schema": "Database isolation",
    "test_b_auth": "Authentication",
    "test_c_users": "User management",
    "test_d_tenants_teams": "Tenant and team APIs",
    "test_f_documents_jobs": "Document and job APIs",
    "test_h_roles": "Role and permission isolation",
    "test_i_metadata": "Document metadata",
    "test_j_embeddings": "Embeddings and HNSW",
    "test_k_search": "Permission-aware search",
    "test_l_grants": "Ownership and document grants",
    "test_z_real_stack": "Real server (uvicorn, Redis, Celery)",
}


def category_of(row: dict) -> str:
    if row.get("category"):
        return row["category"]
    nodeid = row["test"] or ""
    module = nodeid.split(".py")[0].split("/")[-1]
    if module == "test_g_bypass_lifecycle":
        name = nodeid.split("::")[-1]
        if "team" in name or "removal" in name or "adding_back" in name or "role_downgrade" in name:
            return "Team isolation"
        if "tenant" in name or "document" in name:
            return "Tenant isolation"
        return "Authentication" if "user" in name or "token" in name else "Tenant isolation"
    return MODULE_CATEGORY.get(module, "Other")


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(rows: list[dict]) -> list[str]:
    lines = ["| " + " | ".join(HEADERS) + " |", "|" + "---|" * len(HEADERS)]
    for row in rows:
        values = {
            "test": (row["test"] or "").replace("api_tests/", ""),
            "endpoint": row["endpoint"],
            "user": row["user"],
            "expected": f"{row['expected_status']}: {row['expected_behavior']}",
            "actual": f"{row['actual_status']}: {row['actual_behavior']}",
            "status": row["result"],
        }
        lines.append("| " + " | ".join(_cell(values[name]) for name in COLUMNS) + " |")
    return lines


def _inventory(app) -> list[tuple[str, str, str, str]]:
    schema = app.openapi()
    found = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            inputs = [p["name"] for p in operation.get("parameters", [])]
            body = operation.get("requestBody", {}).get("content", {})
            for content in body.values():
                reference = content.get("schema", {}).get("$ref", "")
                inputs.append(f"body:{reference.split('/')[-1]}")
            responses = operation.get("responses", {})
            success = next((code for code in responses if code.startswith("2")), "")
            content = responses.get(success, {}).get("content", {}).get("application/json", {}).get("schema", {})
            model = content.get("$ref", "").split("/")[-1]
            if content.get("type") == "array":
                model = "list[" + content.get("items", {}).get("$ref", "").split("/")[-1] + "]"
            found.append((method.upper(), path, ", ".join(inputs) or "none", f"{success} {model}".strip()))
    return sorted(found, key=lambda item: (item[1], item[0]))


def write_reports(recorder, app, folder: Path, exitstatus: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    rows = recorder.rows
    for row in rows:
        row["category"] = category_of(row)

    (folder / "api_test_report.json").write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    with (folder / "api_test_report.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    inventory = _inventory(app)
    implemented = {(method, path) for method, path, _, _ in inventory}
    untested = sorted(implemented - recorder.routes)
    outside = sorted(recorder.routes - implemented)

    tests: dict[str, str] = {}
    categories: dict[str, str] = {}
    for row in rows:
        tests[row["test"]] = row["result"] if row["result"] != "PASS" else tests.get(row["test"], "PASS")
        categories[row["test"]] = row["category"]
    outcomes = Counter(tests.values())
    by_category: dict[str, Counter] = defaultdict(Counter)
    for name, outcome in tests.items():
        by_category[categories[name]][outcome] += 1
    failures = [row for row in rows if row["result"] != "PASS"]

    lines = [
        "# API test report",
        "",
        "Generated from an executed run. FAIL means the application did not behave as the test required.",
        "",
        "## Totals",
        "",
        f"- APIs discovered: {len(implemented)}",
        f"- APIs tested: {len(implemented) - len(untested)}",
        f"- APIs not tested: {', '.join(f'{m} {p}' for m, p in untested) or 'none'}",
        f"- Requests to methods with no route (expected to be refused): {', '.join(f'{m} {p}' for m, p in outside) or 'none'}",
        f"- Test cases executed: {len(tests)}",
        f"- Passed: {outcomes['PASS']}",
        f"- Failed: {outcomes['FAIL']}",
        f"- Errors: {outcomes['ERROR']}",
        f"- Skipped: {outcomes['SKIPPED']}",
        f"- Checks recorded: {len(rows)}",
        "",
        "## Results by area",
        "",
        "| Area | Test cases | Passed | Failed | Errors |",
        "|---|---|---|---|---|",
        *(
            f"| {name} | {sum(c.values())} | {c['PASS']} | {c['FAIL']} | {c['ERROR']} |"
            for name, c in sorted(by_category.items())
        ),
        "",
        f"## Failures and errors ({len(failures)})",
        "",
        "| Test Case | Endpoint | User | Expected | Actual | Status | Explanation |",
        "|---|---|---|---|---|---|---|",
        *(
            "| " + " | ".join(_cell(value) for value in (
                (row["test"] or "").replace("api_tests/", ""), row["endpoint"], row["user"],
                f"{row['expected_status']}: {row['expected_behavior']}", f"{row['actual_status']}: {row['actual_behavior']}",
                row["result"], row["explanation"],
            )) + " |"
            for row in failures
        ),
        "",
        f"## API inventory ({len(inventory)})",
        "",
        "| Method | Path | Inputs | Success response |",
        "|---|---|---|---|",
        *(f"| {method} | {path} | {inputs} | {response} |" for method, path, inputs, response in inventory),
        "",
        f"## Every check ({len(rows)})",
        "",
        *_table(rows),
        "",
    ]
    (folder / "api_test_report.md").write_text("\n".join(lines), encoding="utf-8")
