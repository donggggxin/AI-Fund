import asyncio
import datetime
import os
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from diagnostics import calculate_portfolio_diagnostics
from database import (
    list_portfolio_snapshots,
    list_trades,
    record_trade,
    upsert_portfolio_snapshot,
)
from portfolio import summarize_portfolio
from storage import initialize_data_files, load_json, save_json

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("FUND_DATA_DIR", ROOT_DIR))
CONFIG_PATH = DATA_DIR / "fund_config.json"
HOLDINGS_PATH = DATA_DIR / "holdings_cache.json"
TREND_PATH = DATA_DIR / "trend_matrix.json"
REPORT_PATH = DATA_DIR / "agent_report.md"
DATABASE_PATH = DATA_DIR / "fund_dashboard.db"
WRITE_LOCK = threading.Lock()
initialize_data_files(DATA_DIR, ROOT_DIR)

app = FastAPI(title="AI Fund Dashboard API", version="0.1.0")


def require_api_key(x_api_key: str | None = Header(default=None)):
    expected = os.getenv("FUND_API_KEY", "")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


def load_config():
    return load_json(CONFIG_PATH)


class BuyTrade(BaseModel):
    fund_code: str = Field(pattern=r"^(\d{6}|QD.+)$")
    nav: float = Field(gt=0)
    amount: float = Field(gt=0)
    fee: float = Field(default=0, ge=0)
    note: str = Field(default="", max_length=500)


class SellTrade(BaseModel):
    fund_code: str = Field(pattern=r"^(\d{6}|QD.+)$")
    ratio: float = Field(gt=0, le=1)
    nav: float = Field(gt=0)
    fee: float = Field(default=0, ge=0)
    note: str = Field(default="", max_length=500)


@app.get("/api/health")
def health():
    return {"status": "ok", "config_exists": CONFIG_PATH.exists()}


@app.get("/api/funds", dependencies=[Depends(require_api_key)])
def funds():
    config = load_config()
    return {
        code: value
        for code, value in config.items()
        if code.isdigit() or str(code).startswith("QD")
    }


@app.get("/api/diagnostics", dependencies=[Depends(require_api_key)])
async def diagnostics():
    config = load_config()
    rows, market = await asyncio.to_thread(
        calculate_portfolio_diagnostics,
        config,
        load_json(HOLDINGS_PATH),
        load_json(TREND_PATH),
    )
    summary = summarize_portfolio(rows, config)
    if rows:
        upsert_portfolio_snapshot(DATABASE_PATH, summary)
    return {"market": market, "funds": rows, "portfolio": summary}


@app.get("/api/portfolio/history", dependencies=[Depends(require_api_key)])
def portfolio_history(limit: int = 365):
    return {"snapshots": list_portfolio_snapshots(DATABASE_PATH, limit=limit)}


@app.get("/api/reports/latest", dependencies=[Depends(require_api_key)])
def latest_report():
    if not REPORT_PATH.exists():
        raise HTTPException(status_code=404, detail="Report not generated")
    return {"content": REPORT_PATH.read_text(encoding="utf-8")}


@app.get("/api/trades", dependencies=[Depends(require_api_key)])
def trade_history(limit: int = 200, fund_code: str | None = None):
    return {"trades": list_trades(DATABASE_PATH, limit=limit, fund_code=fund_code)}


@app.post("/api/trades/buy", dependencies=[Depends(require_api_key)])
def register_buy(trade: BuyTrade):
    with WRITE_LOCK:
        config = load_config()
        cfg = config.get(trade.fund_code)
        if not isinstance(cfg, dict):
            raise HTTPException(status_code=404, detail="Fund not found")
        old_shares = float(cfg.get("shares", 0))
        old_cost = float(cfg.get("cost", 0))
        added_shares = trade.amount / trade.nav
        total_shares = old_shares + added_shares
        cfg["shares"] = round(total_shares, 2)
        cfg["cost"] = round(
            (old_cost * old_shares + trade.amount + trade.fee) / total_shares, 4
        )
        cfg["last_replenish_price"] = round(trade.nav, 4)
        cfg["last_replenish_amount"] = trade.amount
        cfg["last_replenish_date"] = datetime.date.today().isoformat()
        save_json(CONFIG_PATH, config)
        trade_id = record_trade(
            DATABASE_PATH,
            fund_code=trade.fund_code,
            fund_name=cfg.get("name", trade.fund_code),
            side="BUY",
            nav=trade.nav,
            shares=added_shares,
            gross_amount=trade.amount,
            fee=trade.fee,
            note=trade.note,
            source="api",
            position_shares_after=cfg["shares"],
            cost_after=cfg["cost"],
        )
        return {"fund_code": trade.fund_code, "fund": cfg, "trade_id": trade_id}


@app.post("/api/trades/sell", dependencies=[Depends(require_api_key)])
def register_sell(trade: SellTrade):
    with WRITE_LOCK:
        config = load_config()
        cfg = config.get(trade.fund_code)
        if not isinstance(cfg, dict):
            raise HTTPException(status_code=404, detail="Fund not found")
        old_shares = float(cfg.get("shares", 0))
        if old_shares <= 0:
            raise HTTPException(status_code=409, detail="No shares available to sell")
        sold_shares = old_shares * trade.ratio
        cfg["shares"] = round(old_shares - sold_shares, 2)
        cfg["last_sell_date"] = datetime.date.today().isoformat()
        save_json(CONFIG_PATH, config)
        trade_id = record_trade(
            DATABASE_PATH,
            fund_code=trade.fund_code,
            fund_name=cfg.get("name", trade.fund_code),
            side="SELL",
            nav=trade.nav,
            shares=sold_shares,
            gross_amount=sold_shares * trade.nav,
            fee=trade.fee,
            note=trade.note,
            source="api",
            position_shares_after=cfg["shares"],
            cost_after=float(cfg.get("cost", 0)),
        )
        return {"fund_code": trade.fund_code, "fund": cfg, "trade_id": trade_id}
