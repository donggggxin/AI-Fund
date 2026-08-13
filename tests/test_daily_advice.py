import datetime
import unittest

from daily_advice import build_daily_advice


class DailyAdviceTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "000001": {"drop": -0.015, "gap": 0.02, "tp": 0.1, "ratio": 0.5, "tp_ma": 20},
            "ai_agent_settings": {"last_market_stage": "回调期"},
        }
        self.now = datetime.datetime(2026, 8, 13, 14, 30)

    def make_row(self, diag):
        return {
            "code": "000001", "name": "测试基金", "diag": diag,
            "v_hyb": -0.02, "h_yield": 0.12, "est_nav": 1.1,
            "tp": 0.1, "ma": {"MA20": 1.12},
        }

    def test_buy_advice_explains_trigger_and_amount(self):
        guide = build_daily_advice(
            [self.make_row("狙击补仓 +200元")], self.config,
            {"status": "正在开盘", "is_market_closed": False}, self.now,
        )
        item = guide["items"][0]
        self.assertEqual(item["action"], "分批买入")
        self.assertIn("¥200", item["action_detail"])
        self.assertIn("-2.00%", item["reasons"][0])

    def test_sell_advice_explains_target_and_ma(self):
        item = build_daily_advice(
            [self.make_row("破MA20! 建议卖50%")], self.config,
            {"status": "正在开盘", "is_market_closed": False}, self.now,
        )["items"][0]
        self.assertEqual(item["action"], "减仓")
        self.assertIn("50%", item["action_detail"])
        self.assertIn("MA20", item["reasons"][1])

    def test_closed_market_only_recommends_review(self):
        item = build_daily_advice(
            [self.make_row("狙击补仓 +200元")], self.config,
            {"status": "夜间休市", "is_market_closed": True}, self.now,
        )["items"][0]
        self.assertEqual(item["action"], "复盘")


if __name__ == "__main__":
    unittest.main()
