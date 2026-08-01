# PR-readiness fixture

This is the complete export boundary for the controlled public fixture. The
committed `.pr-lab/scenario.json` is validated by the stdlib-only
`tools/scenario_control.py`. Laboratory patches and answer material are not
part of this tree.

This clean-green branch demonstrates first-pass PR readiness.

## Deterministic scenario laboratory

The legacy schema-v1 invocation remains supported:

```console
python tools/scenario_control.py --attempt 1
```

Schema-v2 scenarios are immutable, closed manifests at
`.pr-lab/scenarios/<id>/scenario.json`. They contain only deterministic content intent;
GitHub comments, reviews, base branches, conflicts, and lifecycle state are
deliberately external to the manifests. Use the public commands as follows:

```console
python tools/scenario_control.py validate
python tools/scenario_control.py evaluate first-attempt-flake --attempt 1
python tools/scenario_control.py prepare seeded-review-finding
python tools/scenario_control.py inspect seeded-review-finding
python tools/scenario_control.py validate --manifest .pr-lab/scenarios/clean-green/scenario.json
python tools/scenario_control.py evaluate --manifest .pr-lab/scenarios/clean-green/scenario.json --json
python tools/scenario_control.py prepare --manifest .pr-lab/scenarios/clean-green/scenario.json
python tools/scenario_control.py inspect --manifest .pr-lab/scenarios/clean-green/scenario.json
```

`prepare` verifies the manifest, payload, exact source basis (`path` plus
SHA-256), and the observed 40-hex Git HEAD, then writes both the generated
schema-v1 active selector and payload to its unique admitted target. The selector
is the sole registry-wide shared admitted path; all fixture targets stay unique.
It never commits. Repeating it against
that exact prepared state is an idempotent no-op; every other dirty,
pre-existing, or unadmitted state is refused. `inspect` is read-only and
repeatable. `evaluate` exits 0 for pass, 1 for the scenario's expected failure,
and 2 for invalid input. JSON is canonical and includes payload and result
identities where applicable.

Manifests cannot contain an expected commit SHA: committing such a SHA would
change the commit and therefore the value itself. The laboratory instead
records the actual observed HEAD in evidence and binds deterministic content
to source and payload SHA-256 identities, avoiding that circularity. Manifest
identity binds the exact JSON bytes rather than a parsed/canonicalized object.
CI evidence is confined below `.pr-lab/evidence/`, uses the fetched base-to-head
Git diff, and excludes mutable GitHub review, comment, and conflict state.

Run checks from this directory with the committed lock:

```console
uv run --frozen --offline ruff check .
uv run --frozen --offline ty check
uv run --frozen --offline pytest tests/unit
uv build --offline
uv run --offline --isolated --no-project --with dist/*.whl --with pytest==9.1.1 pytest tests/integration
```
