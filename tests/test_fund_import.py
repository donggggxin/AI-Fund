import unittest
from io import BytesIO

import pandas as pd

from fund_import import apply_import, normalize_fund_table, read_fund_table


VALID_ROWS = [{
    "基金代码": "017470",
    "基金名称": "科技基金",
    "标签": "科创",
    "持仓成本": 1.2345,
    "持有份额": 1000,
    "ETF代理": "sh588000",
}]


class FundImportTests(unittest.TestCase):
    def test_csv_chinese_headers(self):
        content = pd.DataFrame(VALID_ROWS).to_csv(index=False).encode("utf-8-sig")
        frame = read_fund_table(content, "funds.csv")
        rows, errors = normalize_fund_table(frame)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["code"], "017470")
        self.assertEqual(rows[0]["proxy"], "sh588000")
        self.assertEqual(rows[0]["daily_invest"], 20)

    def test_xlsx_import(self):
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame(VALID_ROWS).to_excel(writer, index=False)
        frame = read_fund_table(buffer.getvalue(), "funds.xlsx")
        rows, errors = normalize_fund_table(frame)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["shares"], 1000)

    def test_invalid_row_blocks_import(self):
        frame = pd.DataFrame([{
            "code": "abc", "name": "错误基金", "cost": -1, "shares": 1,
        }])
        rows, errors = normalize_fund_table(frame)
        self.assertEqual(rows, [])
        self.assertTrue(errors)

    def test_apply_import_preserves_system_settings(self):
        config = {
            "ai_agent_settings": {"api_key": "secret"},
            "global_holidays": ["2026-01-01"],
            "017470": {"name": "旧名称", "custom": "keep"},
        }
        rows, errors = normalize_fund_table(pd.DataFrame(VALID_ROWS))
        self.assertEqual(errors, [])
        updated, summary = apply_import(config, rows, "overwrite")
        self.assertEqual(updated["ai_agent_settings"]["api_key"], "secret")
        self.assertEqual(updated["global_holidays"], ["2026-01-01"])
        self.assertEqual(updated["017470"]["custom"], "keep")
        self.assertEqual(summary["overwritten"], ["017470"])


if __name__ == "__main__":
    unittest.main()
