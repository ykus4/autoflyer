"""Tests for variant lookup."""

import pytest

from autoflyer.trading.strategy import VARIANTS, get_variant, select_variants


def test_get_variant_by_name():
    assert get_variant("BASE").name == "BASE"


def test_get_variant_unknown_exits():
    with pytest.raises(SystemExit, match="Unknown variant: NOPE"):
        get_variant("NOPE")


def test_select_variants_defaults_to_all():
    assert select_variants(None) == VARIANTS
    assert select_variants([]) == VARIANTS


def test_select_variants_keeps_definition_order():
    names = [v.name for v in select_variants(["STOP_3ATR", "BASE"])]
    assert names == ["BASE", "STOP_3ATR"]


def test_select_variants_reports_all_unknown():
    with pytest.raises(SystemExit, match="A, B"):
        select_variants(["BASE", "B", "A"])
