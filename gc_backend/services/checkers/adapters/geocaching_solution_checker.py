"""Adapter for the Geocaching.com built-in Solution Checker."""

from __future__ import annotations

import re
import time
from typing import Any

from .base import CheckerRunResult


_SUCCESS_RE = re.compile(r"\b(correct|bonne\s+r[ée]ponse|bravo)\b", re.IGNORECASE)
_FAILURE_RE = re.compile(r"\b(incorrect|mauvaise\s+r[ée]ponse|try\s+again)\b", re.IGNORECASE)

_GC_COORDS_RE = re.compile(
    r"\b[NS]\s*\d{1,2}\s*(?:°\s*)?\d{1,2}\.\d{1,3}\s*[EW]\s*\d{1,3}\s*(?:°\s*)?\d{1,2}\.\d{1,3}\b"
)


class GeocachingSolutionCheckerAdapter:
    """Geocaching.com built-in Solution Checker.

    This checker typically requires a logged-in session and may display a Google reCAPTCHA.
    In that case, automation must run in interactive mode.
    """

    def match(self, url: str) -> bool:
        url_lower = (url or '').lower()
        if 'geocaching.com' not in url_lower:
            return False
        return '/geocache/' in url_lower or 'cache_details.aspx' in url_lower

    def run(self, page: Any, url: str, input_payload: dict[str, Any], timeout_ms: int) -> CheckerRunResult:
        candidate = (input_payload.get('candidate') or input_payload.get('text') or '').strip()
        if not candidate:
            return CheckerRunResult(status='unknown', message='Missing candidate text')

        try:
            page.goto(url, wait_until='networkidle', timeout=timeout_ms)
        except Exception:
            page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)

        if page.locator('#ctl00_ContentBody_uxCacheChecker').count() <= 0:
            return CheckerRunResult(status='unknown', message='No Solution Checker found on this page')

        if page.locator('#ctl00_ContentBody_divRecaptcha, .g-recaptcha').count() > 0:
            return CheckerRunResult(
                status='unknown',
                message='reCAPTCHA detected; use interactive mode',
                evidence=self._collect_text(page, timeout_ms).strip()[:2000],
            )

        return self._attempt_check(page=page, candidate=candidate, timeout_ms=timeout_ms, timeout_sec=10)

    def run_interactive(
        self,
        page: Any,
        url: str,
        input_payload: dict[str, Any],
        timeout_ms: int,
        timeout_sec: int,
    ) -> CheckerRunResult:
        candidate = (input_payload.get('candidate') or input_payload.get('text') or '').strip()
        if not candidate:
            return CheckerRunResult(status='unknown', message='Missing candidate text')

        try:
            page.goto(url, wait_until='networkidle', timeout=timeout_ms)
        except Exception:
            page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)

        if page.locator('#ctl00_ContentBody_uxCacheChecker').count() <= 0:
            return CheckerRunResult(status='unknown', message='No Solution Checker found on this page')

        extracted: dict[str, Any] = {}
        try:
            extracted['page_url'] = page.url
        except Exception:
            pass

        initial_text = self._collect_text(page, timeout_ms)

        input_loc = page.locator('#ctl00_ContentBody_txtSolutionInput, input.solution-input').first
        if input_loc.count() <= 0:
            return CheckerRunResult(
                status='unknown',
                message='Unable to find solution input field',
                evidence=(initial_text or '').strip()[:2000],
                extracted=extracted,
            )

        try:
            input_loc.fill(candidate, timeout=timeout_ms)
        except Exception as exc:
            return CheckerRunResult(
                status='unknown',
                message=f'Unable to fill input: {exc}',
                evidence=(initial_text or '').strip()[:2000],
                extracted=extracted,
            )

        button_loc = page.locator('#CheckerButton, input#CheckerButton').first
        if button_loc.count() > 0:
            try:
                extracted['check_button_enabled'] = bool(button_loc.is_enabled())
            except Exception:
                pass

        try:
            if button_loc.count() > 0 and button_loc.is_enabled():
                button_loc.click(timeout=timeout_ms)
        except Exception:
            # User may need to solve reCAPTCHA before the click can succeed.
            pass

        deadline = time.time() + max(1, int(timeout_sec))
        last_text = initial_text
        saw_recaptcha_error = False

        while time.time() < deadline:
            page.wait_for_timeout(1000)

            result = self._read_solution_result(page, timeout_ms)
            if result['raw_text']:
                last_text = result['raw_text']

            if result.get('captcha_present') and result.get('response_class') and 'solution-error' in str(result.get('response_class')).lower():
                saw_recaptcha_error = True

            coords_raw = self._extract_coords(result)
            if coords_raw:
                extracted['coordinates_raw'] = coords_raw

            if result['status'] == 'success':
                return CheckerRunResult(
                    status='success',
                    message='Solution Checker accepted the candidate',
                    evidence=result['evidence'],
                    extracted=extracted,
                )

            if result['status'] == 'failure':
                return CheckerRunResult(
                    status='failure',
                    message='Solution Checker rejected the candidate',
                    evidence=result['evidence'],
                    extracted=extracted,
                )

        return CheckerRunResult(
            status='unknown',
            message=(
                'Timed out waiting for Solution Checker result (solve/refresh reCAPTCHA and click “Check Solution”)'
                if saw_recaptcha_error
                else 'Timed out waiting for Solution Checker result (solve reCAPTCHA and click “Check Solution”)'
            ),
            evidence=(last_text or '').strip()[:2000],
            extracted=extracted,
        )

    def _attempt_check(self, page: Any, candidate: str, timeout_ms: int, timeout_sec: int) -> CheckerRunResult:
        input_loc = page.locator('#ctl00_ContentBody_txtSolutionInput, input.solution-input').first
        if input_loc.count() <= 0:
            return CheckerRunResult(
                status='unknown',
                message='Unable to find solution input field',
                evidence=self._collect_text(page, timeout_ms).strip()[:2000],
            )

        try:
            input_loc.fill(candidate, timeout=timeout_ms)
        except Exception as exc:
            return CheckerRunResult(
                status='unknown',
                message=f'Unable to fill input: {exc}',
                evidence=self._collect_text(page, timeout_ms).strip()[:2000],
            )

        button_loc = page.locator('#CheckerButton, input#CheckerButton').first
        try:
            if button_loc.count() > 0 and button_loc.is_enabled():
                button_loc.click(timeout=timeout_ms)
        except Exception:
            pass

        deadline = time.time() + max(1, int(timeout_sec))
        last_text = ''

        while time.time() < deadline:
            page.wait_for_timeout(1000)
            result = self._read_solution_result(page, timeout_ms)
            if result['raw_text']:
                last_text = result['raw_text']
            coords_raw = self._extract_coords(result)
            extracted: dict[str, Any] = {}
            if coords_raw:
                extracted['coordinates_raw'] = coords_raw
            if result['status'] in {'success', 'failure'}:
                return CheckerRunResult(
                    status=result['status'],
                    message=(
                        'Solution Checker accepted the candidate'
                        if result['status'] == 'success'
                        else 'Solution Checker rejected the candidate'
                    ),
                    evidence=result['evidence'],
                    extracted=extracted,
                )

        return CheckerRunResult(
            status='unknown',
            message='Unable to determine Solution Checker result',
            evidence=(last_text or self._collect_text(page, timeout_ms)).strip()[:2000],
        )

    def _extract_coords(self, result: dict[str, Any]) -> str | None:
        lat = (result.get('lat') or '').strip()
        lon = (result.get('lon') or '').strip()
        if lat and lon:
            return f'{lat} {lon}'.strip()

        m = _GC_COORDS_RE.search(result.get('raw_text') or '')
        if m:
            return m.group(0)
        return None

    def _read_solution_result(self, page: Any, timeout_ms: int) -> dict[str, Any]:
        response_text = ''
        response_class = ''
        lat_text = ''
        lon_text = ''
        captcha_present = False

        try:
            response_text = (page.locator('#lblSolutionResponse').inner_text(timeout=2000) or '').strip()
        except Exception:
            response_text = ''

        try:
            response_class = (page.locator('#lblSolutionResponse').get_attribute('class', timeout=2000) or '').strip()
        except Exception:
            response_class = ''

        try:
            captcha_loc = page.locator('#ctl00_ContentBody_divRecaptcha, .g-recaptcha, iframe[src*="recaptcha"]').first
            if captcha_loc.count() > 0:
                try:
                    captcha_present = bool(captcha_loc.is_visible())
                except Exception:
                    captcha_present = True
        except Exception:
            captcha_present = False

        try:
            lat_text = (page.locator('#solution-lat').inner_text(timeout=2000) or '').strip()
        except Exception:
            lat_text = ''

        try:
            lon_text = (page.locator('#solution-lon').inner_text(timeout=2000) or '').strip()
        except Exception:
            lon_text = ''

        combined = ' '.join([t for t in [response_text, lat_text, lon_text] if t]).strip()
        evidence = combined or self._collect_text(page, timeout_ms)

        status = 'unknown'
        response_class_lower = (response_class or '').lower()
        if 'solution-success' in response_class_lower:
            status = 'success'
        elif 'solution-error' in response_class_lower:
            status = 'unknown' if captcha_present else 'failure'
        else:
            status_text = (response_text or combined or '').strip()
            if status_text:
                if _SUCCESS_RE.search(status_text):
                    status = 'success'
                elif _FAILURE_RE.search(status_text):
                    status = 'unknown' if captcha_present else 'failure'

        return {
            'status': status,
            'evidence': (evidence or '').strip()[:2000],
            'raw_text': (combined or '').strip(),
            'lat': lat_text,
            'lon': lon_text,
            'captcha_present': captcha_present,
            'response_class': response_class,
        }

    def _collect_text(self, page: Any, timeout_ms: int) -> str:
        texts: list[str] = []
        try:
            texts.append(page.locator('body').inner_text(timeout=timeout_ms))
        except Exception:
            pass
        try:
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                try:
                    texts.append(frame.locator('body').inner_text(timeout=2000))
                except Exception:
                    continue
        except Exception:
            pass
        return '\n'.join([t for t in texts if t])
