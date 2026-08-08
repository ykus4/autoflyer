"""Unit tests for timeframe label parsing."""

import pytest

from autoflyer.timeframes import parse, to_minutes, to_pandas_rule


class TestParse:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [("1H", (1, "H")), ("12h", (12, "H")), ("1D", (1, "D")), ("3D", (3, "D")), ("D", (1, "D"))],
    )
    def test_valid_labels(self, label, expected):
        assert parse(label) == expected

    def test_whitespace_and_case_are_tolerated(self):
        assert parse("  6h ") == (6, "H")

    @pytest.mark.parametrize("label", ["1M", "", "5", "1DD", "abc"])
    def test_invalid_labels_raise(self, label):
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            parse(label)


class TestToPandasRule:
    @pytest.mark.parametrize(
        ("label", "rule"),
        [("1H", "1h"), ("3H", "3h"), ("6H", "6h"), ("12H", "12h"), ("1D", "1D"), ("3D", "3D")],
    )
    def test_rules(self, label, rule):
        assert to_pandas_rule(label) == rule


class TestToMinutes:
    @pytest.mark.parametrize(
        ("label", "minutes"),
        [("1H", 60), ("4H", 240), ("1D", 1440), ("3D", 4320)],
    )
    def test_minutes(self, label, minutes):
        assert to_minutes(label) == minutes
