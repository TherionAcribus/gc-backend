"""Playwright-based runner to execute checker adapters with a persistent GeoApp profile."""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .adapters.base import CheckerAdapter, CheckerRunResult
from .adapters.certitude import CertitudeAdapter
from .adapters.geocaching_solution_checker import GeocachingSolutionCheckerAdapter

logger = logging.getLogger(__name__)


class CheckerRunner:
    """Runs a checker URL using the first matching adapter."""

    def __init__(
        self,
        profile_dir: Path,
        headless: bool,
        timeout_ms: int,
        max_attempts: int,
        allowed_domains: Any = None,
    ) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.max_attempts = max_attempts
        self.allowed_domains_raw = allowed_domains

        self.adapters: list[CheckerAdapter] = [
            GeocachingSolutionCheckerAdapter(),
            CertitudeAdapter(),
        ]

    def run(self, url: str, input_payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_url(url)

        adapter = self._select_adapter(url)
        if not adapter:
            raise ValueError('unsupported_checker_url')

        last_error: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                logger.info('Running checker (attempt %s/%s): %s', attempt, self.max_attempts, url)
                result = self._run_once(adapter, url, input_payload)
                return asdict(result)
            except (PlaywrightTimeoutError, TimeoutError) as exc:
                last_error = f'timeout: {exc}'
                logger.warning('Checker attempt timed out: %s', exc)
            except Exception as exc:
                last_error = str(exc)
                logger.error('Checker attempt failed: %s', exc, exc_info=True)

        return asdict(
            CheckerRunResult(
                status='unknown',
                message='Checker run failed after retries',
                evidence=last_error,
            )
        )

    def run_interactive(
        self,
        url: str,
        input_payload: dict[str, Any],
        timeout_sec: int,
        keep_open: bool = False,
    ) -> dict[str, Any]:
        self._validate_url(url)

        adapter = self._select_adapter(url)
        if not adapter:
            raise ValueError('unsupported_checker_url')

        if not hasattr(adapter, 'run_interactive'):
            raise ValueError('unsupported_interactive_mode')

        self.profile_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=False,
                viewport={'width': 1280, 'height': 900},
                args=['--disable-blink-features=AutomationControlled'],
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                try:
                    page.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
                    )
                except Exception:
                    pass

                result = adapter.run_interactive(
                    page=page,
                    url=url,
                    input_payload=input_payload,
                    timeout_ms=self.timeout_ms,
                    timeout_sec=int(timeout_sec),
                )
                payload = asdict(result)

                if keep_open:
                    logger.info('Keeping checker page open until the user closes the browser window: %s', url)
                    try:
                        context.wait_for_event('close', timeout=0)
                    except PlaywrightError:
                        pass
                    except Exception:
                        pass

                return payload
            finally:
                try:
                    context.close()
                except Exception:
                    pass

    def _run_once(self, adapter: CheckerAdapter, url: str, input_payload: dict[str, Any]) -> CheckerRunResult:
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                viewport={'width': 1280, 'height': 900},
                args=['--disable-blink-features=AutomationControlled'],
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                try:
                    page.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
                    )
                except Exception:
                    pass
                return adapter.run(page=page, url=url, input_payload=input_payload, timeout_ms=self.timeout_ms)
            finally:
                context.close()

    def _select_adapter(self, url: str) -> CheckerAdapter | None:
        for adapter in self.adapters:
            if adapter.match(url):
                return adapter
        return None

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise ValueError('invalid_url_scheme')
        if not parsed.netloc:
            raise ValueError('invalid_url')

        allowed = self._parse_allowed_domains(self.allowed_domains_raw)
        if allowed and parsed.hostname and parsed.hostname.lower() not in allowed:
            raise ValueError('domain_not_allowed')

    def _parse_allowed_domains(self, raw: Any) -> set[str]:
        if raw is None:
            return set()
        if isinstance(raw, (list, tuple, set)):
            return {str(x).strip().lower() for x in raw if str(x).strip()}
        text = str(raw).strip()
        if not text:
            return set()
        return {part.strip().lower() for part in text.split(',') if part.strip()}
