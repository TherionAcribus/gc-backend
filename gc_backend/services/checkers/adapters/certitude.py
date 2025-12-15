"""Adapter for Certitude checkers."""

from __future__ import annotations

import re
import time
from typing import Any

from .base import CheckerRunResult


_SUCCESS_RE = re.compile(
    r'\b(f[ée]licitations|r[ée]ponse\s+correcte|bonne\s+r[ée]ponse|bravo|well\s+done|success)\b',
    re.IGNORECASE,
)
_FAILURE_RE = re.compile(
    r'\b(mauvaise\s+r[ée]ponse|r[ée]ponse\s+incorrecte|incorrect|try\s+again|fail)\b',
    re.IGNORECASE,
)

_GC_COORDS_RE = re.compile(
    r'\b[NS]\s*\d{1,2}\s*(?:°\s*)?\d{1,2}\.\d{1,3}\s*[EW]\s*\d{1,3}\s*(?:°\s*)?\d{1,2}\.\d{1,3}\b'
)


class CertitudeAdapter:
    """Certitude adapter (text input)."""

    def match(self, url: str) -> bool:
        url_lower = (url or '').lower()
        return 'certitude' in url_lower or 'certitudes' in url_lower

    def run(self, page: Any, url: str, input_payload: dict[str, Any], timeout_ms: int) -> CheckerRunResult:
        candidate = (input_payload.get('candidate') or input_payload.get('text') or '').strip()
        if not candidate:
            return CheckerRunResult(status='unknown', message='Missing candidate text')

        try:
            page.goto(url, wait_until='networkidle', timeout=timeout_ms)
        except Exception:
            page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)

        def _collect_text() -> str:
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
                        texts.append(frame.locator('body').inner_text(timeout=timeout_ms))
                    except Exception:
                        continue
            except Exception:
                pass
            return '\n'.join([t for t in texts if t])

        body_text = ''
        for _ in range(20):
            body_text = _collect_text()
            lower_text = (body_text or '').lower()
            if 'cloudflare' in lower_text or 'just a moment' in lower_text or 'checking your browser' in lower_text:
                page.wait_for_timeout(1000)
                continue
            break

        extracted: dict[str, Any] = {}
        try:
            extracted['page_url'] = page.url
        except Exception:
            pass
        try:
            extracted['page_title'] = page.title()
        except Exception:
            pass
        try:
            extracted['frames'] = [
                {
                    'name': f.name,
                    'url': f.url,
                }
                for f in page.frames
            ]
        except Exception:
            pass

        initial_text = _collect_text()
        coords_match = _GC_COORDS_RE.search(initial_text or '')
        if coords_match:
            extracted['coordinates_raw'] = coords_match.group(0)

        if initial_text and _SUCCESS_RE.search(initial_text):
            return CheckerRunResult(
                status='success',
                message='Checker accepted the candidate',
                evidence=initial_text.strip()[:2000],
                extracted=extracted or None,
            )

        if initial_text and _FAILURE_RE.search(initial_text):
            return CheckerRunResult(
                status='failure',
                message='Checker rejected the candidate',
                evidence=initial_text.strip()[:2000],
                extracted=extracted or None,
            )

        turnstile_present = False
        try:
            for frame in page.frames:
                frame_url = (frame.url or '').lower()
                if 'challenges.cloudflare.com' in frame_url and 'turnstile' in frame_url:
                    turnstile_present = True
                    break
        except Exception:
            turnstile_present = False

        lower_text = body_text.lower()
        if 'cloudflare' in lower_text or 'just a moment' in lower_text or 'checking your browser' in lower_text:
            return CheckerRunResult(
                status='unknown',
                message='Cloudflare protection detected (manual validation may be required in the GeoApp profile)',
                evidence=body_text.strip()[:2000],
                extracted=extracted or None,
            )

        if turnstile_present:
            return CheckerRunResult(
                status='unknown',
                message='Cloudflare Turnstile challenge present (human verification required before certifying)',
                evidence=body_text.strip()[:2000],
                extracted=extracted or None,
            )

        # Try to locate an input field
        input_locators = [
            'input[type="text"]',
            'input[type="search"]',
            'textarea',
            'input:not([type])'
        ]

        input_el = None
        input_frame = None

        frames = []
        try:
            frames = list(page.frames)
        except Exception:
            frames = [page.main_frame]

        for frame in frames:
            for selector in input_locators:
                loc = frame.locator(selector)
                try:
                    count = loc.count()
                except Exception:
                    continue

                if count <= 0:
                    continue

                limit = min(count, 10)
                for idx in range(limit):
                    candidate_loc = loc.nth(idx)
                    try:
                        if candidate_loc.is_visible():
                            input_el = candidate_loc
                            input_frame = frame
                            break
                    except Exception:
                        continue

                if input_el is not None:
                    break
            if input_el is not None:
                break

        if input_el is None:
            login_url = None
            try:
                login_url = page.locator('a:has-text("sign in"), a:has-text("vous connecter"), a[href*="login"]').first.get_attribute('href')
            except Exception:
                login_url = None
            if login_url:
                extracted['login_url'] = login_url

            content = ''
            try:
                content = page.content()
            except Exception:
                content = ''

            return CheckerRunResult(
                status='unknown',
                message='Unable to find input field',
                evidence=(content or body_text or '')[:2000],
                extracted=extracted or None,
            )

        input_el.fill(candidate, timeout=timeout_ms)

        # Click validation button
        button_selectors = [
            'button:has-text("Vér")',
            'button:has-text("Verifier")',
            'button:has-text("Check")',
            'button:has-text("Tester")',
            'input[type="submit"]',
            'button[type="submit"]'
        ]

        clicked = False
        scope = input_frame if input_frame is not None else page
        for selector in button_selectors:
            btn = scope.locator(selector)
            try:
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=timeout_ms)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            # As last resort, try pressing Enter
            input_el.press('Enter', timeout=timeout_ms)

        # Wait a bit for the result to appear
        page.wait_for_timeout(800)
        body_text = _collect_text() or body_text

        try:
            for frame in page.frames:
                frame_url = (frame.url or '').lower()
                if 'challenges.cloudflare.com' in frame_url and 'turnstile' in frame_url:
                    return CheckerRunResult(
                        status='unknown',
                        message='Cloudflare Turnstile challenge present (human verification required before certifying)',
                        evidence=body_text.strip()[:2000],
                        extracted=extracted or None,
                    )
        except Exception:
            pass

        coords_match = _GC_COORDS_RE.search(body_text)
        if coords_match:
            extracted['coordinates_raw'] = coords_match.group(0)

        status = 'unknown'
        message = 'Unable to determine checker result'

        if _SUCCESS_RE.search(body_text):
            status = 'success'
            message = 'Checker accepted the candidate'
        elif _FAILURE_RE.search(body_text):
            status = 'failure'
            message = 'Checker rejected the candidate'

        evidence = body_text.strip()[:2000]
        return CheckerRunResult(status=status, message=message, evidence=evidence, extracted=extracted)

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

        def _collect_text() -> str:
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
                        texts.append(frame.locator('body').inner_text(timeout=timeout_ms))
                    except Exception:
                        continue
            except Exception:
                pass
            return '\n'.join([t for t in texts if t])

        extracted: dict[str, Any] = {}
        try:
            extracted['page_url'] = page.url
        except Exception:
            pass
        try:
            extracted['page_title'] = page.title()
        except Exception:
            pass
        try:
            extracted['frames'] = [
                {
                    'name': f.name,
                    'url': f.url,
                }
                for f in page.frames
            ]
        except Exception:
            pass

        initial_text = _collect_text()

        input_locators = [
            'input[type="text"]',
            'input[type="search"]',
            'textarea',
            'input:not([type])',
        ]

        def _find_input() -> tuple[Any | None, Any | None]:
            frames: list[Any] = []
            try:
                frames = list(page.frames)
            except Exception:
                frames = [page.main_frame]

            for frame in frames:
                for selector in input_locators:
                    loc = frame.locator(selector)
                    try:
                        count = loc.count()
                    except Exception:
                        continue
                    if count <= 0:
                        continue
                    limit = min(count, 10)
                    for idx in range(limit):
                        candidate_loc = loc.nth(idx)
                        try:
                            if candidate_loc.is_visible():
                                return candidate_loc, frame
                        except Exception:
                            continue
            return None, None

        input_el, input_frame = _find_input()
        if input_el is None:
            deadline_find = time.time() + min(15, max(1, int(timeout_sec)))
            while time.time() < deadline_find and input_el is None:
                page.wait_for_timeout(1000)
                initial_text = _collect_text() or initial_text
                input_el, input_frame = _find_input()

        if input_el is None:
            return CheckerRunResult(
                status='unknown',
                message='Unable to find input field',
                evidence=(initial_text or '').strip()[:2000],
                extracted=extracted or None,
            )

        try:
            input_el.fill(candidate, timeout=timeout_ms)
        except Exception as exc:
            return CheckerRunResult(
                status='unknown',
                message=f'Unable to fill input: {exc}',
                evidence=_collect_text().strip()[:2000],
                extracted=extracted or None,
            )

        scope = input_frame if input_frame is not None else page
        try:
            button = scope.locator('button:has-text("Certifier"), input[type="submit"], button[type="submit"]').first
            if button.count() > 0:
                extracted['certify_button_enabled'] = bool(button.is_enabled())
        except Exception:
            pass

        deadline = time.time() + max(1, int(timeout_sec))
        last_text = ''
        while time.time() < deadline:
            page.wait_for_timeout(1000)
            body_text = _collect_text()
            if body_text:
                last_text = body_text

            coords_match = _GC_COORDS_RE.search(body_text or '')
            if coords_match:
                extracted['coordinates_raw'] = coords_match.group(0)

            if body_text and _SUCCESS_RE.search(body_text):
                return CheckerRunResult(
                    status='success',
                    message='Checker accepted the candidate',
                    evidence=body_text.strip()[:2000],
                    extracted=extracted or None,
                )

            if body_text and _FAILURE_RE.search(body_text):
                return CheckerRunResult(
                    status='failure',
                    message='Checker rejected the candidate',
                    evidence=body_text.strip()[:2000],
                    extracted=extracted or None,
                )

        return CheckerRunResult(
            status='unknown',
            message='Timed out waiting for manual certification',
            evidence=(last_text or '').strip()[:2000],
            extracted=extracted or None,
        )
