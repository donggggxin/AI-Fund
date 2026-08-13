"""基金 CSV/XLSX 批量导入、规范化与校验。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd

ALLOWED_TAGS = {"科创", "海外", "红利", "黄金", "周期", "救援", "限购"}
COLUMN_ALIASES = {
    "code": "code", "基金代码": "code",
    "name": "name", "基金名称": "name",
    "tag": "tag", "标签": "tag", "基金标签": "tag",
    "cost": "cost", "持仓成本": "cost", "成本": "cost",
    "shares": "shares", "持有份额": "shares", "份额": "shares",
    "proxy": "proxy", "ETF代理": "proxy", "代理代码": "proxy",
    "daily_invest": "daily_invest", "每日定投": "daily_invest",
    "multiplier": "multiplier", "补仓倍数": "multiplier",
    "drop": "drop", "暴跌触发线": "drop",
    "gap": "gap", "空间锁": "gap",
    "cap": "cap", "补仓上限": "cap",
    "tp": "tp", "止盈目标": "tp",
    "ratio": "ratio", "卖出比例": "ratio",
    "tp_ma": "tp_ma", "护航均线": "tp_ma",
}
REQUIRED_COLUMNS = {"code", "name", "cost", "shares"}
NUMERIC_COLUMNS = {
    "cost", "shares", "daily_invest", "multiplier", "drop", "gap",
    "cap", "tp", "ratio", "tp_ma",
}


def read_fund_table(content: bytes, filename: str) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        last_error = None
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return pd.read_csv(BytesIO(content), dtype={"code": str}, encoding=encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
        raise ValueError("CSV 编码无法识别，请使用 UTF-8 或 GB18030") from last_error
    if suffix == ".xlsx":
        return pd.read_excel(BytesIO(content), sheet_name=0, dtype={"code": str})
    raise ValueError("仅支持 .csv 和 .xlsx 文件")


def _default_drop(tag: str) -> float:
    if tag == "科创":
        return -0.025
    if tag == "红利":
        return -0.008
    return -0.01


def _as_number(value, field, row_number, errors, default=None):
    if pd.isna(value) or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"第 {row_number} 行：{field} 必须是数字")
        return default


def normalize_fund_table(frame: pd.DataFrame):
    frame = frame.copy()
    frame.columns = [COLUMN_ALIASES.get(str(c).strip(), str(c).strip()) for c in frame.columns]
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        return [], [f"缺少必填列：{', '.join(missing)}"]

    rows, errors, seen = [], [], set()
    for index, raw in frame.iterrows():
        row_number = index + 2
        if raw.isna().all():
            continue
        raw_code = raw.get("code", "")
        if isinstance(raw_code, float) and raw_code.is_integer():
            raw_code = int(raw_code)
        code = str(raw_code).strip().replace(".0", "")
        if code.isdigit():
            code = code.zfill(6)
        if not (len(code) == 6 and code.isdigit()) and not code.startswith("QD"):
            errors.append(f"第 {row_number} 行：基金代码 {code!r} 不合法")
            continue
        if code in seen:
            errors.append(f"第 {row_number} 行：基金代码 {code} 在文件内重复")
            continue
        seen.add(code)

        name = "" if pd.isna(raw.get("name")) else str(raw.get("name")).strip()
        if not name:
            errors.append(f"第 {row_number} 行：基金名称不能为空")
            continue
        tag = "科创" if pd.isna(raw.get("tag")) else str(raw.get("tag")).strip()
        if tag not in ALLOWED_TAGS:
            errors.append(f"第 {row_number} 行：标签 {tag!r} 不在允许范围")
            continue

        numeric = {
            field: _as_number(raw.get(field), field, row_number, errors)
            for field in NUMERIC_COLUMNS if field in frame.columns
        }
        cost = numeric.get("cost")
        shares = numeric.get("shares")
        if cost is None or cost <= 0:
            errors.append(f"第 {row_number} 行：cost 必须大于 0")
            continue
        if shares is None or shares < 0:
            errors.append(f"第 {row_number} 行：shares 不能小于 0")
            continue

        daily = int(numeric.get("daily_invest") if numeric.get("daily_invest") is not None else 20)
        multiplier = numeric.get("multiplier") if numeric.get("multiplier") is not None else 1.5
        tp_ma = int(numeric.get("tp_ma") if numeric.get("tp_ma") is not None else 5)
        if daily < 0 or multiplier <= 0 or tp_ma not in {5, 10, 20}:
            errors.append(f"第 {row_number} 行：定投/倍数/护航均线参数不合法")
            continue
        proxy = "" if pd.isna(raw.get("proxy")) else str(raw.get("proxy")).strip()
        rows.append({
            "code": code,
            "name": name,
            "tag": tag,
            "cost": round(cost, 4),
            "shares": round(shares, 2),
            "proxy": proxy,
            "daily_invest": daily,
            "multiplier": round(multiplier, 2),
            "drop": numeric.get("drop") if numeric.get("drop") is not None else _default_drop(tag),
            "gap": numeric.get("gap") if numeric.get("gap") is not None else 0.03,
            "cap": int(numeric.get("cap") if numeric.get("cap") is not None else 500),
            "tp": numeric.get("tp") if numeric.get("tp") is not None else 0.15,
            "ratio": numeric.get("ratio") if numeric.get("ratio") is not None else 0.33,
            "tp_ma": tp_ma,
        })
    return rows, errors


def build_fund_config(row: dict, existing: dict | None = None) -> dict:
    cfg = dict(existing or {})
    cfg.update({key: value for key, value in row.items() if key != "code"})
    cfg.setdefault("last_replenish_price", row["cost"])
    cfg.setdefault("last_replenish_amount", 0.0)
    cfg.setdefault("last_replenish_date", "")
    cfg["base_daily_invest"] = row["daily_invest"]
    cfg["base_multiplier"] = row["multiplier"]
    return cfg


def apply_import(config: dict, rows: list[dict], duplicate_policy: str):
    updated = dict(config)
    added, overwritten, skipped = [], [], []
    for row in rows:
        code = row["code"]
        exists = isinstance(updated.get(code), dict)
        if exists and duplicate_policy == "skip":
            skipped.append(code)
            continue
        updated[code] = build_fund_config(row, updated.get(code) if exists else None)
        (overwritten if exists else added).append(code)
    return updated, {"added": added, "overwritten": overwritten, "skipped": skipped}


def csv_template() -> bytes:
    columns = [
        "code", "name", "tag", "cost", "shares", "proxy",
        "daily_invest", "multiplier", "drop", "gap", "cap", "tp",
        "ratio", "tp_ma",
    ]
    example = [[
        "017470", "示例科技基金", "科创", 1.2345, 1000, "sh588000",
        20, 1.5, -0.025, 0.03, 500, 0.15, 0.33, 5,
    ]]
    return pd.DataFrame(example, columns=columns).to_csv(index=False).encode("utf-8-sig")
