"""SQLite 交易账本。交易记录只追加，不提供修改或删除接口。"""

from __future__ import annotations

import csv
import io
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    executed_at TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    fund_name TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    nav REAL NOT NULL CHECK (nav > 0),
    shares REAL NOT NULL CHECK (shares > 0),
    gross_amount REAL NOT NULL CHECK (gross_amount >= 0),
    fee REAL NOT NULL DEFAULT 0 CHECK (fee >= 0),
    source TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    position_shares_after REAL NOT NULL CHECK (position_shares_after >= 0),
    cost_after REAL NOT NULL CHECK (cost_after >= 0),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_executed_at ON trades(executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_fund_code ON trades(fund_code, executed_at DESC);
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_date TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    total_market_value REAL NOT NULL,
    total_cost_basis REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    unrealized_return REAL NOT NULL,
    estimated_day_change REAL NOT NULL
);
"""


def connect_database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SCHEMA)
    return connection


def record_trade(
    path,
    *,
    fund_code,
    fund_name,
    side,
    nav,
    shares,
    gross_amount,
    fee=0,
    source="web",
    note="",
    position_shares_after,
    cost_after,
    executed_at=None,
):
    trade_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    executed_at = executed_at or now
    values = (
        trade_id, executed_at, str(fund_code), str(fund_name), side,
        float(nav), float(shares), float(gross_amount), float(fee), str(source),
        str(note), float(position_shares_after), float(cost_after), now,
    )
    with connect_database(path) as connection:
        connection.execute(
            """INSERT INTO trades (
                id, executed_at, fund_code, fund_name, side, nav, shares,
                gross_amount, fee, source, note, position_shares_after,
                cost_after, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
    return trade_id


def list_trades(path, limit=200, fund_code=None):
    limit = max(1, min(int(limit), 2000))
    with connect_database(path) as connection:
        if fund_code:
            rows = connection.execute(
                "SELECT * FROM trades WHERE fund_code = ? ORDER BY executed_at DESC LIMIT ?",
                (str(fund_code), limit),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM trades ORDER BY executed_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(row) for row in rows]


def trades_csv(path):
    rows = list_trades(path, limit=2000)
    output = io.StringIO()
    columns = [
        "id", "executed_at", "fund_code", "fund_name", "side", "nav",
        "shares", "gross_amount", "fee", "source", "note",
        "position_shares_after", "cost_after", "created_at",
    ]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def upsert_portfolio_snapshot(path, summary, captured_at=None):
    captured_at = captured_at or datetime.now().astimezone().isoformat(timespec="seconds")
    snapshot_date = captured_at[:10]
    values = (
        snapshot_date,
        captured_at,
        summary["total_market_value"],
        summary["total_cost_basis"],
        summary["unrealized_pnl"],
        summary["unrealized_return"],
        summary["estimated_day_change"],
    )
    with connect_database(path) as connection:
        connection.execute(
            """INSERT INTO portfolio_snapshots (
                snapshot_date, captured_at, total_market_value, total_cost_basis,
                unrealized_pnl, unrealized_return, estimated_day_change
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date) DO UPDATE SET
                captured_at=excluded.captured_at,
                total_market_value=excluded.total_market_value,
                total_cost_basis=excluded.total_cost_basis,
                unrealized_pnl=excluded.unrealized_pnl,
                unrealized_return=excluded.unrealized_return,
                estimated_day_change=excluded.estimated_day_change""",
            values,
        )


def list_portfolio_snapshots(path, limit=365):
    limit = max(1, min(int(limit), 5000))
    with connect_database(path) as connection:
        rows = connection.execute(
            """SELECT * FROM (
                SELECT * FROM portfolio_snapshots
                ORDER BY snapshot_date DESC LIMIT ?
            ) ORDER BY snapshot_date ASC""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
