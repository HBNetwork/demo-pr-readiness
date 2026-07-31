# PR-readiness fixture

This is the complete export boundary for the controlled public fixture. The
committed `.pr-lab/scenario.json` is validated by the stdlib-only
`tools/scenario_control.py`. Laboratory patches and answer material are not
part of this tree.

This clean-green branch demonstrates first-pass PR readiness.

Run checks from this directory with the committed lock:

```console
uv run --frozen --offline ruff check .
uv run --frozen --offline ty check
uv run --frozen --offline pytest tests/unit
uv build --offline
uv run --offline --isolated --no-project --with dist/*.whl --with pytest==9.1.1 pytest tests/integration
```
