"""
DigiPIN service (India Post digital address)
===========================================

Uses the *official* ``digipin`` package (India Post / IIT Hyderabad algorithm)
to encode latitude/longitude into a 10-character DIGIPIN such as
``4C3-FJ6-PLK3``.

The DIGIPIN grid only covers India: latitude 2.5°–38.5° N and
longitude 63.5°–99.5° E.  Outside that box the standard refuses to encode, and
so do we — the API returns ``None`` plus a reason instead of inventing a code.

No mock or randomised DigiPIN is ever produced.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

try:  # the package is a hard requirement, but never crash the whole API on import
    from digipin import decode as _digipin_decode
    from digipin import encode as _digipin_encode

    DIGIPIN_LIBRARY_AVAILABLE = True
except Exception:  # pragma: no cover - only when dependency is missing
    _digipin_encode = None  # type: ignore[assignment]
    _digipin_decode = None  # type: ignore[assignment]
    DIGIPIN_LIBRARY_AVAILABLE = False
    logger.error("The 'digipin' package is not installed — DigiPIN disabled")

# Official DIGIPIN coverage box (as enforced by the India Post algorithm)
LAT_MIN, LAT_MAX = 2.5, 38.5
LNG_MIN, LNG_MAX = 63.5, 99.5


def is_within_coverage(lat: float, lng: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX


def generate_digipin(lat: Optional[float], lng: Optional[float]) -> Optional[str]:
    """Return the DIGIPIN for a coordinate, or ``None`` when unavailable.

    Availability depends on (a) the library being installed and (b) the
    coordinate falling inside India's DIGIPIN coverage.
    """
    if lat is None or lng is None:
        return None
    if not DIGIPIN_LIBRARY_AVAILABLE:
        return None
    if not is_within_coverage(lat, lng):
        return None
    try:
        return _digipin_encode(lat, lng)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("DigiPIN encode failed for (%s, %s): %s", lat, lng, exc)
        return None


def generate_digipin_with_reason(
    lat: Optional[float], lng: Optional[float]
) -> Tuple[Optional[str], Optional[str]]:
    """Same as :func:`generate_digipin` but also explains unavailability."""
    if lat is None or lng is None:
        return None, "no_coordinates"
    if not DIGIPIN_LIBRARY_AVAILABLE:
        return None, "library_unavailable"
    if not is_within_coverage(lat, lng):
        return None, "outside_india_coverage"
    code = generate_digipin(lat, lng)
    return code, None if code else "encoding_failed"


def decode_digipin(code: str) -> Optional[Tuple[float, float]]:
    """Best-effort reverse lookup (used for validation and tests)."""
    if not DIGIPIN_LIBRARY_AVAILABLE or not code:
        return None
    try:
        lat, lng = _digipin_decode(code)
        return float(lat), float(lng)
    except Exception:
        return None


def digipin_status() -> dict:
    return {
        "library_available": DIGIPIN_LIBRARY_AVAILABLE,
        "coverage": {
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
            "lng_min": LNG_MIN,
            "lng_max": LNG_MAX,
        },
        "format_example": "4C3-FJ6-PLK3",
        "source": "India Post DIGIPIN (digipin PyPI package)",
    }
