import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.main import BuyTrade, SellTrade, register_buy, register_sell
from database import list_trades
from storage import save_json


class BackendTradeTests(unittest.TestCase):
    def test_buy_and_sell_write_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "fund_config.json"
            database_path = Path(directory) / "fund.db"
            save_json(config_path, {
                "017470": {"name": "科技基金", "cost": 1.0, "shares": 100},
            })
            with (
                patch("backend.main.CONFIG_PATH", config_path),
                patch("backend.main.DATABASE_PATH", database_path),
            ):
                buy = register_buy(BuyTrade(
                    fund_code="017470", nav=1.0, amount=100, fee=1,
                ))
                self.assertEqual(buy["fund"]["shares"], 200)
                self.assertEqual(buy["fund"]["cost"], 1.005)
                sell = register_sell(SellTrade(
                    fund_code="017470", nav=1.2, ratio=0.25, fee=0.5,
                ))
                self.assertEqual(sell["fund"]["shares"], 150)

            rows = list_trades(database_path)
            self.assertEqual({row["side"] for row in rows}, {"BUY", "SELL"})
            self.assertTrue(all(row["id"] for row in rows))


if __name__ == "__main__":
    unittest.main()
