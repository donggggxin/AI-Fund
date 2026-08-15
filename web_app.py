# -*- coding: utf-8 -*-
import streamlit as st
import json
import os
import datetime
import requests
import urllib3
import traceback
import sys
import time
import re

from diagnostics import (
    calculate_portfolio_diagnostics as calculate_shared_diagnostics,
    get_market_status as get_shared_market_status,
)
from database import (
    list_portfolio_snapshots,
    list_trades,
    record_trade,
    trades_csv,
    upsert_portfolio_snapshot,
)
from daily_advice import build_daily_advice
from api_client import BackendUnavailable, get_diagnostics
from fund_import import apply_import, csv_template, normalize_fund_table, read_fund_table
from portfolio import summarize_portfolio
from llm_utils import build_chat_completions_url, build_gemini_url, format_api_error
from storage import (
    backup_file,
    initialize_data_files,
    load_json as load_json_file,
    save_json as save_json_file,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Ensure encoding is safe
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

# Page Config
st.set_page_config(
    page_title="AI科技主线基金智能体",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# File Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("FUND_DATA_DIR", BASE_DIR)
initialize_data_files(DATA_DIR, BASE_DIR)
CONFIG_PATH = os.path.join(DATA_DIR, "fund_config.json")
REPORT_PATH = os.path.join(DATA_DIR, "agent_report.md")
HOLDINGS_CACHE_PATH = os.path.join(DATA_DIR, "holdings_cache.json")
TREND_MATRIX_PATH = os.path.join(DATA_DIR, "trend_matrix.json")
DATABASE_PATH = os.path.join(DATA_DIR, "fund_dashboard.db")

# Clean White Theme CSS
st.markdown("""
<style>
    /* White Theme Body */
    .stApp {
        background-color: #ffffff;
        color: #1e293b;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    
    /* Header Styling */
    .main-title {
        color: #0f172a;
        font-size: 1.6rem;
        font-weight: 700;
        margin-top: -15px;
        margin-bottom: 0.5rem;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    
    /* Table Styling matching console look */
    .table-container {
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        overflow: hidden;
        margin-bottom: 1rem;
        box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.05);
    }
    .console-table {
        width: 100%;
        border-collapse: collapse;
        text-align: center;
        font-family: sans-serif;
    }
    .console-table th {
        background-color: #f8fafc;
        color: #64748b;
        font-weight: 600;
        font-size: 0.85rem;
        padding: 8px;
        border-bottom: 2px solid #cbd5e1;
    }
    .console-table td {
        padding: 10px 8px;
        border-bottom: 1px solid #f1f5f9;
        color: #334155;
        font-size: 0.9rem;
    }
    
    /* Chinese Stock Colors (Red Up, Green Down) */
    .text-up { color: #e11d48; font-weight: 600; }
    .text-down { color: #16a34a; font-weight: 600; }
    .text-neutral { color: #64748b; }
    .text-bold { font-weight: 700; }

    /* Premium card for Trade confirmations */
    .decision-card {
        background-color: #fdf2f2;
        border-left: 4px solid #ef4444;
        padding: 10px;
        border-radius: 6px;
        margin-bottom: 8px;
        box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.05);
    }
    .decision-card-buy {
        background-color: #f0fdf4;
        border-left: 4px solid #22c55e;
        padding: 10px;
        border-radius: 6px;
        margin-bottom: 8px;
        box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.05);
    }
    
    .card-title {
        font-weight: 700;
        color: #0f172a;
        margin-bottom: 4px;
        font-size: 0.9rem;
    }
    
    .card-body {
        font-size: 0.85rem;
        color: #334155;
        margin-bottom: 6px;
    }

    /* Minimalist Config Form */
    .config-box {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 1.5rem;
        box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.05);
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------
# 数据抓取与计算模块
# ----------------------------------------------------

def load_json(path):
    return load_json_file(path)

def save_json(path, data):
    save_json_file(path, data)

def get_market_status():
    """Determine market status string like upupup.py"""
    return get_shared_market_status(load_json(CONFIG_PATH))

def fetch_market_prices(code_list):
    if not code_list:
        return {}
    try:
        res = requests.get(f"http://qt.gtimg.cn/q={','.join(code_list)}", timeout=5).text
        prices = {}
        for line in res.split('\n'):
            if '="' in line:
                key = next((c for c in code_list if c.endswith(line.split('=')[0].split('_')[-1])), "")
                d = line.split('"')[1].split('~')
                if key and len(d) > 4 and float(d[4]) > 0:
                    prices[key] = (float(d[3]) - float(d[4])) / float(d[4])
        return prices
    except:
        return {}

def fetch_realtime_fund_flow(proxy_code):
    """实时获取当天的资金净流入 (单位: 万元)"""
    if not proxy_code:
        return None
    try:
        market = "1" if proxy_code.startswith("sh") else "0"
        code = proxy_code[2:]
        secid = f"{market}.{code}"
        # 获取最近 1 天的数据
        url = f"http://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=1&klt=101&secid={secid}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56"
        res = requests.get(url, timeout=4, verify=False).json()
        if res.get('rc') == 0 and res.get('data') and res['data'].get('klines'):
            parts = res['data']['klines'][0].split(',')
            if len(parts) >= 6:
                main_net = float(parts[1]) / 10000.0  # 万元
                return main_net
    except:
        pass
    return None

def calculate_portfolio_diagnostics():
    """Calculate and return same values as upupup.py's run_scan"""
    try:
        payload = get_diagnostics()
        return payload["funds"], payload["market"]["label"]
    except BackendUnavailable:
        # 本地双击启动或后端短暂不可用时，保持原有可用性。
        pass
    config = load_json(CONFIG_PATH)
    holdings_cache = load_json(HOLDINGS_CACHE_PATH)
    trend_matrix = load_json(TREND_MATRIX_PATH)
    rows, market = calculate_shared_diagnostics(
        config, holdings_cache, trend_matrix
    )
    return rows, market["label"]
    # Legacy implementation retained temporarily below for easy comparison.
    
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # 1. Fetch NAV cache
    nav_cache, nav_date_cache = {}, {}
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'http://fundf10.eastmoney.com/'})
    
    sorted_keys = [k for k in config.keys() if (k.isdigit() or k.startswith('QD'))]
    
    for code in sorted_keys:
        try:
            url = f"http://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=1"
            res = session.get(url, timeout=3, verify=False).json()
            if res.get('Data') and res['Data'].get('LSJZList'):
                nav_cache[code] = float(res['Data']['LSJZList'][0]['DWJZ'])
                nav_date_cache[code] = res['Data']['LSJZList'][0]['FSRQ']
        except:
            pass
            
    # 2. Collect stock prices
    all_codes = set()
    for code in sorted_keys:
        cfg = config[code]
        if cfg.get('proxy'): 
            all_codes.add(cfg['proxy'])
        for sc, _ in holdings_cache.get(code, []): 
            all_codes.add(sc)
            
    stock_prices = fetch_market_prices(list(all_codes))
    
    # 3. Compile output
    status_text, icon, is_m_closed = get_market_status()
    now = datetime.datetime.now()
    cur_t = now.time()
    is_trading_day_check = not (now.weekday() >= 5 or today_str in config.get('global_holidays', []))
    after_close_today = is_trading_day_check and cur_t >= datetime.time(15, 0)
    results = []
    
    for code in sorted_keys:
        cfg = config[code]
        strat = {
            'tag': cfg.get('tag', '默认'),
            'drop': cfg.get('drop', -0.015),
            'gap': cfg.get('gap', 0.02),
            'cap': cfg.get('cap', 200),
            'tp': cfg.get('tp', 0.10),
            'ratio': cfg.get('ratio', 0.50),
            'tp_ma': cfg.get('tp_ma', 20)
        }
        
        # Calculate v_pure
        h = holdings_cache.get(code, [])
        e_contrib, sw_sum = 0.0, 0.0
        for sc, wt in h:
            if sc in stock_prices:
                e_contrib += stock_prices[sc] * wt
                sw_sum += wt
        fb = cfg.get('proxy')
        v_pure = e_contrib + (stock_prices[fb] * max(0.0, 1.0 - sw_sum)) if fb and fb in stock_prices else (e_contrib if sw_sum > 0 else None)
        
        # Fetch v_off
        try:
            r = requests.get(f"http://fundgz.1234567.com.cn/js/{code}.js", timeout=2).text
            v_off = float(json.loads(r[r.find('{'):r.rfind('}')+1])['gszzl']) / 100
        except:
            v_off = None
            
        v_hyb = v_pure if v_pure is not None else (v_off if v_off is not None else 0.0)
        base = nav_cache.get(code, cfg['cost'])
        
        is_today_updated = (nav_date_cache.get(code) == today_str)
        # 净值已公布→实际值；盘中/收盘未出→叠加估算；非交易日/开盘前→不叠加(避免重复)
        if is_today_updated:
            est_nav = base
        elif not is_m_closed or after_close_today:
            est_nav = base * (1 + v_hyb)
        else:
            est_nav = base

        h_yield = (est_nav - cfg['cost']) / cfg['cost']
        
        # Diagnosis Advice Logic identical to upupup.py
        ma = trend_matrix.get(code, {"MA5":0, "MA10":0, "MA20":0, "MA60":0})
        last_p = cfg.get('last_replenish_price', cfg.get('cost', est_nav))
        target_ma_val = ma.get(f"MA{strat['tp_ma']}", 0)
        
        is_silenced = False
        days_passed = 0
        if 'last_sell_date' in cfg:
            try:
                last_sell_date = datetime.datetime.strptime(cfg['last_sell_date'], '%Y-%m-%d').date()
                days_passed = (datetime.date.today() - last_sell_date).days
                if days_passed < 10 and h_yield >= (strat['tp'] - 0.05):
                    is_silenced = True
            except:
                pass

        diag = "[巡航中]"
        if is_m_closed:
            diag = "[休市]"
        else:
            if h_yield >= strat['tp']:
                if is_silenced:
                    diag = f"[止盈静默, 剩余{10 - days_passed}天]"
                else:
                    if target_ma_val > 0 and est_nav > target_ma_val:
                        diag = f"极强(MA{strat['tp_ma']}护航)"
                    elif target_ma_val > 0 and est_nav <= target_ma_val:
                        if strat['tag'] == '救援':
                            diag = f"救援结束! 建议清仓"
                        else:
                            diag = f"破MA{strat['tp_ma']}! 建议卖{strat['ratio']*100:.0f}%"
                    else:
                        diag = f"达标! 等待均线确认"
            
            elif v_hyb <= strat['drop']:
                p_gap = (est_nav - last_p) / last_p
                if p_gap > -strat['gap']: 
                    diag = f"空间锁拦截({p_gap:+.1%})"
                elif strat['tag'] == '周期' and ma.get('MA60',0) > 0 and est_nav < ma['MA60']: 
                    diag = "破位! 观望为上"
                elif strat['tag'] == '限购': 
                    diag = "限购暂停"
                else:
                    val = cfg.get('shares', 0) * est_nav
                    bonus = 1.0 + (abs(min(0, h_yield)) // 0.05) * 0.3
                    buy_amt = int(val * abs(v_hyb) * cfg.get('multiplier', 1.5) * bonus) if val > 0 else 500
                    diag = f"狙击补仓 +{min(max(50, buy_amt), strat['cap'])}元"
            elif h_yield < -0.12: 
                diag = "深度被套(等信号)"
                
        # 实时主力资金流量数据
        main_flow = fetch_realtime_fund_flow(fb) if fb else None

        results.append({
            "code": code,
            "name": cfg['name'],
            "cost": cfg['cost'],
            "shares": cfg['shares'],
            "est_nav": est_nav,
            "v_pure": v_pure,
            "v_off": v_off,
            "v_hyb": v_hyb,
            "h_yield": h_yield,
            "tp": strat['tp'],
            "diag": diag,
            "main_flow": main_flow,
            "proxy": fb,
            "ma": ma
        })
        
    return results, f"时间: {datetime.datetime.now().strftime('%H:%M:%S')} | {icon} {status_text}"

# ----------------------------------------------------
# 页面构建
# ----------------------------------------------------

# Header
st.markdown('<div class="main-title">🤖 AI 科技主线基金投资智能体</div>', unsafe_allow_html=True)

# Dynamic refresh interval matching upupup.py exactly. The interval is now
# consumed by a Streamlit fragment, so it no longer reruns the whole page.
now = datetime.datetime.now()
cur_t = now.time()
is_weekend = now.weekday() >= 5
config_data = load_json(CONFIG_PATH)
holidays = config_data.get('global_holidays', [])
today_str = now.strftime('%Y-%m-%d')
is_holiday = is_weekend or (today_str in holidays)
is_save_time = datetime.time(14, 50) <= cur_t <= datetime.time(15, 0)

# 获取市场开盘状态，确保夜间休市/午间休市也彻底停用自动刷新
_status_text, _icon, is_m_closed_for_autorefresh = get_market_status()

# 若非交易时段（节假日/周末/夜间/午休），彻底停用自动刷新，避免无谓网络消耗与闪烁
if is_holiday or is_m_closed_for_autorefresh:
    dashboard_refresh_sec = None
else:
    dashboard_refresh_sec = 10 if is_save_time else 60

# Navigation Tabs
tab_monitor, tab_config, tab_ai_report = st.tabs(["📊 实时监控与智能对话", "⚙️ 系统参数与配置中心", "🤖 AI 智能诊断报告"])

# ====================================================
# TAB 1: 📊 实时监控与智能对话
# ====================================================
with tab_monitor:
    col_left, col_right = st.columns([0.65, 0.35], gap="large")

    @st.fragment(run_every=dashboard_refresh_sec)
    def render_dashboard():
        # All market-data work and widgets in this function rerun independently.
        # The chat column therefore remains mounted during automatic/manual refreshes.
        fund_rows, status_str = calculate_portfolio_diagnostics()
        _status_text, _icon, is_m_closed_for_autorefresh = get_market_status()

        current_config = load_json(CONFIG_PATH)
        portfolio_summary = summarize_portfolio(fund_rows, current_config)
        if fund_rows:
            upsert_portfolio_snapshot(DATABASE_PATH, portfolio_summary)

        # Time and status is now integrated directly in the main header of the left column
        st.markdown(f"##### 📈 实时跟踪看板 <span style='font-size:0.85rem; font-weight:normal; color:#64748b; margin-left:12px;'>（{status_str}）</span>", unsafe_allow_html=True)

        metric_value, metric_pnl, metric_return, metric_day = st.columns(4)
        metric_value.metric(
            "组合市值", f"¥{portfolio_summary['total_market_value']:,.2f}"
        )
        metric_pnl.metric(
            "未实现盈亏", f"¥{portfolio_summary['unrealized_pnl']:,.2f}"
        )
        metric_return.metric(
            "持仓收益率", f"{portfolio_summary['unrealized_return']:.2%}"
        )
        metric_day.metric(
            "今日估算变动", f"¥{portfolio_summary['estimated_day_change']:,.2f}"
        )

        market_status, market_icon, market_closed = get_shared_market_status(current_config)
        daily_guide = build_daily_advice(
            fund_rows,
            current_config,
            {
                "status": market_status,
                "icon": market_icon,
                "is_market_closed": market_closed,
                "as_of": datetime.datetime.now().isoformat(timespec="seconds"),
            },
        )
        st.markdown("##### 🧭 今日投资行动指南（新手版）")
        if market_closed:
            st.info(daily_guide["headline"])
        elif daily_guide["action_counts"].get("减仓"):
            st.warning(daily_guide["headline"])
        else:
            st.success(daily_guide["headline"])
        st.caption(
            f"市场：{daily_guide['market_status']} ｜ 阶段：{daily_guide['market_stage']} ｜ "
            f"数据时点：{daily_guide['as_of'].replace('T', ' ')}"
        )
        st.write(daily_guide["today_plan"])
        with st.expander("查看每只基金的建议、原因与原理", expanded=True):
            for advice in daily_guide["items"]:
                st.markdown(
                    f"**{advice['action']}｜{advice['name']}（{advice['code']}）**  "
                    f"\n{advice['action_detail']}"
                )
                st.markdown("原因：" + "；".join(advice["reasons"]))
                st.markdown("原理：" + advice["principle"])
                st.caption("操作前确认：" + "；".join(advice["checklist"]))
                st.divider()
        with st.expander("新手必须先知道的 3 条规则"):
            for rule in daily_guide["beginner_rules"]:
                st.markdown(f"- {rule}")
            st.caption(daily_guide["disclaimer"])

        if portfolio_summary["allocation_by_tag"]:
            with st.expander("📊 组合配置与收益趋势"):
                allocation_rows = [
                    {"标签": tag, "市值": value}
                    for tag, value in portfolio_summary["allocation_by_tag"].items()
                ]
                st.markdown("**标签资产配置**")
                st.bar_chart(allocation_rows, x="标签", y="市值")
                snapshots = list_portfolio_snapshots(DATABASE_PATH, limit=365)
                if len(snapshots) >= 2:
                    st.markdown("**组合市值历史**")
                    st.line_chart(
                        snapshots, x="snapshot_date", y="total_market_value"
                    )
                else:
                    st.caption("组合历史将在不同日期产生快照后显示趋势曲线。")
        
        # Clean Single-line HTML table to prevent markdown parsing glitches
        tr_html = ""
        for r in fund_rows:
            pure_str = f"{r['v_pure']*100:+.2f}%" if r['v_pure'] is not None else "--"
            off_str = f"{r['v_off']*100:+.2f}%" if r['v_off'] is not None else "--"
            hyb_str = f"{r['v_hyb']*100:+.2f}%"
            yield_str = f"{r['h_yield']*100:+.2f}%"
            
            pure_cls = "text-up" if (r['v_pure'] and r['v_pure'] > 0) else ("text-down" if (r['v_pure'] and r['v_pure'] < 0) else "text-neutral")
            off_cls = "text-up" if (r['v_off'] and r['v_off'] > 0) else ("text-down" if (r['v_off'] and r['v_off'] < 0) else "text-neutral")
            hyb_cls = "text-up text-bold" if r['v_hyb'] > 0 else ("text-down text-bold" if r['v_hyb'] < 0 else "text-neutral text-bold")
            yield_cls = "text-up" if r['h_yield'] > 0 else ("text-down" if r['h_yield'] < 0 else "text-neutral")
            
            # 今日主力净流入
            flow_val = r.get('main_flow')
            if flow_val is not None:
                flow_str = f"{flow_val:+.2f}万"
                flow_cls = "text-up" if flow_val > 0 else ("text-down" if flow_val < 0 else "text-neutral")
            else:
                flow_str = "--"
                flow_cls = "text-neutral"

            tr_html += (
                f"<tr>"
                f'<td style="text-align: left; font-weight: 500;">{r["name"]}</td>'
                f'<td><span class="{pure_cls}">{pure_str}</span></td>'
                f'<td><span class="{off_cls}">{off_str}</span></td>'
                f'<td><span class="{hyb_cls}">{hyb_str}</span></td>'
                f'<td><span class="{flow_cls}">{flow_str}</span></td>'
                f'<td><span class="{yield_cls}">{yield_str}</span></td>'
                f"<td>{r['tp']*100:.2f}%</td>"
                f"<td><b>{r['diag']}</b></td>"
                f"</tr>"
            )
            
        table_html = (
            f'<div class="table-container">'
            f'<table class="console-table">'
            f'<thead>'
            f'<tr>'
            f'<th style="text-align: left;">资产名称</th>'
            f'<th>归一动能</th>'
            f'<th>官方估算</th>'
            f'<th>严谨终值</th>'
            f'<th>今日主力净流</th>'
            f'<th>实时持有收益</th>'
            f'<th>止盈目标</th>'
            f'<th>诊断建议</th>'
            f'</tr>'
            f'</thead>'
            f'<tbody>'
            f'{tr_html}'
            f'</tbody>'
            f'</table>'
            f'</div>'
        )
        st.markdown(table_html, unsafe_allow_html=True)
        
        # ⚡ 今日待处理交易决策 (Trade Execution Booking closed-loop)
        st.markdown("##### ⚡ 今日待处理交易决策")
        
        pending_trades = 0
        for r in fund_rows:
            diag = r['diag']
            code = r['code']
            
            # Detect Buying Advice
            if diag.startswith("狙击补仓"):
                pending_trades += 1
                buy_amt = 200 # Default
                match = re.search(r'\+(\d+)元', diag)
                if match:
                    buy_amt = int(match.group(1))
                
                card_html = (
                    f'<div class="decision-card-buy">'
                    f'<div class="card-title">🟢 狙击加仓建议：{r["name"]} ({code})</div>'
                    f'<div class="card-body">今日净值波动跌破策略阈值，触发狙击补仓，建议买入：<b>{buy_amt}元</b>。<br>'
                    f'当前持仓：{r["shares"]:.2f} 份 | 预估成交净值：{r["est_nav"]:.4f}</div>'
                    f'</div>'
                )
                st.markdown(card_html, unsafe_allow_html=True)
                
                # Expandable booking form
                with st.expander(f"🛒 登记执行这笔买入 ({r['name']})"):
                    actual_nav = st.number_input("实际成交净值", min_value=0.0001, value=float(r['est_nav']), format="%.4f", key=f"nav_b_{code}")
                    actual_amt = st.number_input("实际买入金额(元)", min_value=0.01, value=float(buy_amt), step=50.0, key=f"amt_b_{code}")
                    actual_fee = st.number_input("申购费用(元)", min_value=0.0, value=0.0, step=0.1, key=f"fee_b_{code}")
                    
                    if st.button("💾 确认交易并自动更新持仓与均价", key=f"btn_b_{code}"):
                        config_data = load_json(CONFIG_PATH)
                        cfg = config_data[code]
                        
                        old_shares = cfg.get('shares', 0.0)
                        old_cost = cfg.get('cost', 0.0)
                        
                        new_shares = actual_amt / actual_nav
                        new_total_shares = old_shares + new_shares
                        
                        if new_total_shares > 0:
                            new_cost = (old_cost * old_shares + actual_amt + actual_fee) / new_total_shares
                        else:
                            new_cost = old_cost
                            
                        cfg['shares'] = round(new_total_shares, 2)
                        cfg['cost'] = round(new_cost, 4)
                        cfg['last_replenish_price'] = round(actual_nav, 4)
                        cfg['last_replenish_amount'] = actual_amt
                        cfg['last_replenish_date'] = datetime.datetime.now().strftime('%Y-%m-%d')
                        
                        save_json(CONFIG_PATH, config_data)
                        record_trade(
                            DATABASE_PATH,
                            fund_code=code,
                            fund_name=cfg.get('name', code),
                            side="BUY",
                            nav=actual_nav,
                            shares=new_shares,
                            gross_amount=actual_amt,
                            fee=actual_fee,
                            source="web",
                            position_shares_after=cfg['shares'],
                            cost_after=cfg['cost'],
                        )
                        st.success(f"加仓记账成功！更新后份额: {cfg['shares']}，更新后均价成本: {cfg['cost']}")
                        
            # Detect Selling Advice
            elif "建议卖" in diag or "建议清仓" in diag or diag.startswith("破MA"):
                pending_trades += 1
                sell_ratio = 0.33
                if "建议清仓" in diag or "清仓" in diag:
                    sell_ratio = 1.0
                else:
                    match = re.search(r'卖(\d+)%', diag)
                    if match:
                        sell_ratio = int(match.group(1)) / 100.0
                
                card_html = (
                    f'<div class="decision-card">'
                    f'<div class="card-title">🔴 止盈减仓建议：{r["name"]} ({code})</div>'
                    f'<div class="card-body">目前满足止盈达标均线破位，建议分批减仓：<b>{sell_ratio*100:.0f}%</b>。<br>'
                    f'当前持仓：{r["shares"]:.2f} 份 | 预估赎回份额：{r["shares"]*sell_ratio:.2f} 份</div>'
                    f'</div>'
                )
                st.markdown(card_html, unsafe_allow_html=True)
                
                # Expandable booking form
                with st.expander(f"🛒 登记执行这笔卖出 ({r['name']})"):
                    actual_nav = st.number_input("实际成交净值", min_value=0.0001, value=float(r['est_nav']), format="%.4f", key=f"nav_s_{code}")
                    actual_ratio = st.slider("实际卖出比例", min_value=0.05, max_value=1.0, value=max(0.05, float(sell_ratio)), step=0.05, key=f"ratio_s_{code}")
                    actual_fee = st.number_input("赎回费用(元)", min_value=0.0, value=0.0, step=0.1, key=f"fee_s_{code}")
                    
                    if st.button("💾 确认交易并自动减仓记账", key=f"btn_s_{code}"):
                        config_data = load_json(CONFIG_PATH)
                        cfg = config_data[code]
                        
                        old_shares = cfg.get('shares', 0.0)
                        sold_shares = old_shares * actual_ratio
                        new_total_shares = max(0.0, old_shares - sold_shares)
                        
                        cfg['shares'] = round(new_total_shares, 2)
                        cfg['last_sell_date'] = datetime.datetime.now().strftime('%Y-%m-%d')
                        
                        save_json(CONFIG_PATH, config_data)
                        record_trade(
                            DATABASE_PATH,
                            fund_code=code,
                            fund_name=cfg.get('name', code),
                            side="SELL",
                            nav=actual_nav,
                            shares=sold_shares,
                            gross_amount=sold_shares * actual_nav,
                            fee=actual_fee,
                            source="web",
                            position_shares_after=cfg['shares'],
                            cost_after=float(cfg.get('cost', 0)),
                        )
                        st.success(f"减仓记账成功！剩余持有份额: {cfg['shares']}")
                        
        if pending_trades == 0:
            if is_m_closed_for_autorefresh:
                st.info("💤 当前休市，无待处理交易。")
            else:
                st.info("🟢 暂无待处理交易。所有组合基金在合理波动区间内，巡航运行中。")
            
        # Control Buttons
        st.write("")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔄 手动刷新行情", width="stretch"):
                # Clicking a widget inside a fragment has already refreshed it.
                st.toast("行情已局部刷新", icon="✅")
        with c2:
            if st.button("🧠 运行后台 AI 智能分析", width="stretch"):
                with st.spinner("正在评估大盘并调节策略定投参数..."):
                    try:
                        from ai_agent import AIFundAgent
                        agent = AIFundAgent()
                        agent.run_analysis()
                        st.success("AI 智能分析运行完成！请查看“AI 智能诊断报告”标签卡。")
                    except Exception as e:
                        st.error(f"分析出错: {e}")
                        
    with col_left:
        render_dashboard()

    with col_right:
        st.markdown("##### 💬 问答咨询助手")
        
        # Initialize message history
        if "messages" not in st.session_state:
            st.session_state.messages = []
            
        # Reduced height (340px) to prevent vertical scrolling and align perfectly with left side buttons
        chat_container = st.container(height=340)
        with chat_container:
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
                    
        # Chat Input
        if user_prompt := st.chat_input("向 DeepSeek 提问当前主线策略..."):
            with chat_container:
                with st.chat_message("user"):
                    st.markdown(user_prompt)
            st.session_state.messages.append({"role": "user", "content": user_prompt})
            
            # Load config to get API keys
            config = load_json(CONFIG_PATH)
            settings = config.get('ai_agent_settings', {})
            api_key = settings.get('api_key', '')
            provider = settings.get('llm_provider', 'gemini').lower()
            base_url = settings.get('base_url', '')
            model_name = settings.get('model', '')
            
            # DeepSeek auto mapping
            if provider == 'deepseek':
                if not base_url: base_url = "https://api.deepseek.com"
                if not model_name or model_name == 'gemini-1.5-flash': model_name = "deepseek-chat"
            with chat_container:
                with st.chat_message("assistant"):
                    message_placeholder = st.empty()
                    message_placeholder.markdown("🤖 *Thinking...*")
                    
                    if not api_key:
                        ans = "[-] 错误：您尚未配置 API Key。请在第二栏“系统参数与配置中心”配置密钥后再试。"
                    else:
                        try:
                            # Load report content as context
                            report_context = ""
                            if os.path.exists(REPORT_PATH):
                                with open(REPORT_PATH, 'r', encoding='utf-8') as f:
                                    report_context = f.read()
                                    
                            # 构建当前实盘决策参数摘要，让模型回答做到"知行合一"
                            config_for_chat = load_json(CONFIG_PATH)
                            fund_params_summary = {}
                            for c, cfg in config_for_chat.items():
                                if c.isdigit() or (isinstance(c, str) and c.startswith('QD')):
                                    fund_params_summary[c] = {
                                        'name': cfg.get('name', ''),
                                        'daily_invest': cfg.get('daily_invest', 0),
                                        'multiplier': cfg.get('multiplier', 1.0),
                                        'tag': cfg.get('tag', ''),
                                        'base_daily_invest': cfg.get('base_daily_invest', 0)
                                    }

                            api_messages = [
                                {
                                    "role": "system",
                                    "content": (
                                        "你是一个中文智能助手，默认服务于用户的AI科技主线基金投资看板。"
                                        "当用户询问基金、市场、持仓、定投、补仓、止盈或报告相关问题时，"
                                        "优先结合下方投资诊断报告和实盘决策参数回答；"
                                        "当用户询问其他主题时，按通用助手方式正常回答，不要强行转回基金话题。\n\n"
                                        "下面是目前最新的投资诊断报告上下文：\n\n"
                                        f"{report_context}\n\n"
                                        f"当前实盘决策参数 (来自系统配置): {json.dumps(fund_params_summary, ensure_ascii=False)}\n\n"
                                        "回答规则："
                                        "1. 投资相关问题优先引用当前报告、持仓收益、定投金额、补仓倍数等已知数据，做到'知行合一'。"
                                        "2. 不要编造实时行情、基金净值或配置中不存在的数据；如果上下文不足，要明确说明。"
                                        "3. 投资建议保持谨慎，遵循不频繁交易、分批建仓、避免情绪化追高、防守底仓稳固。"
                                        "4. 非投资问题直接回答用户问题，保持简洁自然。"
                                        "5. 始终用中文回答。"
                                    )
                                }
                            ]
                            for msg in st.session_state.messages:
                                api_messages.append({"role": msg["role"], "content": msg["content"]})
                                
                            headers = {"Content-Type": "application/json"}
                            if provider == 'gemini':
                                url = build_gemini_url(base_url, model_name, api_key)
                                gemini_contents = []
                                for msg in api_messages:
                                    role = 'model' if msg['role'] == 'assistant' else 'user'
                                    if msg['role'] == 'system':
                                        gemini_contents.append({"role": "user", "parts": [{"text": f"[System Instruction] {msg['content']}"}]})
                                    else:
                                        gemini_contents.append({"role": role, "parts": [{"text": msg['content']}]})
                                payload = {"contents": gemini_contents}
                                res = requests.post(url, json=payload, headers=headers, timeout=40)
                                if res.status_code == 200:
                                    ans = res.json()['candidates'][0]['content']['parts'][0]['text']
                                else:
                                    ans = format_api_error(res)
                            else:
                                url = build_chat_completions_url(provider, base_url)
                                headers["Authorization"] = f"Bearer {api_key}"
                                payload = {
                                    "model": model_name,
                                    "messages": api_messages,
                                    "temperature": 0.4
                                }
                                res = requests.post(url, json=payload, headers=headers, timeout=40)
                                if res.status_code == 200:
                                    ans = res.json()['choices'][0]['message']['content']
                                else:
                                    ans = format_api_error(res)
                        except Exception as e:
                            ans = f"请求出错: {e}"
                            
                    message_placeholder.markdown(ans)
                    st.session_state.messages.append({"role": "assistant", "content": ans})
                    st.rerun()

# ====================================================
# TAB 2: ⚙️ 系统参数与配置中心
# ====================================================
with tab_config:
    col_cfg_left, col_cfg_right = st.columns([0.55, 0.45], gap="large")
    
    with col_cfg_left:
        st.markdown("##### 📈 基金参数编辑器")
        
        config_data = load_json(CONFIG_PATH)
        sorted_keys = [k for k in config_data.keys() if (k.isdigit() or k.startswith('QD'))]
        fund_options = {k: f"{k} - {config_data[k]['name']}" for k in sorted_keys}

        with st.expander("📥 批量导入 CSV / XLSX"):
            st.caption(
                "必填列：code、name、cost、shares；可选列会使用系统默认值。"
                "支持中文列名，导入前会预览且不会修改模型配置。"
            )
            st.download_button(
                "⬇️ 下载 CSV 模板",
                data=csv_template(),
                file_name="fund_import_template.csv",
                mime="text/csv",
                width="stretch",
            )
            import_file = st.file_uploader(
                "选择基金文件",
                type=["csv", "xlsx"],
                accept_multiple_files=False,
                key="fund_batch_import",
            )
            duplicate_label = st.radio(
                "遇到已存在的基金",
                ["跳过现有基金", "覆盖基金字段"],
                horizontal=True,
                key="fund_duplicate_policy",
            )
            if import_file is not None:
                try:
                    import_frame = read_fund_table(
                        import_file.getvalue(), import_file.name
                    )
                    import_rows, import_errors = normalize_fund_table(import_frame)
                except Exception as exc:
                    import_rows, import_errors = [], [str(exc)]

                if import_errors:
                    st.error("文件校验未通过，尚未修改任何配置。")
                    for error in import_errors[:30]:
                        st.write(f"- {error}")
                    if len(import_errors) > 30:
                        st.write(f"- 其余 {len(import_errors) - 30} 条错误已省略")
                elif not import_rows:
                    st.warning("文件中没有可导入的基金记录。")
                else:
                    duplicate_policy = (
                        "skip" if duplicate_label == "跳过现有基金" else "overwrite"
                    )
                    preview_rows = []
                    for row in import_rows:
                        preview = dict(row)
                        exists = row["code"] in config_data
                        preview["导入动作"] = (
                            "跳过" if exists and duplicate_policy == "skip"
                            else ("覆盖" if exists else "新增")
                        )
                        preview_rows.append(preview)
                    st.markdown(f"**校验通过：{len(import_rows)} 条记录**")
                    st.dataframe(preview_rows, width="stretch", hide_index=True)

                    if st.button(
                        "✅ 确认批量导入",
                        type="primary",
                        width="stretch",
                        key="confirm_fund_batch_import",
                    ):
                        new_config, summary = apply_import(
                            config_data, import_rows, duplicate_policy
                        )
                        backup_path = backup_file(CONFIG_PATH)
                        save_json(CONFIG_PATH, new_config)
                        st.success(
                            f"导入完成：新增 {len(summary['added'])}，"
                            f"覆盖 {len(summary['overwritten'])}，"
                            f"跳过 {len(summary['skipped'])}。"
                        )
                        if backup_path:
                            st.caption(f"原配置已备份：{backup_path.name}")
                        time.sleep(1)
                        st.rerun()

        with st.expander("🧾 交易流水与导出"):
            recent_trades = list_trades(DATABASE_PATH, limit=200)
            if recent_trades:
                display_trades = []
                for trade in recent_trades:
                    display_trades.append({
                        "成交时间": trade["executed_at"],
                        "基金代码": trade["fund_code"],
                        "基金名称": trade["fund_name"],
                        "方向": "买入" if trade["side"] == "BUY" else "卖出",
                        "净值": trade["nav"],
                        "份额": trade["shares"],
                        "成交金额": trade["gross_amount"],
                        "费用": trade["fee"],
                        "交易后份额": trade["position_shares_after"],
                        "来源": trade["source"],
                    })
                st.dataframe(display_trades, width="stretch", hide_index=True)
                st.download_button(
                    "⬇️ 导出全部交易流水 CSV",
                    data=trades_csv(DATABASE_PATH),
                    file_name="fund_transactions.csv",
                    mime="text/csv",
                    width="stretch",
                )
            else:
                st.info("尚无交易流水；在监控页面登记买入或卖出后会自动记录。")
        
        # Mode selector: Edit or Add
        mode = st.radio("模式", ["修改现有基金", "添加新基金"], horizontal=True, label_visibility="collapsed")
        
        if mode == "修改现有基金" and sorted_keys:
            selected_code = st.selectbox("选择要修改的基金", options=sorted_keys, format_func=lambda x: fund_options[x])
            cfg = config_data[selected_code]
            
            st.markdown("###### **1. 📝 核心参数管理**")
            
            c1, c2 = st.columns(2)
            with c1:
                cost_val = st.number_input("持仓成本价 (cost)", min_value=0.0001, value=float(cfg.get('cost', 1.0)), format="%.4f")
                shares_val = st.number_input("当前持有总份额 (shares)", min_value=0.0, value=float(cfg.get('shares', 0.0)), step=100.0, format="%.2f")
                tp_val = st.number_input("止盈目标比例 (tp)", min_value=0.0, max_value=1.0, value=float(cfg.get('tp', 0.15)), step=0.01, format="%.2f")
                last_rep_price_val = st.number_input("上次补仓净值 (last_replenish_price)", min_value=0.0001, value=float(cfg.get('last_replenish_price', cfg.get('cost', 1.0))), format="%.4f")
            with c2:
                base_daily_val = st.number_input("基础每日定投 (base_daily_invest)", value=int(cfg.get('base_daily_invest', cfg.get('daily_invest', 20))), step=5)
                base_mult_val = st.number_input("基础补仓乘数 (base_multiplier)", value=float(cfg.get('base_multiplier', cfg.get('multiplier', 1.5))), step=0.1, format="%.1f")
                last_rep_amt_val = st.number_input("上次补仓金额(元) (last_replenish_amount)", value=float(cfg.get('last_replenish_amount', 0.0)), step=50.0, format="%.2f")
                last_rep_date_val = st.text_input("上次补仓日期 (last_replenish_date)", value=cfg.get('last_replenish_date', ''))
            
            st.write("")
            with st.expander("⚙️ 高级风控阈值参数 (常规维持默认，微调展开)"):
                c3, c4 = st.columns(2)
                with c3:
                    drop_val = st.number_input("单日暴跌触发线 (drop)", value=float(cfg.get('drop', -0.025)), step=0.005, format="%.3f")
                    gap_val = st.number_input("空间锁拦截线 (gap)", value=float(cfg.get('gap', 0.03)), step=0.005, format="%.3f")
                    cap_val = st.number_input("补仓绝对硬顶/元 (cap)", value=int(cfg.get('cap', 500)), step=50)
                with c4:
                    ratio_val = st.number_input("破位后止盈卖出比例 (ratio)", min_value=0.0, max_value=1.0, value=float(cfg.get('ratio', 0.33)), step=0.05, format="%.2f")
                    tp_ma_val = st.selectbox("逃顶护航均线 (tp_ma)", [5, 10, 20], index=[5, 10, 20].index(cfg.get('tp_ma', 5)))
                    
            c5, c6 = st.columns(2)
            with c5:
                if st.button("💾 保存基金配置", width="stretch"):
                    cfg['cost'] = cost_val
                    cfg['shares'] = shares_val
                    cfg['base_daily_invest'] = base_daily_val
                    cfg['daily_invest'] = base_daily_val
                    cfg['base_multiplier'] = base_mult_val
                    cfg['multiplier'] = base_mult_val
                    cfg['tp'] = tp_val
                    cfg['last_replenish_price'] = last_rep_price_val
                    cfg['last_replenish_amount'] = last_rep_amt_val
                    cfg['last_replenish_date'] = last_rep_date_val
                    cfg['drop'] = drop_val
                    cfg['gap'] = gap_val
                    cfg['cap'] = cap_val
                    cfg['ratio'] = ratio_val
                    cfg['tp_ma'] = tp_ma_val
                    
                    save_json(CONFIG_PATH, config_data)
                    st.success(f"基金 {selected_code} 配置修改成功！")
                    time.sleep(1)
                    st.rerun()
                    
            with c6:
                delete_confirm = st.checkbox("确认要彻底删除此基金")
                if st.button("❌ 彻底删除基金", width="stretch", disabled=not delete_confirm):
                    config_data.pop(selected_code)
                    save_json(CONFIG_PATH, config_data)
                    st.warning(f"基金 {selected_code} 已从配置中彻底删除！")
                    time.sleep(1)
                    st.rerun()
                    
        elif mode == "添加新基金":
            st.markdown("###### **➕ 录入新基金基本参数**")
            new_code = st.text_input("基金代码 (6位数字)", value="", max_chars=6)
            new_name = st.text_input("基金名称", value="")
            
            c_new1, c_new2 = st.columns(2)
            with c_new1:
                new_cost = st.number_input("持仓成本价", min_value=0.0001, value=1.0000, format="%.4f")
                new_shares = st.number_input("当前持有份额", min_value=0.0, value=0.00, step=100.0, format="%.2f")
                new_tag = st.selectbox("基金标签属性", ["科创", "红利", "黄金", "救援", "海外", "周期", "限购"])
            with c_new2:
                new_invest = st.number_input("每日基础定投额(元)", value=20, step=5)
                new_mult = st.number_input("补仓放大乘数", value=1.5, step=0.1, format="%.1f")
                new_proxy = st.text_input("场内ETF代理代码 (如 sh588200)", value="")
                
            if st.button("💾 确认添加新基金", width="stretch"):
                if not new_code.isdigit() or len(new_code) != 6:
                    st.error("请输入合法的6位数字基金代码！")
                elif not new_name:
                    st.error("请输入基金名称！")
                elif new_code in config_data:
                    st.error("该基金代码已存在于配置中！")
                else:
                    config_data[new_code] = {
                        "name": new_name,
                        "tag": new_tag,
                        "cost": new_cost,
                        "shares": new_shares,
                        "proxy": new_proxy,
                        "multiplier": new_mult,
                        "daily_invest": new_invest,
                        "last_replenish_price": new_cost,
                        "last_replenish_amount": 0.0,
                        "last_replenish_date": "",
                        "drop": -0.025 if new_tag == "科创" else (-0.008 if new_tag == "红利" else -0.01),
                        "gap": 0.03,
                        "cap": 500,
                        "tp": 0.15,
                        "ratio": 0.33,
                        "tp_ma": 5,
                        "base_daily_invest": new_invest,
                        "base_multiplier": new_mult
                    }
                    save_json(CONFIG_PATH, config_data)
                    st.success(f"新基金 {new_code} - {new_name} 添加成功！")
                    time.sleep(1)
                    st.rerun()

    with col_cfg_right:
        st.markdown("##### 🤖 大模型参数微调")
        
        config_data = load_json(CONFIG_PATH)
        sett = config_data.get('ai_agent_settings', {})
        
        st.markdown('<div class="config-box">', unsafe_allow_html=True)
        provider_sel = st.selectbox("API 渠道", ["deepseek", "gemini", "openai"], index=["deepseek", "gemini", "openai"].index(sett.get('llm_provider', 'deepseek')), key="prov")
        key_input = st.text_input("API Key（留空则保持现有密钥）", value="", type="password", key="key")
        url_input = st.text_input("Base URL (选填)", value=sett.get('base_url', ''), key="url")
        model_input = st.text_input("模型名称", value=sett.get('model', 'deepseek-chat'), key="mod")
        enable_llm_input = st.checkbox("启用 AI 分析", value=sett.get('enable_llm', True), key="en_llm")
        
        if st.button("💾 保存大模型配置", width="stretch"):
            config_data['ai_agent_settings']['llm_provider'] = provider_sel
            if key_input.strip():
                config_data['ai_agent_settings']['api_key'] = key_input.strip()
            config_data['ai_agent_settings']['base_url'] = url_input
            config_data['ai_agent_settings']['model'] = model_input
            config_data['ai_agent_settings']['enable_llm'] = enable_llm_input
            save_json(CONFIG_PATH, config_data)
            st.success("大模型配置已保存！")
            time.sleep(1)
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        
        st.markdown("##### 🏖️ 全球节假日管理")
        with st.expander("查看/编辑休市休假日期 (JSON 格式)"):
            holidays_list = config_data.get("global_holidays", [])
            holidays_str = st.text_area("休市日期列表", value=json.dumps(holidays_list, ensure_ascii=False, indent=2), height=150)
            if st.button("💾 保存交易节假日配置", width="stretch"):
                try:
                    parsed_list = json.loads(holidays_str)
                    if isinstance(parsed_list, list):
                        config_data["global_holidays"] = parsed_list
                        save_json(CONFIG_PATH, config_data)
                        st.success("休市休假配置修改成功！")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("输入必须是 JSON 字符串数组格式，例如 ['2026-01-01', '2026-01-02']")
                except Exception as ex:
                    st.error(f"解析出错: {ex}")

# ====================================================
# TAB 3: 🤖 AI 智能诊断报告
# ====================================================
with tab_ai_report:
    st.markdown("##### 🤖 AI 智能诊断决策报告")
    
    if os.path.exists(REPORT_PATH):
        try:
            with open(REPORT_PATH, 'r', encoding='utf-8') as f:
                report_content = f.read()
            st.markdown(report_content)
        except Exception as e:
            st.error(f"读取报告失败: {e}")
    else:
        st.info("💡 暂无分析报告。请在第一栏“实时监控与智能对话”中点击下方的“运行后台 AI 智能分析”按钮生成报告。")
