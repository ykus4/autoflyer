"""Unit tests for bot state persistence."""

import json

from autoflyer.trading.state import STATE_DEFAULT, append_equity, load_state, save_state


class TestLoadState:
    def test_missing_file_returns_default(self, tmp_path):
        assert load_state(tmp_path / "state.json") == STATE_DEFAULT

    def test_missing_keys_are_backfilled(self, tmp_path):
        f = tmp_path / "state.json"
        f.write_text(json.dumps({"in_pos": False}))
        assert load_state(f)["peak_cash"] is None

    def test_corrupt_file_is_backed_up_and_reset(self, tmp_path):
        f = tmp_path / "state.json"
        f.write_text("{not json")
        assert load_state(f) == STATE_DEFAULT
        assert (tmp_path / "state.json.bak").read_text() == "{not json"

    def test_in_pos_without_position_is_reset(self, tmp_path):
        f = tmp_path / "state.json"
        f.write_text(json.dumps({"in_pos": True, "entry_price": None, "btc": 0.0}))
        state = load_state(f)
        assert state["in_pos"] is False
        # 修正後の状態がディスクにも反映される
        assert json.loads(f.read_text())["in_pos"] is False

    def test_btc_without_in_pos_is_zeroed(self, tmp_path):
        f = tmp_path / "state.json"
        f.write_text(json.dumps({"in_pos": False, "btc": 0.5}))
        assert load_state(f)["btc"] == 0.0

    def test_valid_position_is_preserved(self, tmp_path):
        f = tmp_path / "state.json"
        f.write_text(json.dumps({"in_pos": True, "entry_price": 5_000_000, "btc": 0.01}))
        state = load_state(f)
        assert state["in_pos"] is True
        assert state["btc"] == 0.01


class TestSaveState:
    def test_roundtrip(self, tmp_path):
        f = tmp_path / "state.json"
        save_state(f, {**STATE_DEFAULT, "btc": 0.25})
        assert json.loads(f.read_text())["btc"] == 0.25

    def test_no_temp_file_left_behind(self, tmp_path):
        f = tmp_path / "state.json"
        save_state(f, dict(STATE_DEFAULT))
        assert list(tmp_path.iterdir()) == [f]


class TestAppendEquity:
    def test_appends_one_json_line_per_call(self, tmp_path):
        f = tmp_path / "equity.jsonl"
        append_equity(f, 1_000_000.0)
        append_equity(f, 1_100_000.0)
        rows = [json.loads(line) for line in f.read_text().splitlines()]
        assert [r["equity"] for r in rows] == [1_000_000.0, 1_100_000.0]
        assert all("dt" in r for r in rows)
