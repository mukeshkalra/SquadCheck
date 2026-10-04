"""
Data model for the Growth Analyst report.

Structural guarantees (enforced at construction, covered by tests):
  - A Finding must carry at least one Evidence reference.
  - A RateStat must carry its time window and denominator; it cannot be built without them.
  - Hypotheses are a separate type and are never rendered as Findings.
"""

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Optional, Tuple


class Confidence(IntEnum):
    VERY_LOW = 0
    LOW = 1
    MODERATE = 2
    HIGH = 3

    @property
    def label(self):
        return self.name.replace("_", " ").lower()


class EventClass(str, Enum):
    CORE = "core_funnel"          # missing => possible instrumentation failure
    CONDITIONAL = "conditional"   # branch/error paths: only fire if the path is taken
    BEHAVIOURAL = "behavioural"   # optional user actions: only fire if the user chooses


@dataclass(frozen=True)
class Window:
    """Time window a statistic was computed over, and which tool produced it."""
    lookback_days: int
    source: str  # e.g. "get_funnel"

    def __post_init__(self):
        if not isinstance(self.lookback_days, int) or self.lookback_days <= 0:
            raise ValueError(f"Window.lookback_days must be a positive int, got {self.lookback_days!r}")
        if not self.source:
            raise ValueError("Window.source is required")

    def __str__(self):
        return f"last {self.lookback_days}d, {self.source}"


@dataclass(frozen=True)
class Evidence:
    """Pointer into a tool output: a dotted path and the value found there."""
    source: str   # "get_funnel" | "get_event_health"
    path: str     # e.g. "funnel.uploaded.n"
    value: Any

    def __str__(self):
        return f"{self.source}:{self.path}={self.value}"


@dataclass(frozen=True)
class RateStat:
    """An observed proportion n/d, always reported with its window and a Wilson interval."""
    name: str
    n: int
    d: int
    window: Window
    ci: Optional[Tuple[float, float]] = None  # (low, high) as fractions; None when d == 0

    def __post_init__(self):
        if self.window is None:
            raise ValueError(f"RateStat {self.name!r} requires a window")
        if self.d is None or self.n is None or self.n < 0 or self.d < 0 or self.n > self.d:
            raise ValueError(f"RateStat {self.name!r} has invalid n/d: {self.n}/{self.d}")

    def render(self):
        if self.d == 0:
            return f"{self.name}: n/a (0 denominator) [{self.window}]"
        pct = self.n / self.d * 100
        ci = f", 95% CI {self.ci[0] * 100:.0f}-{self.ci[1] * 100:.0f}%" if self.ci else ""
        return f"{self.name}: {self.n}/{self.d} ({pct:.1f}%{ci}) [{self.window}]"


@dataclass(frozen=True)
class Finding:
    id: str
    text: str
    evidence: Tuple[Evidence, ...]
    rates: Tuple[RateStat, ...] = ()
    n: Optional[int] = None  # sample size backing the claim; None for structural checks

    def __post_init__(self):
        if not self.evidence:
            raise ValueError(f"Finding {self.id!r} has no evidence reference")


@dataclass(frozen=True)
class Hypothesis:
    id: str
    text: str
    from_finding: str  # id of the observation/gap that prompted it
    would_test: str    # data or query that could confirm/refute it


@dataclass(frozen=True)
class ConfidenceEntry:
    conclusion: str
    confidence: Confidence
    basis: str
    n: Optional[int] = None


@dataclass
class Report:
    facts: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    hypotheses: list = field(default_factory=list)
    confidence: list = field(default_factory=list)
    next_investigation: str = ""
