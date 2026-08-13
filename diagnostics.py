# -*- coding: utf-8 -*-
"""共享诊断逻辑模块 -- 供 ai_agent.py / web_app.py / upupup.py 复用"""

import datetime
import json
import os
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_market_status(config, now=None):
    """返回 (状态文本, 图标, 是否休市)，供 Web/API/控制台统一使用。"""
    now = now or datetime.datetime.now()
    cur_t, today_str = now.time(), now.strftime("%Y-%m-%d")
    if now.weekday() >= 5 or today_str in config.get("global_holidays", []):
        return "节假休市", "💤", True
    if cur_t < datetime.time(9, 30):
        return "夜间休市", "💤", True
    if cur_t < datetime.time(11, 30):
        return "正在开盘", "💰", False
    if cur_t < datetime.time(13, 0):
        return "午间休市", "☕", True
    if cur_t < datetime.time(14, 50):
        return "正在开盘", "💰", False
    if cur_t < datetime.time(15, 0):
        return "决战收盘", "🔥", False
    return "夜间休市", "💤", True


def fetch_market_prices_from_stock_sdk(code_list):
    """从内部 stock-sdk 服务获取行情；SDK 百分数转换为小数比例。"""
    service_url = os.getenv("STOCK_SDK_URL", "").rstrip("/")
    if not service_url or not code_list:
        return {}
    response = requests.post(
        f"{service_url}/quotes/cn",
        json={"symbols": code_list},
        timeout=10,
    )
    response.raise_for_status()
    result = {}
    for quote in response.json().get("quotes", []):
        code = str(quote.get("symbol") or quote.get("code") or "")
        matched = next((item for item in code_list if item.endswith(code[-6:])), None)
        if matched and quote.get("changePercent") is not None:
            result[matched] = float(quote["changePercent"]) / 100.0
    return result


def fetch_market_prices_from_eltdx(code_list):
    """从通达信协议获取 A股/ETF 行情；不可用时由调用方降级。"""
    if os.getenv("ELTDX_ENABLED", "1").lower() not in {"1", "true", "yes"}:
        return {}
    if not code_list:
        return {}
    from eltdx import TdxClient

    supported = [
        code for code in code_list if str(code).lower().startswith(("sh", "sz", "bj"))
    ]
    if not supported:
        return {}
    with TdxClient(timeout=3, heartbeat_interval=None) as client:
        quotes = client.get_quote(supported)
    return {
        quote.full_code: float(quote.change_pct) / 100.0
        for quote in quotes
        if quote.change_pct is not None
    }


def fetch_market_prices(code_list):
    """按 eltdx → stock-sdk → 腾讯直连逐层补齐行情。"""
    if not code_list:
        return {}
    prices = {}
    try:
        prices.update(fetch_market_prices_from_eltdx(code_list))
    except Exception:
        pass
    missing_codes = [code for code in code_list if code not in prices]
    try:
        prices.update(fetch_market_prices_from_stock_sdk(missing_codes))
    except (requests.RequestException, ValueError, TypeError):
        pass
    missing_codes = [code for code in code_list if code not in prices]
    if not missing_codes:
        return prices
    try:
        res = requests.get(
            f"http://qt.gtimg.cn/q={','.join(missing_codes)}", timeout=5
        ).text
        for line in res.split("\n"):
            if '="' in line:
                key = next(
                    (
                        c
                        for c in missing_codes
                        if c.endswith(line.split("=")[0].split("_")[-1])
                    ),
                    "",
                )
                d = line.split('"')[1].split("~")
                if key and len(d) > 4 and float(d[4]) > 0:
                    prices[key] = (float(d[3]) - float(d[4])) / float(d[4])
        return prices
    except (requests.RequestException, ValueError, IndexError, json.JSONDecodeError):
        pass
    return prices


def fetch_realtime_fund_flow(proxy_code):
    """获取当天的资金净流入 (单位: 万元)"""
    if not proxy_code:
        return None
    try:
        market = "1" if proxy_code.startswith("sh") else "0"
        code = proxy_code[2:]
        secid = f"{market}.{code}"
        url = (
            f"http://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
            f"?lmt=1&klt=101&secid={secid}&fields1=f1,f2,f3,f7"
            f"&fields2=f51,f52,f53,f54,f55,f56"
        )
        res = requests.get(url, timeout=4, verify=False).json()
        if res.get("rc") == 0 and res.get("data") and res["data"].get("klines"):
            parts = res["data"]["klines"][0].split(",")
            if len(parts) >= 6:
                return float(parts[1]) / 10000.0
    except:
        pass
    return None


def fetch_nav_batch(session, fund_codes):
    """批量抓取基金最新净值，返回 (nav_cache, nav_date_cache)"""
    nav_cache, nav_date_cache = {}, {}
    for code in fund_codes:
        try:
            url = (
                f"http://api.fund.eastmoney.com/f10/lsjz"
                f"?fundCode={code}&pageIndex=1&pageSize=1"
            )
            res = session.get(url, timeout=3, verify=False).json()
            if res.get("Data") and res["Data"].get("LSJZList"):
                nav_cache[code] = float(res["Data"]["LSJZList"][0]["DWJZ"])
                nav_date_cache[code] = res["Data"]["LSJZList"][0]["FSRQ"]
        except:
            pass
    return nav_cache, nav_date_cache


def compute_fund_diagnostic(
    code,
    config,
    nav_cache,
    nav_date_cache,
    stock_prices,
    holdings_cache,
    trend_matrix,
    today_str,
    is_market_closed,
    after_close_today=False,
):
    """返回单只基金的完整诊断信息
    after_close_today: 今天是交易日且当前时间已过15:00收盘（但净值尚未公布），
                       此时仍需叠加当日涨跌幅估算，避免收益率回退到前一日。
    """
    cfg = config[code]
    strat = {
        "tag": cfg.get("tag", "默认"),
        "drop": cfg.get("drop", -0.015),
        "gap": cfg.get("gap", 0.02),
        "cap": cfg.get("cap", 200),
        "tp": cfg.get("tp", 0.10),
        "ratio": cfg.get("ratio", 0.50),
        "tp_ma": cfg.get("tp_ma", 20),
    }

    # --- v_pure: 归一动能 (持仓穿透) ---
    h = holdings_cache.get(code, [])
    e_contrib, sw_sum = 0.0, 0.0
    for sc, wt in h:
        if sc in stock_prices:
            e_contrib += stock_prices[sc] * wt
            sw_sum += wt
    fb = cfg.get("proxy")
    if fb and fb in stock_prices:
        v_pure = e_contrib + stock_prices[fb] * max(0.0, 1.0 - sw_sum)
    elif sw_sum > 0:
        v_pure = e_contrib
    else:
        v_pure = None

    # --- v_off: 官方估算 ---
    try:
        r = requests.get(
            f"http://fundgz.1234567.com.cn/js/{code}.js", timeout=2
        ).text
        v_off = (
            float(json.loads(r[r.find("{") : r.rfind("}") + 1])["gszzl"]) / 100
        )
    except:
        v_off = None

    # --- v_hyb: 严谨终值 ---
    v_hyb = v_pure if v_pure is not None else (v_off if v_off is not None else 0.0)

    # --- est_nav & h_yield ---
    base = nav_cache.get(code, cfg["cost"])
    is_today_updated = nav_date_cache.get(code) == today_str

    # 估算逻辑：
    # - 净值已公布：直接用实际值
    # - 盘中 / 收盘后净值未出：base * (1 + v_hyb) 叠加当日涨跌
    # - 非交易日 / 开盘前：base 已是最近收盘净值，不叠加（避免重复计算）
    if is_today_updated:
        est_nav = base
    elif not is_market_closed or after_close_today:
        est_nav = base * (1 + v_hyb)
    else:
        est_nav = base

    h_yield = (est_nav - cfg["cost"]) / cfg["cost"]

    # --- diag: 诊断建议 ---
    ma = trend_matrix.get(code, {"MA5": 0, "MA10": 0, "MA20": 0, "MA60": 0})
    last_p = cfg.get("last_replenish_price", cfg.get("cost", est_nav))
    target_ma_val = ma.get(f"MA{strat['tp_ma']}", 0)

    is_silenced = False
    days_passed = 0
    if "last_sell_date" in cfg:
        try:
            last_sell_date = datetime.datetime.strptime(
                cfg["last_sell_date"], "%Y-%m-%d"
            ).date()
            days_passed = (datetime.date.today() - last_sell_date).days
            if days_passed < 10 and h_yield >= (strat["tp"] - 0.05):
                is_silenced = True
        except:
            pass

    diag = "[巡航中]"
    if is_market_closed:
        diag = "[休市]"
    else:
        if h_yield >= strat["tp"]:
            if is_silenced:
                diag = f"[止盈静默, 剩余{10 - days_passed}天]"
            else:
                if target_ma_val > 0 and est_nav > target_ma_val:
                    diag = f"极强(MA{strat['tp_ma']}护航)"
                elif target_ma_val > 0 and est_nav <= target_ma_val:
                    if strat["tag"] == "救援":
                        diag = "救援结束! 建议清仓"
                    else:
                        diag = f"破MA{strat['tp_ma']}! 建议卖{strat['ratio']*100:.0f}%"
                else:
                    diag = "达标! 等待均线确认"

        elif v_hyb <= strat["drop"]:
            p_gap = (est_nav - last_p) / last_p
            if p_gap > -strat["gap"]:
                diag = f"空间锁拦截({p_gap:+.1%})"
            elif (
                strat["tag"] == "周期"
                and ma.get("MA60", 0) > 0
                and est_nav < ma["MA60"]
            ):
                diag = "破位! 观望为上"
            elif strat["tag"] == "限购":
                diag = "限购暂停"
            else:
                val = cfg.get("shares", 0) * est_nav
                bonus = 1.0 + (abs(min(0, h_yield)) // 0.05) * 0.3
                buy_amt = (
                    int(val * abs(v_hyb) * cfg.get("multiplier", 1.5) * bonus)
                    if val > 0
                    else 500
                )
                diag = f"狙击补仓 +{min(max(50, buy_amt), strat['cap'])}元"
        elif h_yield < -0.12:
            diag = "深度被套(等信号)"

    # --- main_flow: 实时主力资金 ---
    main_flow = fetch_realtime_fund_flow(fb) if fb else None

    return {
        "code": code,
        "name": cfg["name"],
        "cost": cfg["cost"],
        "shares": cfg.get("shares", 0),
        "est_nav": est_nav,
        "v_pure": v_pure,
        "v_off": v_off,
        "v_hyb": v_hyb,
        "h_yield": h_yield,
        "tp": strat["tp"],
        "diag": diag,
        "main_flow": main_flow,
        "proxy": fb,
        "ma": ma,
    }


def calculate_portfolio_diagnostics(config, holdings_cache, trend_matrix, now=None):
    """抓取一次行情并用唯一诊断内核计算整个组合。"""
    now = now or datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    fund_codes = [
        key for key in config if key.isdigit() or str(key).startswith("QD")
    ]

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "Mozilla/5.0", "Referer": "http://fundf10.eastmoney.com/"}
    )
    nav_cache, nav_date_cache = fetch_nav_batch(session, fund_codes)

    market_codes = set()
    for code in fund_codes:
        proxy = config[code].get("proxy")
        if proxy:
            market_codes.add(proxy)
        market_codes.update(stock_code for stock_code, _ in holdings_cache.get(code, []))
    stock_prices = fetch_market_prices(sorted(market_codes))

    status_text, icon, is_market_closed = get_market_status(config, now)
    is_trading_day = (
        now.weekday() < 5
        and today_str not in config.get("global_holidays", [])
    )
    after_close_today = is_trading_day and now.time() >= datetime.time(15, 0)
    rows = [
        compute_fund_diagnostic(
            code,
            config,
            nav_cache,
            nav_date_cache,
            stock_prices,
            holdings_cache,
            trend_matrix,
            today_str,
            is_market_closed,
            after_close_today,
        )
        for code in fund_codes
    ]
    return rows, {
        "status": status_text,
        "icon": icon,
        "is_market_closed": is_market_closed,
        "as_of": now.isoformat(timespec="seconds"),
        "label": f"时间: {now.strftime('%H:%M:%S')} | {icon} {status_text}",
    }
