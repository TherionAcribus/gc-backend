"""Load realistic workflow scenarios from JSON fixtures."""

import json
from pathlib import Path


FIXTURES_FILE = Path(__file__).parent / 'fixtures' / 'realistic_workflow_cases.json'
REQUIRED_CASE_KEYS = {
    'id',
    'payload',
    'expected_labels',
    'expected_plan_steps',
}


def _load_realistic_workflow_cases():
    if not FIXTURES_FILE.exists():
        raise RuntimeError(f'Missing workflow scenario fixtures file: {FIXTURES_FILE}')

    with FIXTURES_FILE.open('r', encoding='utf-8') as handle:
        cases = json.load(handle)

    if not isinstance(cases, list) or not cases:
        raise RuntimeError('Workflow scenario fixtures must contain a non-empty JSON array')

    seen_ids = set()
    normalized_cases = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise RuntimeError(f'Workflow scenario at index {index} must be a JSON object')

        missing_keys = sorted(REQUIRED_CASE_KEYS - set(case.keys()))
        if missing_keys:
            raise RuntimeError(
                f"Workflow scenario at index {index} is missing required keys: {', '.join(missing_keys)}"
            )

        case_id = case.get('id')
        if not isinstance(case_id, str) or not case_id.strip():
            raise RuntimeError(f'Workflow scenario at index {index} must define a non-empty string id')
        if case_id in seen_ids:
            raise RuntimeError(f'Duplicate workflow scenario id: {case_id}')
        seen_ids.add(case_id)

        payload = case.get('payload')
        if not isinstance(payload, dict) or not payload:
            raise RuntimeError(f'Workflow scenario "{case_id}" must define a non-empty payload object')

        expected_labels = case.get('expected_labels')
        if not isinstance(expected_labels, list):
            raise RuntimeError(f'Workflow scenario "{case_id}" must define an expected_labels list')

        expected_plan_steps = case.get('expected_plan_steps')
        if not isinstance(expected_plan_steps, list) or not expected_plan_steps:
            raise RuntimeError(f'Workflow scenario "{case_id}" must define a non-empty expected_plan_steps list')

        normalized_cases.append(case)

    return normalized_cases


REALISTIC_WORKFLOW_CASES = _load_realistic_workflow_cases()
