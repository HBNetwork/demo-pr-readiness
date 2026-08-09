# PR-readiness fixture

This is the complete export boundary for the controlled public fixture. The
committed `.pr-lab/scenario.json` is validated by the stdlib-only
`tools/scenario_control.py`. Laboratory patches and answer material are not
part of this tree.

This clean-green branch demonstrates first-pass PR readiness.

## Role and operating model

This repository is a disposable, provider-realistic acceptance harness. A
scenario is prepared in a clean checkout, committed on its own branch, and
exercised through a real GitHub pull request. The product under test remains
external: this repository does not import its implementation or credentials.

Use a fresh checkout or worktree for every scenario. `prepare` deliberately has
no broad reset command: it reports the exact admitted paths it changed and
refuses to overwrite dirty, repaired, instructed, or unrelated work.

## Scenario catalog

| Scenario | Initial observable result | Expected exercise |
| --- | --- | --- |
| `clean-green` | CI passes | First-pass readiness |
| `first-attempt-flake` | Attempt 1 fails; later attempts pass | Exact rerun and recovery |
| `persistent-ci-regression` | Every attempt fails | Persistent blocking without merge |
| `seeded-review-finding` | CI passes with a latent mergeability defect | Review finding publication |
| `conversational-change` | Seed fixture passes | Add the manifest-pinned README example |
| `agent-repair` | Named fixture test fails | Publish the one pinned repair |
| `hero-review` | CI passes with three bounded defects | Multi-file review and complete pinned repair |

## Provider lifecycle recipes

Mutable GitHub state stays separate from deterministic content manifests. The
closed recipes under `.pr-lab/lifecycle/` bind the exact `clean-green` manifest
bytes while describing provider actions and observable transitions. They are
validation-only documents: the controller never calls GitHub or performs the
declared actions or cleanup.

| Recipe | Ordered lifecycle |
| --- | --- |
| `draft-to-ready` | Author marks a draft ready |
| `stale-base` | Operator advances the base; author updates the branch |
| `true-conflict` | Base advance makes the branch stale and conflicting; author resolves both dimensions |
| `collaboration-gate` | Author enables maintainer edits; reviewer approves |

Validate the complete registry or one canonical recipe:

```console
python tools/lifecycle_control.py validate
python tools/lifecycle_control.py validate draft-to-ready
python tools/lifecycle_control.py validate --recipe .pr-lab/lifecycle/true-conflict.json
```

Every recipe declares completion conditions followed by explicit close-PR and
delete-branch cleanup expectations. Use a fresh branch and PR for live
qualification; successful validation is not evidence that provider actions
occurred.

Scenario demonstrations require same-repository branches. Fork pull requests
skip scenario control but still run lint, type, unit, build, and integration
checks without persisted checkout credentials.

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
python tools/scenario_control.py prepare conversational-change
python tools/scenario_control.py prepare agent-repair
python tools/scenario_control.py prepare hero-review
python tools/scenario_control.py prepare persistent-ci-regression
python tools/scenario_control.py inspect seeded-review-finding
python tools/scenario_control.py validate --manifest .pr-lab/scenarios/clean-green/scenario.json
python tools/scenario_control.py evaluate --manifest .pr-lab/scenarios/clean-green/scenario.json --json
python tools/scenario_control.py prepare --manifest .pr-lab/scenarios/clean-green/scenario.json
python tools/scenario_control.py inspect --manifest .pr-lab/scenarios/clean-green/scenario.json
```

`prepare` verifies the manifest, payload, exact source basis (`path` plus
SHA-256), and the observed 40-hex Git HEAD, then writes both the generated
schema-v1 active selector and payload to its unique fixture target. The selector
is the sole registry-wide shared path; all fixture targets stay unique. Declared
instruction or repair paths may additionally be admitted without being generated
by `prepare`. It never commits. Repeating it against that exact seeded state is
an idempotent no-op; every other dirty, pre-existing, or unadmitted state is
refused. `inspect` is read-only and repeatable, and distinguishes exact
`prepared`, `instructed`, and explicitly admitted `repaired` fixture states.
`evaluate` exits 0 for pass, 1 for the scenario's expected failure, and 2 for
invalid input. CI evaluation requires exact immutable fixture bytes, except for
the pinned `agent-repair` result and validator-approved complete `hero-review`
repair. JSON is canonical and includes payload and result identities where
applicable.

`hero-review` is the deliberately bounded multi-file schema-v2 variant. Its
manifest contains an ordered, closed `files` array with one payload path,
target path, seeded SHA-256, and repaired SHA-256 per entry. The admitted paths
must be exactly the selector followed by those targets in order. Its three opaque semantic
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

## Rerun broker boundary

HamsterDan requests a rerun by posting one exact bot-authored marker containing
the workflow run, PR head, and producer-owned operation identity. The
`rerun-broker` workflow checks out trusted default-branch code without persisted
credentials, then `tools/rerun_broker.py` binds the marker to the API response's
exact run ID, repository, pull request, event, workflow path, and head SHA. Only
after validation does the workflow use its scoped `actions: write` permission
for one rerun POST. The broker does not maintain a replay store; producer-side
operation identity remains the idempotency owner.

Run checks from this directory with the committed lock. These commands may
populate the uv cache from the configured package index; `--frozen` prevents
the project lock from changing:

```console
uv run --frozen ruff check .
uv run --frozen ty check
uv run --frozen pytest tests/unit
uv build
uv run --isolated --no-project --with dist/*.whl --with pytest==9.1.1 pytest tests/integration
```
