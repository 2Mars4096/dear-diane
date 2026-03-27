from legacy_utils import normalize_name, ratio


def test_normalize_name():
    assert normalize_name("  Alice ") == "alice"


def test_ratio():
    assert ratio(9, 3) == 3.0
