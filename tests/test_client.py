"""Tests for BitFlyerClient helpers (no network)."""

import hashlib
import hmac

from autoflyer.trading.client import BitFlyerClient, free_amount, net_position


def test_net_position_long_weighted_average():
    side, size, avg = net_position(
        [
            {"side": "BUY", "size": 0.1, "price": 100},
            {"side": "BUY", "size": 0.3, "price": 200},
        ]
    )
    assert side == "long" and abs(size - 0.4) < 1e-12 and avg == 175


def test_net_position_short_and_flat():
    assert net_position([{"side": "SELL", "size": 0.2, "price": 50}])[:2] == ("short", 0.2)
    assert net_position([]) == (None, 0.0, 0.0)


def test_free_amount_missing_currency():
    assert free_amount({"JPY": {"free": 5.0}}, "BTC") == 0.0
    assert free_amount({"JPY": {"free": 5.0}}, "JPY") == 5.0


def test_private_get_signs_query_string(monkeypatch):
    client = BitFlyerClient("key", "secret")
    seen = {}

    class Resp:
        content = b"[]"

        def raise_for_status(self):
            pass

        def json(self):
            return []

    def fake_get(url, headers, timeout):
        seen["url"], seen["headers"] = url, headers
        return Resp()

    monkeypatch.setattr(client._session, "get", fake_get)
    client.fetch_positions("FX_BTC_JPY")

    path = "/v1/me/getpositions?product_code=FX_BTC_JPY"
    assert seen["url"].endswith(path)
    h = seen["headers"]
    expected = hmac.new(
        b"secret", (h["ACCESS-TIMESTAMP"] + "GET" + path).encode(), hashlib.sha256
    ).hexdigest()
    assert h["ACCESS-SIGN"] == expected
