"""
Tests for the archive API.
"""

import sys
import types

import pytest

try:
    import pyproj  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - optional dependency in tests
    class _FakeGeod:
        def __init__(self, **_kwargs):
            pass

        def inv(self, *_args, **_kwargs):
            return 0.0, 0.0, 0.0

    sys.modules['pyproj'] = types.SimpleNamespace(Geod=_FakeGeod)

from gc_backend import create_app
from gc_backend.database import db
from gc_backend.models import Zone
from gc_backend.geocaches.archive_service import ArchiveService
from gc_backend.geocaches.models import Geocache


@pytest.fixture
def app():
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def sample_geocache(app):
    with app.app_context():
        zone = Zone(name='Archive Test Zone')
        db.session.add(zone)
        db.session.flush()

        geocache = Geocache(
            gc_code='GCARCH1',
            name='Archive Diagnostic Cache',
            zone_id=zone.id,
            description_raw='Listing with hidden clues and a compact code.',
            hints='8 5 12 12 15',
            solved='in_progress',
        )
        db.session.add(geocache)
        db.session.commit()
        return geocache.gc_code


def test_update_resolution_diagnostics_creates_archive_from_geocache(client, app, sample_geocache):
    payload = {
        'source': 'plugin_executor_metasolver',
        'labels': [{'name': 'secret_code', 'confidence': 0.91}],
        'recommended_actions': ['Use metasolver on the best compact fragment.'],
        'metasolver': {
            'selected_plugins': ['alpha_decoder', 'morse_code']
        }
    }

    response = client.put(f'/api/archive/{sample_geocache}/resolution-diagnostics', json=payload)

    assert response.status_code == 200
    assert response.get_json()['updated'] is True

    with app.app_context():
        archive = ArchiveService.get_by_gc_code(sample_geocache)
        assert archive is not None
        assert archive['gc_code'] == sample_geocache
        assert archive['solved_status'] == 'in_progress'
        assert archive['resolution_diagnostics']['source'] == 'plugin_executor_metasolver'
        assert archive['resolution_diagnostics']['metasolver']['selected_plugins'] == ['alpha_decoder', 'morse_code']


def test_update_resolution_diagnostics_returns_404_for_unknown_gc_code(client):
    response = client.put('/api/archive/GCUNKNOWN/resolution-diagnostics', json={'source': 'test'})

    assert response.status_code == 404
    assert response.get_json()['error'] == 'Archive not found for this gc_code'


def test_update_resolution_diagnostics_requires_payload(client, sample_geocache):
    response = client.put(f'/api/archive/{sample_geocache}/resolution-diagnostics', json={})

    assert response.status_code == 400
    assert response.get_json()['error'] == 'No data provided'


def test_update_resolution_diagnostics_persists_resume_state(client, app, sample_geocache):
    payload = {
        'source': 'plugin_executor_metasolver',
        'resume_state': {
            'currentText': 'N 47° AB.CDE E 006° FG.HIJ',
            'recommendationSourceText': '8 5 12 12 15',
            'workflowResolution': {
                'workflow': {
                    'kind': 'formula',
                    'confidence': 0.94,
                    'score': 6.0,
                    'reason': 'formula dominant',
                    'supporting_labels': ['formula'],
                },
                'workflow_candidates': [],
                'classification': {
                    'source': 'geocache',
                    'geocache': {'id': 1, 'gc_code': sample_geocache, 'name': 'Archive Diagnostic Cache'},
                    'title': 'Archive Diagnostic Cache',
                    'max_secret_fragments': 5,
                    'labels': [{'name': 'formula', 'confidence': 0.94, 'evidence': ['coord template']}],
                    'recommended_actions': ['Utiliser le Formula Solver'],
                    'candidate_secret_fragments': [],
                    'hidden_signals': [],
                    'formula_signals': ['N 47° AB.CDE E 006° FG.HIJ'],
                    'signal_summary': {
                        'has_title': True,
                        'has_hint': True,
                        'has_description_html': False,
                        'image_count': 0,
                        'checker_count': 1,
                        'waypoint_count': 0,
                    },
                },
                'plan': [{'id': 'search-answers', 'title': 'Search answers', 'status': 'completed', 'automated': True}],
                'execution': {
                    'formula': {
                        'formula_count': 1,
                        'formulas': [{'formula': 'N 47° AB.CDE E 006° FG.HIJ'}],
                        'variables': ['A', 'B'],
                        'questions': {'A': 'Nombre de bornes ?', 'B': 'Annee du pont ?'},
                        'found_question_count': 2,
                        'answer_search': {
                            'answers': {
                                'A': {
                                    'question': 'Nombre de bornes ?',
                                    'best_answer': '12',
                                    'recommended_value_type': 'integer',
                                }
                            },
                            'found_count': 1,
                            'missing': ['B'],
                        },
                        'calculated_coordinates': {
                            'coordinates': {'ddm': 'N 47° 12.345 E 006° 54.321'},
                        },
                    },
                    'checker': {
                        'checker_name': 'Certitude',
                        'status': 'success',
                        'candidate': 'N 47° 12.345 E 006° 54.321',
                        'message': 'Coordonnees valides',
                    },
                },
                'next_actions': ['validate-with-checker'],
                'explanation': ['formula workflow restored'],
            },
            'workflowEntries': [
                {'id': 'entry-1', 'category': 'formula', 'message': 'Recherche web executee', 'detail': 'A=12', 'timestamp': '10:42:00'}
            ],
        },
    }

    response = client.put(f'/api/archive/{sample_geocache}/resolution-diagnostics', json=payload)

    assert response.status_code == 200

    with app.app_context():
        archive = ArchiveService.get_by_gc_code(sample_geocache)
        assert archive is not None
        resume_state = archive['resolution_diagnostics']['resume_state']
        assert resume_state['workflowResolution']['execution']['formula']['answer_search']['answers']['A']['best_answer'] == '12'


def test_update_resolution_diagnostics_builds_history_state(client, app, sample_geocache):
    first_payload = {
        'source': 'plugin_executor_metasolver',
        'updated_at': '2026-03-27T10:00:00Z',
        'resume_state': {
            'updatedAt': '2026-03-27T10:00:00Z',
            'currentText': 'first formula state',
            'recommendationSourceText': 'ABC',
            'workflowResolution': {
                'workflow': {
                    'kind': 'formula',
                    'confidence': 0.81,
                    'score': 0.9,
                    'reason': 'formula',
                    'supporting_labels': ['formula'],
                },
                'workflow_candidates': [],
                'classification': {
                    'source': 'geocache',
                    'geocache': {'id': 1, 'gc_code': sample_geocache, 'name': 'Archive Diagnostic Cache'},
                    'title': 'Archive Diagnostic Cache',
                    'max_secret_fragments': 5,
                    'labels': [{'name': 'formula', 'confidence': 0.81, 'evidence': ['formula']}],
                    'recommended_actions': [],
                    'candidate_secret_fragments': [],
                    'hidden_signals': [],
                    'formula_signals': ['formula'],
                    'signal_summary': {
                        'has_title': True,
                        'has_hint': True,
                        'has_description_html': False,
                        'image_count': 0,
                        'checker_count': 0,
                        'waypoint_count': 0,
                    },
                },
                'plan': [],
                'execution': {},
                'control': {
                    'status': 'ready',
                    'budget': {},
                    'usage': {},
                    'remaining': {},
                    'stop_reasons': [],
                    'can_run_next_step': True,
                    'requires_user_input': False,
                    'final_confidence': 0.72,
                    'summary': 'ready',
                },
                'next_actions': [],
                'explanation': ['formula'],
            },
            'workflowEntries': [
                {'id': 'entry-1', 'category': 'formula', 'message': 'Recherche web executee', 'detail': 'A=12', 'timestamp': '10:00:00'}
            ],
        },
    }
    second_payload = {
        'source': 'plugin_executor_metasolver',
        'updated_at': '2026-03-27T10:05:00Z',
        'resume_state': {
            'updatedAt': '2026-03-27T10:05:00Z',
            'currentText': 'second secret state',
            'recommendationSourceText': '8 5 12 12 15',
            'workflowResolution': {
                'workflow': {
                    'kind': 'secret_code',
                    'confidence': 0.9,
                    'score': 0.95,
                    'reason': 'secret',
                    'supporting_labels': ['secret_code'],
                },
                'workflow_candidates': [],
                'classification': {
                    'source': 'geocache',
                    'geocache': {'id': 1, 'gc_code': sample_geocache, 'name': 'Archive Diagnostic Cache'},
                    'title': 'Archive Diagnostic Cache',
                    'max_secret_fragments': 5,
                    'labels': [{'name': 'secret_code', 'confidence': 0.9, 'evidence': ['compact code']}],
                    'recommended_actions': [],
                    'candidate_secret_fragments': [],
                    'hidden_signals': [],
                    'formula_signals': [],
                    'signal_summary': {
                        'has_title': True,
                        'has_hint': True,
                        'has_description_html': False,
                        'image_count': 0,
                        'checker_count': 0,
                        'waypoint_count': 0,
                    },
                },
                'plan': [],
                'execution': {},
                'control': {
                    'status': 'ready',
                    'budget': {},
                    'usage': {},
                    'remaining': {},
                    'stop_reasons': [],
                    'can_run_next_step': True,
                    'requires_user_input': False,
                    'final_confidence': 0.84,
                    'summary': 'ready',
                },
                'next_actions': [],
                'explanation': ['secret'],
            },
            'workflowEntries': [
                {'id': 'entry-2', 'category': 'secret', 'message': 'Fragment selectionne', 'detail': '8 5 12 12 15', 'timestamp': '10:05:00'}
            ],
        },
    }

    assert client.put(f'/api/archive/{sample_geocache}/resolution-diagnostics', json=first_payload).status_code == 200
    assert client.put(f'/api/archive/{sample_geocache}/resolution-diagnostics', json=second_payload).status_code == 200
    assert client.put(f'/api/archive/{sample_geocache}/resolution-diagnostics', json=second_payload).status_code == 200

    with app.app_context():
        archive = ArchiveService.get_by_gc_code(sample_geocache)
        assert archive is not None
        diagnostics = archive['resolution_diagnostics']
        history_state = diagnostics['history_state']
        assert len(history_state) == 2
        assert history_state[0]['workflow_kind'] == 'secret_code'
        assert history_state[0]['resume_state']['currentText'] == 'second secret state'
        assert history_state[1]['workflow_kind'] == 'formula'
        assert history_state[1]['resume_state']['currentText'] == 'first formula state'
        assert diagnostics['resume_state']['currentText'] == 'second secret state'
