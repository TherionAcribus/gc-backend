"""Flask blueprint exposing endpoints to run external checkers (Certitude, Geocaching, etc.)."""

from __future__ import annotations

import logging
import time
import shutil
from pathlib import Path

from flask import Blueprint, jsonify, request

from ..services.checkers.runner import CheckerRunner
from ..services.checkers.session import GeocachingSessionManager, CertitudesSessionManager
from ..services.checkers.storage import get_default_profile_dir
from ..utils.preferences import get_value_or_default

bp = Blueprint('checkers', __name__)
logger = logging.getLogger(__name__)


def _is_checkers_enabled() -> bool:
    return bool(get_value_or_default('geoApp.checkers.enabled', True))


def _build_runner() -> CheckerRunner:
    headless = bool(get_value_or_default('geoApp.checkers.playwright.headless', True))
    timeout_ms = int(get_value_or_default('geoApp.checkers.timeoutMs', 20000))
    max_attempts = int(get_value_or_default('geoApp.checkers.maxAttempts', 2))

    profile_dir_raw = get_value_or_default('geoApp.checkers.profileDir', '')
    profile_dir = Path(profile_dir_raw) if profile_dir_raw else get_default_profile_dir()

    allowed_domains = get_value_or_default('geoApp.checkers.allowedDomains', None)

    return CheckerRunner(
        profile_dir=profile_dir,
        headless=headless,
        timeout_ms=timeout_ms,
        max_attempts=max_attempts,
        allowed_domains=allowed_domains,
    )


@bp.post('/api/checkers/run')
def run_checker():
    """Runs a checker and returns a normalized result."""
    if not _is_checkers_enabled():
        return jsonify({'status': 'error', 'error': 'checkers_disabled'}), 403

    payload = request.get_json(silent=True, force=True) or {}
    url = (payload.get('url') or '').strip()
    input_payload = payload.get('input') or {}

    if not url:
        return jsonify({'status': 'error', 'error': 'Missing required field: url'}), 400

    runner = _build_runner()

    try:
        result = runner.run(url=url, input_payload=input_payload)
        return jsonify({'status': 'success', 'result': result})
    except ValueError as exc:
        return jsonify({'status': 'error', 'error': str(exc)}), 400
    except Exception as exc:
        logger.error('Checker run failed: %s', exc, exc_info=True)
        return jsonify({'status': 'error', 'error': 'Checker run failed'}), 500


@bp.post('/api/checkers/run-interactive')
def run_checker_interactive():
    if not _is_checkers_enabled():
        return jsonify({'status': 'error', 'error': 'checkers_disabled'}), 403

    started_at = time.perf_counter()
    payload = request.get_json(silent=True, force=True) or {}
    url = (payload.get('url') or '').strip()
    input_payload = payload.get('input') or {}
    timeout_sec = int(payload.get('timeout_sec') or 300)

    if not url:
        return jsonify({'status': 'error', 'error': 'Missing required field: url'}), 400

    runner = _build_runner()

    try:
        logger.info(
            'Checker run-interactive start url=%s timeout_sec=%s input_keys=%s',
            url,
            timeout_sec,
            sorted(list(input_payload.keys())) if isinstance(input_payload, dict) else None,
        )
        result = runner.run_interactive(url=url, input_payload=input_payload, timeout_sec=timeout_sec)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            'Checker run-interactive done url=%s duration_ms=%s status=%s',
            url,
            duration_ms,
            getattr(result, 'status', None) if hasattr(result, 'status') else None,
        )
        return jsonify({'status': 'success', 'result': result})
    except ValueError as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.warning('Checker run-interactive failed (bad request) url=%s duration_ms=%s error=%s', url, duration_ms, exc)
        return jsonify({'status': 'error', 'error': str(exc)}), 400
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.error('Checker run interactive failed: %s', exc, exc_info=True)
        logger.error('Checker run-interactive failed url=%s duration_ms=%s', url, duration_ms)
        return jsonify({'status': 'error', 'error': 'Checker run interactive failed'}), 500


@bp.post('/api/checkers/session/ensure')
def ensure_session():
    """Checks whether a provider session is available (currently Geocaching)."""
    if not _is_checkers_enabled():
        return jsonify({'status': 'error', 'error': 'checkers_disabled'}), 403

    payload = request.get_json(silent=True, force=True) or {}
    provider = (payload.get('provider') or 'geocaching').strip().lower()
    wp = (payload.get('wp') or '').strip() or None

    profile_dir_raw = get_value_or_default('geoApp.checkers.profileDir', '')
    profile_dir = Path(profile_dir_raw) if profile_dir_raw else get_default_profile_dir()

    headless = bool(get_value_or_default('geoApp.checkers.playwright.headless', True))
    timeout_ms = int(get_value_or_default('geoApp.checkers.timeoutMs', 20000))

    if provider == 'geocaching':
        manager = GeocachingSessionManager(profile_dir=profile_dir, timeout_ms=timeout_ms)
        call = lambda: manager.is_logged_in(headless=headless)
    elif provider == 'certitudes':
        manager = CertitudesSessionManager(profile_dir=profile_dir, timeout_ms=timeout_ms)
        call = lambda: manager.is_logged_in(headless=headless, wp=wp)
    else:
        return jsonify({'status': 'error', 'error': f'Unsupported provider: {provider}'}), 400

    try:
        logged_in = call()
        return jsonify({'status': 'success', 'provider': provider, 'logged_in': logged_in})
    except Exception as exc:
        logger.error('Ensure session failed: %s', exc, exc_info=True)
        return jsonify({'status': 'error', 'error': 'Failed to check session'}), 500


@bp.post('/api/checkers/session/login')
def login_session():
    """Opens a headed browser window and waits for user login (currently Geocaching)."""
    if not _is_checkers_enabled():
        return jsonify({'status': 'error', 'error': 'checkers_disabled'}), 403

    payload = request.get_json(silent=True, force=True) or {}
    provider = (payload.get('provider') or 'geocaching').strip().lower()
    timeout_sec = int(payload.get('timeout_sec') or 180)
    wp = (payload.get('wp') or '').strip() or None

    profile_dir_raw = get_value_or_default('geoApp.checkers.profileDir', '')
    profile_dir = Path(profile_dir_raw) if profile_dir_raw else get_default_profile_dir()

    timeout_ms = int(get_value_or_default('geoApp.checkers.timeoutMs', 20000))

    if provider == 'geocaching':
        manager = GeocachingSessionManager(profile_dir=profile_dir, timeout_ms=timeout_ms)
        call = lambda: manager.login_interactive(timeout_sec=timeout_sec)
    elif provider == 'certitudes':
        manager = CertitudesSessionManager(profile_dir=profile_dir, timeout_ms=timeout_ms)
        call = lambda: manager.login_interactive(wp=wp, timeout_sec=timeout_sec)
    else:
        return jsonify({'status': 'error', 'error': f'Unsupported provider: {provider}'}), 400

    try:
        logged_in = call()
        return jsonify({'status': 'success', 'provider': provider, 'logged_in': logged_in})
    except Exception as exc:
        logger.error('Login session failed: %s', exc, exc_info=True)
        return jsonify({'status': 'error', 'error': 'Failed to login'}), 500


@bp.post('/api/checkers/session/reset')
def reset_session():
    """Deletes the GeoApp Playwright profile directory. Requires confirm=true."""
    if not _is_checkers_enabled():
        return jsonify({'status': 'error', 'error': 'checkers_disabled'}), 403

    payload = request.get_json(silent=True, force=True) or {}
    confirm = bool(payload.get('confirm'))

    if not confirm:
        return jsonify({'status': 'error', 'error': 'Missing confirm=true'}), 400

    profile_dir_raw = get_value_or_default('geoApp.checkers.profileDir', '')
    profile_dir = Path(profile_dir_raw) if profile_dir_raw else get_default_profile_dir()

    try:
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
        return jsonify({'status': 'success', 'deleted': True, 'profile_dir': str(profile_dir)})
    except Exception as exc:
        logger.error('Reset session failed: %s', exc, exc_info=True)
        return jsonify({'status': 'error', 'error': 'Failed to reset session'}), 500
