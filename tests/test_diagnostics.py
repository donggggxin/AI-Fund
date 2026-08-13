import datetime
import sys
import types
import unittest
from unittest.mock import patch

from diagnostics import (
    compute_fund_diagnostic,
    fetch_market_prices_from_eltdx,
    fetch_market_prices_from_stock_sdk,
    get_market_status,
)


class MarketStatusTests(unittest.TestCase):
    def test_weekend_is_closed(self):
        status, _, closed = get_market_status({}, datetime.datetime(2026, 8, 15, 10))
        self.assertEqual(status, "节假休市")
        self.assertTrue(closed)

    def test_trading_session_is_open(self):
        status, _, closed = get_market_status({}, datetime.datetime(2026, 8, 13, 10))
        self.assertEqual(status, "正在开盘")
        self.assertFalse(closed)


class DiagnosticTests(unittest.TestCase):
    @patch.dict("os.environ", {"ELTDX_ENABLED": "1"})
    def test_eltdx_percent_is_normalized(self):
        quote = types.SimpleNamespace(full_code="sh588000", change_pct=1.5)

        class FakeClient:
            def __init__(self, **_kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def get_quote(self, _symbols): return [quote]

        fake_module = types.SimpleNamespace(TdxClient=FakeClient)
        with patch.dict(sys.modules, {"eltdx": fake_module}):
            self.assertEqual(
                fetch_market_prices_from_eltdx(["sh588000"]),
                {"sh588000": 0.015},
            )

    @patch.dict("os.environ", {"STOCK_SDK_URL": "http://stock-data:3000"})
    @patch("diagnostics.requests.post")
    def test_stock_sdk_percent_is_normalized(self, request_post):
        request_post.return_value.raise_for_status.return_value = None
        request_post.return_value.json.return_value = {
            "quotes": [{"symbol": "sh588000", "changePercent": 2.5}]
        }
        self.assertEqual(
            fetch_market_prices_from_stock_sdk(["sh588000"]),
            {"sh588000": 0.025},
        )

    @patch("diagnostics.fetch_realtime_fund_flow", return_value=None)
    @patch("diagnostics.requests.get")
    def test_drop_signal_respects_space_lock(self, request_get, _flow):
        request_get.side_effect = RuntimeError("offline")
        config = {
            "000001": {
                "name": "测试基金", "cost": 1.0, "shares": 1000,
                "proxy": "sh500000", "drop": -0.02, "gap": 0.03,
                "cap": 500, "multiplier": 1.5, "tp": 0.1,
            }
        }
        row = compute_fund_diagnostic(
            "000001", config, {"000001": 1.0}, {"000001": "2026-08-12"},
            {"sh500000": -0.025}, {}, {}, "2026-08-13", False,
        )
        self.assertTrue(row["diag"].startswith("空间锁拦截"))

    @patch("diagnostics.fetch_realtime_fund_flow", return_value=None)
    @patch("diagnostics.requests.get")
    def test_take_profit_below_ma_suggests_sell(self, request_get, _flow):
        request_get.side_effect = RuntimeError("offline")
        config = {
            "000001": {
                "name": "测试基金", "cost": 1.0, "shares": 1000,
                "proxy": "sh500000", "tp": 0.1, "ratio": 0.33, "tp_ma": 5,
            }
        }
        row = compute_fund_diagnostic(
            "000001", config, {"000001": 1.2}, {"000001": "2026-08-13"},
            {}, {}, {"000001": {"MA5": 1.25}}, "2026-08-13", False,
        )
        self.assertEqual(row["diag"], "破MA5! 建议卖33%")


if __name__ == "__main__":
    unittest.main()
