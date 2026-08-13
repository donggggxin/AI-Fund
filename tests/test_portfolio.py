import tempfile
import unittest
from pathlib import Path

from database import list_portfolio_snapshots, upsert_portfolio_snapshot
from portfolio import summarize_portfolio


class PortfolioTests(unittest.TestCase):
    def test_summary_and_allocation(self):
        rows = [
            {"code": "000001", "name": "A", "shares": 100, "est_nav": 1.2, "cost": 1, "v_hyb": 0.01},
            {"code": "000002", "name": "B", "shares": 50, "est_nav": 2, "cost": 2.2, "v_hyb": -0.02},
        ]
        summary = summarize_portfolio(
            rows, {"000001": {"tag": "科创"}, "000002": {"tag": "黄金"}}
        )
        self.assertEqual(summary["total_market_value"], 220)
        self.assertEqual(summary["total_cost_basis"], 210)
        self.assertEqual(summary["unrealized_pnl"], 10)
        self.assertAlmostEqual(sum(p["weight"] for p in summary["positions"]), 1)
        self.assertEqual(summary["allocation_by_tag"], {"科创": 120, "黄金": 100})

    def test_daily_snapshot_is_upserted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fund.db"
            base = {
                "total_market_value": 100, "total_cost_basis": 90,
                "unrealized_pnl": 10, "unrealized_return": 0.111111,
                "estimated_day_change": 1,
            }
            upsert_portfolio_snapshot(path, base, "2026-08-13T10:00:00+00:00")
            newer = dict(base, total_market_value=105)
            upsert_portfolio_snapshot(path, newer, "2026-08-13T14:00:00+00:00")
            rows = list_portfolio_snapshots(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["total_market_value"], 105)


if __name__ == "__main__":
    unittest.main()
