from polizia_locale.cli import _effective_expensive_limit


def test_effective_expensive_limit_keeps_small_regions_unbounded():
    assert _effective_expensive_limit(273, 0) == 0


def test_effective_expensive_limit_caps_large_regions():
    assert _effective_expensive_limit(301, 0) == 120
    assert _effective_expensive_limit(620, 0) == 90
    assert _effective_expensive_limit(900, 0) == 60


def test_effective_expensive_limit_respects_user_override():
    assert _effective_expensive_limit(1000, 25) == 25