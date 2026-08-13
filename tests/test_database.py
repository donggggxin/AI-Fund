import csv
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from database import list_trades, record_trade, trades_csv


class TradeLedgerTests(unittest.TestCase):
    def test_record_filter_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fund.db"
            trade_id = record_trade(
                path, fund_code="017470", fund_name="科技基金", side="BUY",
                nav=1.25, shares=80, gross_amount=100, fee=0.1, source="test",
                position_shares_after=1080, cost_after=1.2,
            )
            record_trade(
                path, fund_code="000001", fund_name="另一基金", side="SELL",
                nav=2, shares=10, gross_amount=20, source="test",
                position_shares_after=90, cost_after=1.8,
            )
            rows = list_trades(path, fund_code="017470")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], trade_id)
            self.assertEqual(rows[0]["fee"], 0.1)
            exported_rows = list(csv.DictReader(StringIO(trades_csv(path).decode("utf-8-sig"))))
            self.assertEqual(len(exported_rows), 2)
            self.assertEqual(exported_rows[0]["source"], "test")

    def test_invalid_trade_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Exception):
                record_trade(
                    Path(directory) / "fund.db", fund_code="017470",
                    fund_name="科技基金", side="BUY", nav=0, shares=1,
                    gross_amount=1, source="test", position_shares_after=1,
                    cost_after=1,
                )


if __name__ == "__main__":
    unittest.main()
