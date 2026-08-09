import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

from tools.rerun_broker import (  # noqa: E402
    BrokerError,
    RerunRequest,
    authorize_run,
    parse_marker,
    parse_run_json,
)

RUN_ID = 123456789
HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
OPERATION = "rerun:owner.repo:42.attempt-2"
REPOSITORY = "HBNetwork/demo-pr-readiness"
PR_NUMBER = 42
MARKER = (
    f"<!-- hamsterdan-rerun run={RUN_ID} head={HEAD_SHA} operation={OPERATION} -->"
)


def valid_run() -> dict[str, object]:
    return {
        "id": RUN_ID,
        "repository": {"full_name": REPOSITORY},
        "event": "pull_request",
        "path": ".github/workflows/ci.yml",
        "head_sha": HEAD_SHA,
        "pull_requests": [{"number": PR_NUMBER}],
    }


def test_exact_marker_preserves_operation_identity() -> None:
    assert parse_marker(MARKER) == RerunRequest(RUN_ID, HEAD_SHA, OPERATION)


@pytest.mark.parametrize(
    "marker",
    [
        "",
        f"prefix {MARKER}",
        f"{MARKER} suffix",
        f"{MARKER}\n",
        f"<!-- hamsterdan-rerun run=0 head={HEAD_SHA} operation={OPERATION} -->",
        f"<!-- hamsterdan-rerun run=01 head={HEAD_SHA} operation={OPERATION} -->",
        f"<!-- hamsterdan-rerun run={RUN_ID} head={HEAD_SHA.upper()} operation={OPERATION} -->",
        f"<!-- hamsterdan-rerun  run={RUN_ID} head={HEAD_SHA} operation={OPERATION} -->",
    ],
)
def test_marker_requires_an_exact_full_match(marker: str) -> None:
    with pytest.raises(BrokerError, match="malformed rerun broker marker"):
        parse_marker(marker)


@pytest.mark.parametrize(
    "operation",
    [
        "a",
        "Z9._:-",
        "a" * 128,
    ],
)
def test_operation_identity_admits_the_exact_syntax(operation: str) -> None:
    marker = f"<!-- hamsterdan-rerun run={RUN_ID} head={HEAD_SHA} operation={operation} -->"
    assert parse_marker(marker).operation == operation


@pytest.mark.parametrize(
    "operation",
    [
        "",
        "_leading",
        "-leading",
        "contains/slash",
        "contains space",
        "a" * 129,
    ],
)
def test_operation_identity_rejects_values_outside_the_exact_syntax(operation: str) -> None:
    marker = f"<!-- hamsterdan-rerun run={RUN_ID} head={HEAD_SHA} operation={operation} -->"
    with pytest.raises(BrokerError, match="malformed rerun broker marker"):
        parse_marker(marker)


@pytest.mark.parametrize("raw_run", ["", "{", "[]", "null", '"run"'])
def test_run_json_must_be_a_well_formed_object(raw_run: str) -> None:
    with pytest.raises(BrokerError, match="malformed workflow run JSON"):
        parse_run_json(raw_run)


def test_exact_workflow_run_is_authorized_without_changing_the_request() -> None:
    request = parse_marker(MARKER)
    assert authorize_run(
        request, valid_run(), repository=REPOSITORY, pr_number=PR_NUMBER
    ) is request


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", RUN_ID + 1),
        ("repository", {"full_name": "HBNetwork/other"}),
        ("event", "workflow_dispatch"),
        ("path", ".github/workflows/other.yml"),
        ("head_sha", "f" * 40),
        ("pull_requests", [{"number": PR_NUMBER + 1}]),
    ],
)
def test_each_run_binding_is_required(field: str, value: object) -> None:
    run = valid_run()
    run[field] = value
    with pytest.raises(BrokerError, match="outside the exact PR workflow and head"):
        authorize_run(parse_marker(MARKER), run, repository=REPOSITORY, pr_number=PR_NUMBER)


@pytest.mark.parametrize(
    "field", ["id", "repository", "event", "path", "head_sha", "pull_requests"]
)
def test_missing_run_fields_are_rejected(field: str) -> None:
    run = valid_run()
    del run[field]
    with pytest.raises(BrokerError, match="outside the exact PR workflow and head"):
        authorize_run(parse_marker(MARKER), run, repository=REPOSITORY, pr_number=PR_NUMBER)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", str(RUN_ID)),
        ("repository", None),
        ("pull_requests", None),
        ("pull_requests", [None]),
        ("pull_requests", [{"number": str(PR_NUMBER)}]),
    ],
)
def test_malformed_run_fields_are_rejected(field: str, value: object) -> None:
    run = valid_run()
    run[field] = value
    with pytest.raises(BrokerError, match="outside the exact PR workflow and head"):
        authorize_run(parse_marker(MARKER), run, repository=REPOSITORY, pr_number=PR_NUMBER)


def test_cli_validates_and_outputs_the_complete_request_contract() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/rerun_broker.py"),
            "validate-run",
            "--run-id",
            str(RUN_ID),
            "--head",
            HEAD_SHA,
            "--operation",
            OPERATION,
            "--repository",
            REPOSITORY,
            "--pr-number",
            str(PR_NUMBER),
        ],
        input=json.dumps(valid_run()),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == asdict(parse_marker(MARKER))
