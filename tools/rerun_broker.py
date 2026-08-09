"""Validate rerun-broker markers and bind them to an exact workflow run."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any

MARKER = re.compile(
    r"<!-- hamsterdan-rerun run=([1-9][0-9]*) head=([0-9a-f]{40}) "
    r"operation=([A-Za-z0-9][A-Za-z0-9._:-]{0,127}) -->"
)
WORKFLOW = ".github/workflows/ci.yml"


class BrokerError(ValueError):
    """A rerun request is malformed or is not authorized for the exact run."""


@dataclass(frozen=True)
class RerunRequest:
    """Producer-owned identity and bindings for one requested rerun."""

    run_id: int
    head_sha: str
    operation: str


def parse_marker(marker: str) -> RerunRequest:
    """Parse an exact rerun marker without admitting surrounding content."""
    match = MARKER.fullmatch(marker)
    if match is None:
        raise BrokerError("refusing malformed rerun broker marker")
    return RerunRequest(
        run_id=int(match.group(1)),
        head_sha=match.group(2),
        operation=match.group(3),
    )


def parse_run_json(raw_run: str) -> dict[str, Any]:
    """Decode a workflow run response as a JSON object."""
    try:
        run = json.loads(raw_run)
    except json.JSONDecodeError as error:
        raise BrokerError("refusing malformed workflow run JSON") from error
    if not isinstance(run, dict):
        raise BrokerError("refusing malformed workflow run JSON")
    return run


def authorize_run(
    request: RerunRequest,
    run: dict[str, Any],
    *,
    repository: str,
    pr_number: int,
) -> RerunRequest:
    """Return the request only when the API response matches every binding."""
    run_repository = run.get("repository")
    pull_requests = run.get("pull_requests")
    valid_pull_request = isinstance(pull_requests, list) and any(
        isinstance(pull_request, dict)
        and type(pull_request.get("number")) is int
        and pull_request["number"] == pr_number
        for pull_request in pull_requests
    )
    valid = (
        type(run.get("id")) is int
        and run["id"] == request.run_id
        and isinstance(run_repository, dict)
        and run_repository.get("full_name") == repository
        and run.get("event") == "pull_request"
        and run.get("path") == WORKFLOW
        and run.get("head_sha") == request.head_sha
        and valid_pull_request
    )
    if not valid:
        raise BrokerError("refusing rerun request outside the exact PR workflow and head")
    return request


def _positive_integer(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise argparse.ArgumentTypeError("must be a positive integer")
    return int(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    marker_parser = subparsers.add_parser("parse-marker")
    marker_parser.add_argument("--marker", required=True)

    run_parser = subparsers.add_parser("validate-run")
    run_parser.add_argument("--run-id", required=True, type=_positive_integer)
    run_parser.add_argument("--head", required=True)
    run_parser.add_argument("--operation", required=True)
    run_parser.add_argument("--repository", required=True)
    run_parser.add_argument("--pr-number", required=True, type=_positive_integer)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "parse-marker":
            request = parse_marker(args.marker)
            print(request.run_id)
            print(request.head_sha)
            print(request.operation)
            return 0

        request = parse_marker(
            f"<!-- hamsterdan-rerun run={args.run_id} head={args.head} "
            f"operation={args.operation} -->"
        )
        authorize_run(
            request,
            parse_run_json(sys.stdin.read()),
            repository=args.repository,
            pr_number=args.pr_number,
        )
        print(json.dumps(asdict(request), sort_keys=True, separators=(",", ":")))
        return 0
    except BrokerError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
