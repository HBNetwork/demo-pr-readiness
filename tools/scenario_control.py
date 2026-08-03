"""Deterministic scenario laboratory control, implemented with only stdlib."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

CONTROL_FIELDS = frozenset({"schema_version", "scenario", "behavior", "review_lenses"})
BEHAVIOR_FIELDS = frozenset({"kind", "fingerprint"})
LENSES = ("correctness", "test-quality", "risk")
MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "scenario",
        "behavior",
        "review_lenses",
        "source_basis",
        "fixture",
        "expectation",
    }
)
BEHAVIORS = {"pass", "first_attempt_flake", "persistent_failure"}
ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = Path(".pr-lab/scenarios")
SELECTOR = ".pr-lab/scenario.json"
EVIDENCE_DIRECTORY = ".pr-lab/evidence/"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*")

EXPECTATION_FIELDS = {
    "content": frozenset({"kind", "target", "content_sha256"}),
    "seeded_review_finding": frozenset(
        {"kind", "target", "content_sha256", "semantic_fingerprint"}
    ),
    "conversational_instruction": frozenset(
        {"kind", "target", "content_sha256", "instruction"}
    ),
    "agent_repair": frozenset(
        {"kind", "target", "content_sha256", "failing_test", "repair_paths"}
    ),
    "hero_review": frozenset(
        {"kind", "semantic_fingerprints", "validator", "validator_sha256"}
    ),
}
SCENARIO_CONTRACTS = {
    "clean-green": ("pass", "content"),
    "first-attempt-flake": ("first_attempt_flake", "content"),
    "persistent-ci-regression": ("persistent_failure", "content"),
    "seeded-review-finding": ("pass", "seeded_review_finding"),
    "conversational-change": ("pass", "conversational_instruction"),
    "agent-repair": ("pass", "agent_repair"),
    "hero-review": ("pass", "hero_review"),
}
SINGLE_FILE_FIXTURE_FIELDS = frozenset({"payload", "target", "admitted_changed_paths"})
HERO_FIXTURE_FIELDS = frozenset({"files", "admitted_changed_paths"})
HERO_FILE_FIELDS = frozenset({"payload", "target", "content_sha256"})
HERO_VALIDATOR = ".pr-lab/scenarios/hero-review/validate.py"


class ControlError(ValueError):
    """A control, manifest, repository, or invocation is not admitted."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ControlError(f"{label} must be an object")
    return value


def _closed(value: Any, fields: frozenset[str], label: str) -> dict[str, Any]:
    result = _object(value, label)
    if set(result) != fields:
        raise ControlError(f"{label} fields must be exactly: {', '.join(sorted(fields))}")
    return result


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ControlError(f"{label} must be a non-empty string")
    return value


def safe_path(value: Any, label: str = "path") -> str:
    """Return a normalized admitted repository-relative POSIX path."""
    path = _text(value, label)
    if "\\" in path or path.startswith("/") or path.startswith("-"):
        raise ControlError(f"{label} is not a safe relative path")
    parts = path.split("/")
    if any(part in {"", ".", ".."} or part.startswith("-") for part in parts):
        raise ControlError(f"{label} is not normalized")
    if any(part.casefold() in {".git", ".gitmodules"} for part in parts):
        raise ControlError(f"{label} names protected git metadata")
    if PurePosixPath(path).as_posix() != path:
        raise ControlError(f"{label} is not normalized")
    return path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _framed_contents(contents: list[tuple[str, bytes]]) -> bytes:
    """Bind ordered paths and contents without concatenation ambiguity."""
    framed = bytearray()
    for path, content in contents:
        path_bytes = path.encode()
        framed.extend(len(path_bytes).to_bytes(8, "big"))
        framed.extend(path_bytes)
        framed.extend(len(content).to_bytes(8, "big"))
        framed.extend(content)
    return bytes(framed)


def _behavior(value: Any) -> dict[str, Any]:
    behavior = _object(value, "behavior")
    kind = behavior.get("kind")
    fields = frozenset({"kind"}) if kind == "pass" else BEHAVIOR_FIELDS
    if kind not in BEHAVIORS or set(behavior) != fields:
        raise ControlError("behavior has invalid kind or fields")
    if kind != "pass":
        _text(behavior["fingerprint"], "behavior fingerprint")
    return behavior


def validate_control(value: Any) -> dict[str, Any]:
    """Validate and return the legacy schema-v1 control."""
    control = _closed(value, CONTROL_FIELDS, "control")
    if control["schema_version"] != 1:
        raise ControlError("schema_version must be 1")
    _text(control["scenario"], "scenario")
    if control["review_lenses"] != list(LENSES):
        raise ControlError("review_lenses must be the three independent pinned lenses")
    _behavior(control["behavior"])
    return control


def _read_json(path: Path, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ControlError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result

    try:
        return _object(json.loads(path.read_bytes(), object_pairs_hook=reject_duplicates), label)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ControlError(f"cannot read {label}: {error}") from error


def load_control(path: Path) -> dict[str, Any]:
    return validate_control(_read_json(path, "control"))


def evaluate(control: dict[str, Any], attempt: int) -> tuple[bool, str]:
    """Evaluate legacy or schema-v2 behavior for a positive run attempt."""
    if control.get("schema_version") == 1:
        validate_control(control)
    if attempt < 1:
        raise ControlError("attempt must be a positive integer")
    behavior = _behavior(control.get("behavior"))
    if behavior["kind"] == "first_attempt_flake" and attempt == 1:
        return False, behavior["fingerprint"]
    if behavior["kind"] == "persistent_failure":
        return False, behavior["fingerprint"]
    return True, "scenario-control:pass"


def _confined(root: Path, name: str, label: str, *, allow_missing: bool = False) -> Path:
    """Confine a path and reject every existing symlink in its traversal."""
    relative = safe_path(name, label)
    root_resolved = root.resolve()
    current = root_resolved
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ControlError(f"{label} traverses a symlink")
        if current.exists() and not current.resolve().is_relative_to(root_resolved):
            raise ControlError(f"{label} escapes repository root")
    resolved = current.resolve(strict=not allow_missing)
    if not resolved.is_relative_to(root_resolved):
        raise ControlError(f"{label} escapes repository root")
    return current


def validate_manifest(value: Any, scenario_id: str, root: Path = ROOT) -> dict[str, Any]:
    """Validate one closed schema-v2 manifest and its content contracts."""
    manifest = _closed(value, MANIFEST_FIELDS, "manifest")
    if manifest["schema_version"] != 2:
        raise ControlError("manifest schema_version must be 2")
    scenario = _text(manifest["scenario"], "scenario")
    if scenario != scenario_id or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", scenario):
        raise ControlError("manifest scenario must be its canonical scenario directory name")
    if manifest["review_lenses"] != list(LENSES):
        raise ControlError("review_lenses must be the three independent pinned lenses")
    behavior = _behavior(manifest["behavior"])

    basis = _closed(manifest["source_basis"], frozenset({"path", "sha256"}), "source_basis")
    basis_path = safe_path(basis["path"], "source_basis.path")
    if not isinstance(basis["sha256"], str) or not HEX64.fullmatch(basis["sha256"]):
        raise ControlError("source_basis.sha256 must be lowercase sha256")

    fixture_fields = (
        HERO_FIXTURE_FIELDS if scenario == "hero-review" else SINGLE_FILE_FIXTURE_FIELDS
    )
    fixture = _closed(manifest["fixture"], fixture_fields, "fixture")
    canonical_prefix = f"{SCENARIOS.as_posix()}/{scenario}/"
    files: list[dict[str, str]] = []
    if scenario == "hero-review":
        raw_files = fixture["files"]
        if not isinstance(raw_files, list) or len(raw_files) < 2:
            raise ControlError("hero fixture files must be an ordered multi-file array")
        for index, value in enumerate(raw_files):
            entry = _closed(value, HERO_FILE_FIELDS, f"fixture.files[{index}]")
            payload = safe_path(entry["payload"], f"fixture.files[{index}].payload")
            target = safe_path(entry["target"], f"fixture.files[{index}].target")
            digest = entry["content_sha256"]
            if not isinstance(digest, str) or not HEX64.fullmatch(digest):
                raise ControlError("fixture file content_sha256 must be lowercase sha256")
            files.append({"payload": payload, "target": target, "content_sha256": digest})
    else:
        payload = safe_path(fixture["payload"], "fixture.payload")
        target = safe_path(fixture["target"], "fixture.target")
        files.append({"payload": payload, "target": target, "content_sha256": ""})
    payloads = [entry["payload"] for entry in files]
    targets = [entry["target"] for entry in files]
    if len(payloads) != len(set(payloads)) or len(targets) != len(set(targets)):
        raise ControlError("fixture payloads and targets must be unique")
    admitted = fixture["admitted_changed_paths"]
    if not isinstance(admitted, list) or not admitted:
        raise ControlError("admitted_changed_paths must be a non-empty array")
    normalized = [safe_path(item, "admitted_changed_paths item") for item in admitted]
    if len(normalized) != len(set(normalized)):
        raise ControlError("admitted_changed_paths contains duplicates")
    if normalized != [SELECTOR, *targets]:
        raise ControlError("admitted_changed_paths must contain selector then ordered targets")
    if any(
        not payload.startswith(canonical_prefix) or payload == canonical_prefix
        for payload in payloads
    ):
        raise ControlError("fixture payloads must be confined to their scenario directory")

    expectation = _object(manifest["expectation"], "expectation")
    expectation_kind = expectation.get("kind")
    fields = EXPECTATION_FIELDS.get(expectation_kind)
    if fields is None or set(expectation) != fields:
        raise ControlError("expectation has invalid kind or fields")
    if SCENARIO_CONTRACTS.get(scenario) != (behavior["kind"], expectation_kind):
        raise ControlError("scenario behavior and expectation contract do not match")
    if expectation_kind != "hero_review":
        if expectation["target"] != targets[0]:
            raise ControlError("expectation target must equal fixture target")
        digest = expectation["content_sha256"]
        if not isinstance(digest, str) or not HEX64.fullmatch(digest):
            raise ControlError("expectation.content_sha256 must be lowercase sha256")
        files[0]["content_sha256"] = digest
    if expectation_kind == "hero_review":
        fingerprints = expectation["semantic_fingerprints"]
        if (
            not isinstance(fingerprints, list)
            or len(fingerprints) != 3
            or any(not isinstance(item, str) or not item for item in fingerprints)
            or len(set(fingerprints)) != 3
        ):
            raise ControlError("hero review requires exactly three unique semantic fingerprints")
        validator = safe_path(expectation["validator"], "expectation.validator")
        if validator != HERO_VALIDATOR:
            raise ControlError("hero validator must be the exact confined validator")
        validator_file = _confined(root, validator, "expectation.validator")
        if validator_file.suffix != ".py" or not validator_file.is_file():
            raise ControlError("hero validator must be a regular Python file")
        validator_digest = expectation["validator_sha256"]
        if (
            not isinstance(validator_digest, str)
            or not HEX64.fullmatch(validator_digest)
            or _sha256(validator_file.read_bytes()) != validator_digest
        ):
            raise ControlError("hero validator does not match validator_sha256")
    elif expectation_kind == "seeded_review_finding":
        _text(expectation["semantic_fingerprint"], "semantic_fingerprint")
    elif expectation_kind == "conversational_instruction":
        _text(expectation["instruction"], "instruction")
    elif expectation_kind == "agent_repair":
        _text(expectation["failing_test"], "failing_test")
        repair_paths = expectation["repair_paths"]
        if not isinstance(repair_paths, list) or not repair_paths:
            raise ControlError("repair_paths must be a non-empty array")
        repairs = [safe_path(item, "repair_paths item") for item in repair_paths]
        if len(repairs) != len(set(repairs)) or targets[0] not in repairs:
            raise ControlError("repair_paths must be unique and include fixture target")

    scenario_directory = (root / SCENARIOS / scenario).resolve()
    for entry in files:
        payload_file = _confined(root, entry["payload"], "fixture.payload")
        if not payload_file.resolve().is_relative_to(scenario_directory):
            raise ControlError("fixture payload escapes its scenario directory")
        if _sha256(payload_file.read_bytes()) != entry["content_sha256"]:
            raise ControlError("payload does not match declared content_sha256")
    _confined(root, basis_path, "source_basis.path")
    for target in targets:
        _confined(root, target, "fixture.target", allow_missing=True)
    if expectation_kind == "hero_review":
        _run_fixture_validator(manifest, root, payloads)
    return manifest


def _fixture_files(manifest: dict[str, Any]) -> list[dict[str, str]]:
    fixture = manifest["fixture"]
    if manifest["scenario"] == "hero-review":
        return fixture["files"]
    return [
        {
            "payload": fixture["payload"],
            "target": fixture["target"],
            "content_sha256": manifest["expectation"]["content_sha256"],
        }
    ]


def _run_fixture_validator(
    manifest: dict[str, Any], root: Path, paths: list[str] | None = None
) -> None:
    if manifest["expectation"]["kind"] != "hero_review":
        return
    validator = _confined(root, manifest["expectation"]["validator"], "expectation.validator")
    selected = paths or [entry["target"] for entry in _fixture_files(manifest)]
    inputs = [_confined(root, path, "validator input") for path in selected]
    with tempfile.TemporaryDirectory(prefix="hero-review-validator-") as temporary:
        confined = Path(temporary)
        validator_copy = confined / "validate.py"
        validator_copy.write_bytes(validator.read_bytes())
        arguments: list[str] = []
        for source in inputs:
            copy = confined / source.name
            copy.write_bytes(source.read_bytes())
            arguments.append(str(copy))
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(validator_copy), *arguments],
                cwd=confined,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                env={},
            )
        except subprocess.TimeoutExpired as error:
            raise ControlError("hero fixture validator timed out") from error
    if result.returncode:
        raise ControlError("hero fixture validator failed")


def _manifest_name(scenario: str) -> str:
    return f"{SCENARIOS.as_posix()}/{scenario}/scenario.json"


def load_registry(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    """Load every scenario and enforce registry-wide path and payload uniqueness."""
    directory = _confined(root, SCENARIOS.as_posix(), "scenario registry")
    try:
        children = sorted(directory.iterdir())
    except OSError as error:
        raise ControlError(f"cannot read scenario registry: {error}") from error
    if any(item.is_symlink() for item in children):
        raise ControlError("scenario registry entries must not be symlinks")
    entries = [item for item in children if item.is_dir()]
    manifests: dict[str, dict[str, Any]] = {}
    targets: set[str] = set()
    payloads: set[str] = set()
    payload_digests: set[str] = set()
    semantic_fingerprints: set[str] = set()
    for entry in entries:
        manifest_name = _manifest_name(entry.name)
        path = _confined(root, manifest_name, "manifest path")
        manifest = validate_manifest(_read_json(path, "manifest"), entry.name, root)
        files = _fixture_files(manifest)
        aliases = targets.intersection(entry["target"] for entry in files)
        if aliases:
            raise ControlError(f"duplicate admitted target path: {sorted(aliases)[0]}")
        payload_aliases = payloads.intersection(entry["payload"] for entry in files)
        if payload_aliases:
            raise ControlError(f"duplicate payload path: {sorted(payload_aliases)[0]}")
        digests = {entry["content_sha256"] for entry in files}
        if len(digests) != len(files) or payload_digests.intersection(digests):
            raise ControlError("scenario payload hashes must be genuinely distinct")
        targets.update(entry["target"] for entry in files)
        payloads.update(entry["payload"] for entry in files)
        payload_digests.update(digests)
        expectation = manifest["expectation"]
        fingerprints: list[str] = []
        if expectation["kind"] == "hero_review":
            fingerprints = expectation["semantic_fingerprints"]
        elif expectation["kind"] == "seeded_review_finding":
            fingerprints = [expectation["semantic_fingerprint"]]
        if semantic_fingerprints.intersection(fingerprints):
            raise ControlError("semantic fingerprints must be registry-wide unique")
        semantic_fingerprints.update(fingerprints)
        manifests[entry.name] = manifest
    if not manifests:
        raise ControlError("scenario registry is empty")
    for manifest in manifests.values():
        _verify_basis(manifest, root)
    return manifests


def _select_manifest(
    path: Path | None, scenario: str | None, root: Path
) -> tuple[str, dict[str, Any]]:
    registry = load_registry(root)
    if path is not None:
        raw = path.as_posix()
        safe_path(raw, "manifest path")
        match = re.fullmatch(r"\.pr-lab/scenarios/([^/]+)/scenario\.json", raw)
        if match is None or raw != _manifest_name(match.group(1)):
            raise ControlError(
                "manifest path must be canonical .pr-lab/scenarios/<scenario>/scenario.json"
            )
        _confined(root, raw, "manifest path")
        selected = match.group(1)
        if scenario is not None and scenario != selected:
            raise ControlError("scenario id and manifest path disagree")
    elif scenario is not None:
        selected = scenario
    else:
        raise ControlError("scenario or manifest is required")
    if selected not in registry:
        raise ControlError("unknown scenario id")
    return selected, registry[selected]


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    if result.returncode:
        raise ControlError("git repository state is unavailable")
    return result.stdout.strip()


def observed_head(root: Path = ROOT) -> str:
    head = _git(root, "rev-parse", "--verify", "HEAD")
    if not HEX40.fullmatch(head):
        raise ControlError("observed HEAD is not a lowercase 40hex commit identity")
    return head


def _porcelain(root: Path) -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ControlError("git repository state is unavailable")
    output = result.stdout
    if not output:
        return []
    records = output.split("\0")
    paths: list[tuple[str, str]] = []
    index = 0
    while index < len(records) and records[index]:
        record = records[index]
        status, path = record[:2], record[3:]
        if status[0] in {"R", "C"}:
            index += 1
        paths.append((status, path))
        index += 1
    return sorted(set(paths))


def _is_exact_prepared_status(
    state: list[tuple[str, str]], target_names: list[str]
) -> bool:
    if not state:
        return True
    observed = {path: status for status, path in state}
    if set(observed) not in (set(target_names), {SELECTOR, *target_names}):
        return False
    if any(observed.get(target) != "??" for target in target_names):
        return False
    return SELECTOR not in observed or observed[SELECTOR] == " M"


def _observed_paths(root: Path) -> list[str]:
    return sorted({path for _, path in _porcelain(root)})


def _identities(manifest: dict[str, Any], root: Path) -> tuple[str, str, str]:
    files = _fixture_files(manifest)
    payload_contents = [
        (
            entry["payload"],
            _confined(root, entry["payload"], "fixture.payload").read_bytes(),
        )
        for entry in files
    ]
    manifest_digest = _sha256(
        _confined(root, _manifest_name(manifest["scenario"]), "manifest path").read_bytes()
    )
    selector = _confined(root, SELECTOR, "selector")
    if manifest["scenario"] != "hero-review":
        target = _confined(root, files[0]["target"], "fixture.target", allow_missing=True)
        target_bytes = target.read_bytes() if target.is_file() else b""
        return (
            manifest_digest,
            _sha256(payload_contents[0][1]),
            _sha256(selector.read_bytes() + b"\0" + target_bytes),
        )
    result_contents = [(SELECTOR, selector.read_bytes())]
    for entry in files:
        target = _confined(root, entry["target"], "fixture.target", allow_missing=True)
        result_contents.append((entry["target"], target.read_bytes() if target.is_file() else b""))
    payload_digest = _sha256(_framed_contents(payload_contents))
    result_digest = _sha256(_framed_contents(result_contents))
    return manifest_digest, payload_digest, result_digest


def _selector(manifest: dict[str, Any]) -> bytes:
    value = {
        "schema_version": 1,
        "scenario": manifest["scenario"],
        "behavior": manifest["behavior"],
        "review_lenses": manifest["review_lenses"],
    }
    return (json.dumps(value, indent=2) + "\n").encode()


def _verify_basis(manifest: dict[str, Any], root: Path) -> None:
    basis = manifest["source_basis"]
    try:
        digest = _sha256(_confined(root, basis["path"], "source_basis.path").read_bytes())
    except OSError as error:
        raise ControlError(f"cannot read source basis: {error}") from error
    if digest != basis["sha256"]:
        raise ControlError("source basis mismatch")


def prepare(
    scenario: str | None, root: Path = ROOT, manifest_path: Path | None = None
) -> dict[str, Any]:
    """Apply exactly one validated payload without creating a commit."""
    scenario, manifest = _select_manifest(manifest_path, scenario, root)
    head = observed_head(root)
    _verify_basis(manifest, root)
    files = _fixture_files(manifest)
    payloads = [
        _confined(root, entry["payload"], "fixture.payload").read_bytes() for entry in files
    ]
    admitted = manifest["fixture"]["admitted_changed_paths"]
    targets = [
        _confined(root, entry["target"], "fixture.target", allow_missing=True) for entry in files
    ]
    selector = _confined(root, SELECTOR, "selector")
    selector_bytes = _selector(manifest)
    state = _porcelain(root)
    exact_prepared = (
        selector.read_bytes() == selector_bytes
        and all(target.is_file() for target in targets)
        and all(target.read_bytes() == payload for target, payload in zip(targets, payloads))
        and _is_exact_prepared_status(state, [entry["target"] for entry in files])
    )
    if exact_prepared:
        _run_fixture_validator(manifest, root)
        changed = False
    else:
        if state:
            raise ControlError("worktree has dirty or unadmitted changes")
        if any(target.exists() for target in targets):
            raise ControlError("fixture target already exists")
        selector_before = selector.read_bytes()
        missing_directories: list[Path] = []
        for target in targets:
            parent = target.parent
            while not parent.exists() and parent != root:
                if parent not in missing_directories:
                    missing_directories.append(parent)
                parent = parent.parent
        created_directories: list[Path] = []
        created_targets: list[Path] = []
        try:
            for directory in reversed(missing_directories):
                directory.mkdir()
                created_directories.append(directory)
            # Recheck after mkdir so races cannot redirect any write.
            targets = [
                _confined(root, entry["target"], "fixture.target", allow_missing=True)
                for entry in files
            ]
            for target, payload in zip(targets, payloads):
                try:
                    with target.open("xb") as stream:
                        created_targets.append(target)
                        stream.write(payload)
                except FileExistsError as error:
                    raise ControlError("fixture target appeared during prepare") from error
            _run_fixture_validator(manifest, root)
            selector.write_bytes(selector_bytes)
        except Exception:
            for target in created_targets:
                if target.is_file():
                    target.unlink()
            if selector.read_bytes() != selector_before:
                selector.write_bytes(selector_before)
            for directory in reversed(created_directories):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
        changed = True
    manifest_digest, payload_digest, result_digest = _identities(manifest, root)
    return {
        "schema_version": 1,
        "scenario": scenario,
        "manifest_path": _manifest_name(scenario),
        "changed": changed,
        "observed_head": head,
        "admitted_changed_paths": admitted,
        "observed_changed_paths": _observed_paths(root),
        "manifest_sha256": manifest_digest,
        "payload_sha256": payload_digest,
        "result_sha256": result_digest,
    }


def inspect(
    scenario: str | None, root: Path = ROOT, manifest_path: Path | None = None
) -> dict[str, Any]:
    """Read and report deterministic scenario and worktree state."""
    scenario, manifest = _select_manifest(manifest_path, scenario, root)
    head = observed_head(root)
    _verify_basis(manifest, root)
    admitted = manifest["fixture"]["admitted_changed_paths"]
    observed = _observed_paths(root)
    files = _fixture_files(manifest)
    payloads = [
        _confined(root, entry["payload"], "fixture.payload").read_bytes() for entry in files
    ]
    targets = [
        _confined(root, entry["target"], "fixture.target", allow_missing=True) for entry in files
    ]
    selector_matches = _confined(root, SELECTOR, "selector").read_bytes() == _selector(manifest)
    state = _porcelain(root)
    prepared = (
        all(target.is_file() for target in targets)
        and all(target.read_bytes() == payload for target, payload in zip(targets, payloads))
        and selector_matches
        and _is_exact_prepared_status(state, [entry["target"] for entry in files])
    )
    if prepared:
        _run_fixture_validator(manifest, root)
    elif state or any(target.exists() for target in targets):
        raise ControlError("worktree is neither clean baseline nor exact prepared state")
    manifest_digest, payload_digest, result_digest = _identities(manifest, root)
    return {
        "schema_version": 1,
        "scenario": scenario,
        "manifest_path": _manifest_name(scenario),
        "valid": True,
        "observed_head": head,
        "manifest_sha256": manifest_digest,
        "payload_sha256": payload_digest,
        "result_sha256": result_digest,
        "admitted_changed_paths": admitted,
        "observed_changed_paths": observed,
        "state": "prepared" if prepared else "not-prepared",
    }


def _emit(value: dict[str, Any], path: Path | None = None) -> None:
    content = _canonical(value)
    if path is None:
        sys.stdout.buffer.write(content)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _github_context(observed: str, *, required: bool) -> dict[str, str]:
    context = {
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "pull_request": os.environ.get("PR_NUMBER", ""),
        "base_sha": os.environ.get("BASE_SHA", ""),
        "head_sha": os.environ.get("HEAD_SHA", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    }
    if any(len(value) > 256 for value in context.values()):
        raise ControlError("GitHub context value is too long")
    if required and any(not value for value in context.values()):
        raise ControlError("CI evidence requires complete GitHub context")
    if context["repository"] and not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", context["repository"]
    ):
        raise ControlError("GITHUB_REPOSITORY must be owner/name")
    for name in ("base_sha", "head_sha"):
        value = context[name]
        if value and not HEX40.fullmatch(value):
            raise ControlError(f"{name.upper()} must be lowercase 40hex")
    for name in ("pull_request", "run_id", "run_attempt"):
        value = context[name]
        if value and not POSITIVE_INTEGER.fullmatch(value):
            raise ControlError(f"{name} must be a positive integer")
    if context["head_sha"] and context["head_sha"] != observed:
        raise ControlError("HEAD_SHA does not equal observed_head")
    return context


def _evidence(
    scenario: str,
    manifest: dict[str, Any],
    attempt: int,
    passed: bool,
    head: str,
    identities: tuple[str, str, str],
    observed_paths: list[str],
    *,
    ci_grade: bool,
) -> dict[str, Any]:
    context = _github_context(head, required=ci_grade)
    observed_result = "pass" if passed else "fail"
    expected_passed, _ = evaluate(manifest, attempt)
    expected_result = "pass" if expected_passed else "fail"
    return {
        "schema_version": 1,
        "scenario": scenario,
        "manifest_path": _manifest_name(scenario),
        "manifest_sha256": identities[0],
        "repository": context["repository"],
        "pull_request": context["pull_request"],
        "base_sha": context["base_sha"],
        "head_sha": context["head_sha"],
        "observed_head": head,
        "run_id": context["run_id"],
        "run_attempt": context["run_attempt"],
        "behavior": manifest["behavior"],
        "expected_result": expected_result,
        "observed_result": observed_result,
        "admitted_changed_paths": manifest["fixture"]["admitted_changed_paths"],
        "observed_changed_paths": observed_paths,
        "payload_sha256": identities[1],
        "result_sha256": identities[2],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=Path, default=Path(".pr-lab/scenario.json"))
    parser.add_argument("--attempt", type=int)
    subparsers = parser.add_subparsers(dest="command")
    for command in ("validate", "evaluate", "prepare", "inspect"):
        child = subparsers.add_parser(command)
        child.add_argument("scenario", nargs="?")
        child.add_argument("--manifest", type=Path)
        child.add_argument("--json", action="store_true", help="emit JSON (the default)")
        if command == "evaluate":
            child.add_argument("--attempt", type=int)
            child.add_argument("--evidence", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        attempt_text = args.attempt if args.attempt is not None else os.environ.get(
            "GITHUB_RUN_ATTEMPT", "1"
        )
        if isinstance(attempt_text, str) and not POSITIVE_INTEGER.fullmatch(attempt_text):
            raise ControlError("attempt must be a positive integer")
        attempt = int(attempt_text)
    except (ControlError, TypeError, ValueError) as error:
        if args.command is None:
            print(f"scenario-control:invalid:{error}")
        else:
            _emit({"schema_version": 1, "valid": False, "error": str(error)})
        return 2
    if args.command is None:
        try:
            passed, conclusion = evaluate(load_control(args.control), attempt)
        except ControlError as error:
            print(f"scenario-control:invalid:{error}")
            return 2
        print(conclusion)
        return 0 if passed else 1
    try:
        if args.command == "validate":
            if args.manifest or args.scenario:
                scenario, _ = _select_manifest(args.manifest, args.scenario, ROOT)
                scenarios = [scenario]
            else:
                scenarios = sorted(load_registry(ROOT))
            _emit({"schema_version": 1, "valid": True, "scenarios": scenarios})
            return 0
        if args.command == "prepare":
            _emit(prepare(args.scenario, ROOT, args.manifest))
            return 0
        if args.command == "inspect":
            _emit(inspect(args.scenario, ROOT, args.manifest))
            return 0
        if args.manifest or args.scenario:
            scenario, manifest = _select_manifest(args.manifest, args.scenario, ROOT)
            control = manifest
        else:
            control = load_control(args.control)
            scenario, manifest = _select_manifest(None, control["scenario"], ROOT)
            expected = _selector(manifest)
            if (json.dumps(control, indent=2) + "\n").encode() != expected:
                raise ControlError("active control does not exactly match selected manifest")
        _verify_basis(manifest, ROOT)
        passed, conclusion = evaluate(control, attempt)
        head = observed_head(ROOT)
        if args.evidence:
            evidence_name = safe_path(args.evidence.as_posix(), "evidence path")
            if not evidence_name.startswith(EVIDENCE_DIRECTORY):
                raise ControlError("evidence path must be under .pr-lab/evidence/")
            evidence_path = _confined(ROOT, evidence_name, "evidence path", allow_missing=True)
            selector = _confined(ROOT, SELECTOR, "selector")
            if selector.read_bytes() != _selector(manifest):
                raise ControlError("active control does not exactly match selected manifest")
            targets = [
                _confined(ROOT, entry["target"], "fixture.target", allow_missing=True)
                for entry in _fixture_files(manifest)
            ]
            if not all(target.is_file() for target in targets):
                raise ControlError("active scenario fixture target is missing")
            _run_fixture_validator(manifest, ROOT)
            context = _github_context(head, required=True)
            if context["run_attempt"] != str(attempt):
                raise ControlError("attempt must equal GITHUB_RUN_ATTEMPT")
            changed = _git(ROOT, "diff", "--name-only", context["base_sha"], context["head_sha"])
            observed_paths = sorted(
                {safe_path(path, "observed changed path") for path in changed.splitlines() if path}
            )
            if not observed_paths or not set(observed_paths) <= set(
                manifest["fixture"]["admitted_changed_paths"]
            ):
                raise ControlError("CI changed paths are empty or not admitted")
        else:
            _github_context(head, required=False)
            evidence_path = None
            observed_paths = _observed_paths(ROOT)
        evidence = _evidence(
            scenario,
            manifest,
            attempt,
            passed,
            head,
            _identities(manifest, ROOT),
            observed_paths,
            ci_grade=evidence_path is not None,
        )
        _emit(evidence)
        print(conclusion, file=sys.stderr)
        if evidence_path:
            _emit(evidence, evidence_path)
        return 0 if passed else 1
    except (ControlError, OSError) as error:
        invalid = {"schema_version": 1, "valid": False, "error": str(error)[:256]}
        _emit(invalid)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
