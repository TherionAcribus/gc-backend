"""Base interface for checker adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class CheckerRunResult:
    status: str  # success|failure|unknown
    message: str
    evidence: str | None = None
    extracted: dict[str, Any] = field(default_factory=dict)


class CheckerAdapter(Protocol):
    """Adapter interface for a checker website."""

    def match(self, url: str) -> bool:
        """Returns True if this adapter can handle the given URL."""

    def run(self, page: Any, url: str, input_payload: dict[str, Any], timeout_ms: int) -> CheckerRunResult:
        """Runs the checker on the given page."""
