"""Session management helpers for authenticated providers (Geocaching.com)."""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError


class GeocachingSessionManager:
    """Ensures a valid Geocaching.com session in the GeoApp Playwright profile."""

    def __init__(self, profile_dir: Path, timeout_ms: int) -> None:
        self.profile_dir = profile_dir
        self.timeout_ms = timeout_ms

    def is_logged_in(self, headless: bool) -> bool:
        """Returns True if the profile seems logged-in on Geocaching.com."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=headless,
                viewport={'width': 1280, 'height': 900},
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                page.goto('https://www.geocaching.com/account/dashboard', wait_until='domcontentloaded')

                url = (page.url or '').lower()
                if 'signin' in url or 'login' in url:
                    return False

                body = page.locator('body').inner_text(timeout=self.timeout_ms).lower()
                if 'sign in' in body or 'se connecter' in body:
                    return False

                return True
            finally:
                context.close()

    def login_interactive(self, timeout_sec: int = 180) -> bool:
        """Opens a headed browser and waits for the user to login."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=False,
                viewport={'width': 1280, 'height': 900},
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                try:
                    page.goto('https://www.geocaching.com/account/signin', wait_until='domcontentloaded')
                except PlaywrightError:
                    return self.is_logged_in(headless=True)

                deadline = time.time() + timeout_sec
                while time.time() < deadline:
                    try:
                        page.wait_for_timeout(1000)
                    except PlaywrightError:
                        # Window/browser has been closed by the user.
                        return self.is_logged_in(headless=True)
                    current = (page.url or '').lower()
                    if 'signin' not in current and 'login' not in current:
                        # Try to confirm by accessing dashboard
                        try:
                            page.goto('https://www.geocaching.com/account/dashboard', wait_until='domcontentloaded')
                        except Exception:
                            pass
                        return self.is_logged_in(headless=True)

                return self.is_logged_in(headless=True)
            finally:
                context.close()


class CertitudesSessionManager:
    """Ensures a valid Certitudes.org session in the GeoApp Playwright profile."""

    def __init__(self, profile_dir: Path, timeout_ms: int) -> None:
        self.profile_dir = profile_dir
        self.timeout_ms = timeout_ms

    def is_logged_in(self, headless: bool, wp: str | None = None) -> bool:
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        target = 'https://www.certitudes.org/certitude'
        if wp:
            target = f'{target}?wp={wp}'

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=headless,
                viewport={'width': 1280, 'height': 900},
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)

                try:
                    page.goto(target, wait_until='networkidle')
                except Exception:
                    page.goto(target, wait_until='domcontentloaded')

                body = ''
                try:
                    body = page.locator('body').inner_text(timeout=self.timeout_ms).lower()
                except Exception:
                    body = ''

                if 'cloudflare' in body or 'just a moment' in body or 'checking your browser' in body:
                    return False

                try:
                    for frame in page.frames:
                        frame_url = (frame.url or '').lower()
                        if 'challenges.cloudflare.com' in frame_url and 'turnstile' in frame_url:
                            return False
                except Exception:
                    return False

                try:
                    input_loc = page.locator('input[type="text"], textarea').first
                    if input_loc.count() > 0 and input_loc.is_visible():
                        button_loc = page.locator('button:has-text("Certifier"), input[type="submit"], button[type="submit"]').first
                        try:
                            if button_loc.count() > 0 and button_loc.is_enabled():
                                return True
                        except Exception:
                            return False
                except Exception:
                    pass

                return False
            finally:
                context.close()

    def login_interactive(self, wp: str | None = None, timeout_sec: int = 180) -> bool:
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        target = 'https://www.certitudes.org/certitude'
        if wp:
            target = f'{target}?wp={wp}'

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=False,
                viewport={'width': 1280, 'height': 900},
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)

                try:
                    page.goto(target, wait_until='networkidle')
                except Exception:
                    page.goto(target, wait_until='domcontentloaded')

                deadline = time.time() + timeout_sec
                while time.time() < deadline:
                    page.wait_for_timeout(1000)

                    try:
                        body = page.locator('body').inner_text(timeout=self.timeout_ms).lower()
                    except Exception:
                        body = ''

                    if 'cloudflare' in body or 'just a moment' in body or 'checking your browser' in body:
                        continue

                    try:
                        for frame in page.frames:
                            frame_url = (frame.url or '').lower()
                            if 'challenges.cloudflare.com' in frame_url and 'turnstile' in frame_url:
                                raise RuntimeError('turnstile_present')
                    except RuntimeError:
                        continue
                    except Exception:
                        continue

                    try:
                        input_loc = page.locator('input[type="text"], textarea').first
                        button_loc = page.locator('button:has-text("Certifier"), input[type="submit"], button[type="submit"]').first
                        if (
                            input_loc.count() > 0
                            and input_loc.is_visible()
                            and button_loc.count() > 0
                            and button_loc.is_enabled()
                        ):
                            return True
                    except Exception:
                        pass

                return self.is_logged_in(headless=True, wp=wp)
            finally:
                context.close()
