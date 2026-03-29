# Realistic Workflow Cases

This directory stores realistic workflow fixtures used by the backend agent tests.

## Source of truth

- Main corpus: `realistic_workflow_cases.json`
- Python loader: `../workflow_scenario_cases.py`

## How to contribute new cases

Provide anonymized listings that are close to real geocache mysteries.

Useful fields:

- `id`: stable, unique identifier
- `payload.title`
- `payload.description`
- `payload.description_html`
- `payload.hint`
- `payload.images`
- `payload.waypoints`
- `payload.checkers`
- `expected_labels`
- `expected_workflow`
- `expected_plan_steps`
- optional `expect_workflow_candidates`
- optional `step_runner`
- optional `expected_signal_summary`
- optional mocks:
  - `remote_css_map`
  - `mock_plugin_results`
  - `mock_checker_result`

## Anonymization rules

Before adding a case:

- remove real geocache codes if they are sensitive
- remove player names, owner names, emails, phone numbers
- replace exact URLs with neutral placeholders when the remote content is not needed
- if exact coordinates are sensitive, shift them consistently while preserving the puzzle structure
- keep the puzzle mechanics intact

## Minimal acceptance bar

A good fixture should:

- represent a real puzzle pattern, not a toy string
- be understandable without external private context
- state the expected workflow clearly
- include enough data to reproduce the decision

Notes:

- `expected_labels` may be an empty list when the fixture documents a known limitation and the current workflow falls back to `general`
- use `expect_workflow_candidates: false` when an empty candidate list is the expected current behavior

## Workflow

1. Copy `realistic_workflow_cases.template.json`.
2. Add one or more anonymized cases.
3. Merge them into `realistic_workflow_cases.json`.
4. Run:

```powershell
python -m pytest gc-backend/tests/test_plugins_api.py -q
```

Or use the unified suite:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-geoapp-agent-suite.ps1 -BackendOnly
```
