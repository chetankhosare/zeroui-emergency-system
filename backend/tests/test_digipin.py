"""DigiPIN must be the real India Post code — or nothing at all."""

from __future__ import annotations

from digipin_service import (
    decode_digipin,
    digipin_status,
    generate_digipin,
    generate_digipin_with_reason,
    is_within_coverage,
)


def test_generates_official_format_for_india():
    code = generate_digipin(19.8762, 75.3433)  # Chhatrapati Sambhajinagar
    assert code is not None
    assert len(code.replace("-", "")) == 10
    assert code.count("-") == 2


def test_known_reference_values():
    # Values produced by the official digipin package for well-known places
    assert generate_digipin(19.8762, 75.3433) == "4C3-FJ6-PLK3"
    assert generate_digipin(28.6139, 77.2090) == "39J-438-TJC7"


def test_round_trip_decode_is_close():
    code = generate_digipin(28.6139, 77.2090)
    decoded = decode_digipin(code)
    assert decoded is not None
    lat, lng = decoded
    assert abs(lat - 28.6139) < 0.01
    assert abs(lng - 77.2090) < 0.01


def test_outside_india_returns_none_and_reason():
    assert generate_digipin(51.5074, -0.1278) is None  # London
    code, reason = generate_digipin_with_reason(51.5074, -0.1278)
    assert code is None
    assert reason == "outside_india_coverage"


def test_missing_coordinates_are_reported():
    code, reason = generate_digipin_with_reason(None, None)
    assert code is None and reason == "no_coordinates"


def test_coverage_bounds_are_the_official_box():
    assert is_within_coverage(19.8762, 75.3433)
    assert not is_within_coverage(2.0, 63.0)
    assert not is_within_coverage(38.0, 63.0)  # latitude is fine, longitude is not
    status = digipin_status()
    assert status["library_available"] is True
    assert status["coverage"]["lat_min"] == 2.5
