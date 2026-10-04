"""
Deterministic uncertainty helpers.

The Wilson interval quantifies uncertainty around an observed proportion.
It is not a predictive model and says nothing about future conversion.
"""

from math import sqrt
from typing import Optional, Tuple

from .models import Confidence

Z95 = 1.96


def wilson_interval(n: int, d: int, z: float = Z95) -> Optional[Tuple[float, float]]:
    """95% Wilson score interval for n successes out of d. None when d == 0."""
    if d <= 0:
        return None
    p = n / d
    z2 = z * z
    denom = 1 + z2 / d
    centre = (p + z2 / (2 * d)) / denom
    half = z * sqrt(p * (1 - p) / d + z2 / (4 * d * d)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def sample_cap(n: Optional[int]) -> Confidence:
    """Highest confidence a sample of size n can support."""
    if n is None:
        return Confidence.HIGH  # structural checks are not sample-size limited
    if n < 10:
        return Confidence.VERY_LOW
    if n < 30:
        return Confidence.LOW
    if n < 100:
        return Confidence.MODERATE
    return Confidence.HIGH


def cap_confidence(requested: Confidence, n: Optional[int]) -> Confidence:
    """Never let a rule claim more confidence than the sample size allows."""
    return min(requested, sample_cap(n))
