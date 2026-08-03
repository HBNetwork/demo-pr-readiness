# PR-readiness fixture

This is the complete export boundary for the controlled public fixture. The
committed `.pr-lab/scenario.json` is validated by the stdlib-only
`tools/scenario_control.py`. Laboratory patches and answer material are not
part of this tree.

This clean-green branch demonstrates first-pass PR readiness.

## Release-readiness example

Release readiness can be evaluated deterministically from fixed pull-request
facts, without consulting network services or mutable repository state:

```pycon
>>> from pr_fixture.readiness import PullRequestFacts, ready_for_release
>>> facts = PullRequestFacts(draft=False, approvals=1, checks_green=True, mergeable=True)
>>> ready_for_release(facts)
True
```

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
python tools/scenario_control.py prepare hero-review
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

`hero-review` is the deliberately bounded multi-file schema-v2 variant. Its
manifest contains an ordered, closed `files` array with one payload path,
target path, and SHA-256 per entry. The admitted paths must be exactly the
selector followed by those targets in order. Its three opaque semantic
fingerprints are enforced by a scenario-confined executable validator; neither
the generated files nor the manifest contains expected review comments. The
aggregate payload and result identities hash path-framed ordered contents, so
path/content regrouping cannot preserve an identity accidentally. The fixture
is intended to compile and pass its executable contract while leaving its
review findings for the reviewer.

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
