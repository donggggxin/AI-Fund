# -*- coding: utf-8 -*-
import sys
import time
import datetime
import json
import os
import requests
import urllib3
import traceback
import io
import re

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from diagnostics import fetch_market_prices, compute_fund_diagnostic, fetch_nav_batch
from llm_utils import build_chat_completions_url, build_gemini_url, format_api_error
from storage import initialize_data_files, save_json
from time_utils import beijing_now
from source_links import build_sources_markdown

class AIFundAgent:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.getenv("FUND_DATA_DIR", self.base_dir)
        initialize_data_files(self.data_dir, self.base_dir)
        self.config_path = os.path.join(self.data_dir, "fund_config.json")
        self.report_path = os.path.join(self.data_dir, "agent_report.md")
        self.holdings_cache_path = os.path.join(self.data_dir, "holdings_cache.json")
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'http://fundf10.eastmoney.com/'
        })
        self.holdings_cache = {}
        if os.path.exists(self.holdings_cache_path):
            try:
                with open(self.holdings_cache_path, 'r', encoding='utf-8-sig') as f:
                    self.holdings_cache = json.load(f)
            except:
                pass
        self.load_config()

    def load_config(self):
        with open(self.config_path, 'r', encoding='utf-8-sig') as f:
            self.config = json.load(f)
        
        # Self-healing: Initialize base_daily_invest if not present
        changed = False
        for code, cfg in self.config.items():
            if not (code.isdigit() or code.startswith('QD')):
                continue
            if 'base_daily_invest' not in cfg:
                cfg['base_daily_invest'] = cfg.get('daily_invest', 0)
                changed = True
            if 'base_multiplier' not in cfg:
                cfg['base_multiplier'] = cfg.get('multiplier', 1.5)
                changed = True
        
        if 'ai_agent_settings' not in self.config:
            self.config['ai_agent_settings'] = {
                "llm_provider": "gemini",
                "api_key": "",
                "base_url": "",
                "model": "gemini-1.5-flash",
                "enable_llm": False,
                "daily_invest_scales": {
                    "AI主升浪": 1.0,
                    "高位震荡": 0.8,
                    "回调期": 1.5,
                    "趋势破坏": 0.0
                },
                "last_market_stage": "高位震荡",
                "last_analysis_date": ""
            }
            changed = True
            
        if changed:
            self.save_config()

    def save_config(self):
        save_json(self.config_path, self.config)

    def fetch_fund_history(self, fund_code, page_size=60):
        """Fetch historical NAVs of a mutual fund from Eastmoney"""
        try:
            url = f"http://api.fund.eastmoney.com/f10/lsjz?fundCode={fund_code}&pageIndex=1&pageSize={page_size}"
            res = self.session.get(url, timeout=10, verify=False).json()
            if res.get('Data', {}).get('LSJZList'):
                ls = res['Data']['LSJZList']
                navs = [float(x.get('DWJZ') or x.get('LJJZ')) for x in ls if x.get('DWJZ') or x.get('LJJZ')]
                dates = [x.get('FSRQ') for x in ls if x.get('DWJZ') or x.get('LJJZ')]
                return navs, dates
        except Exception as e:
            print(f"[-] 抓取基金 {fund_code} 历史净值失败: {e}")
        return [], []

    def fetch_today_est_growth(self, fund_code):
        """获取基金今日实时估算涨跌幅 (来自天天基金)"""
        try:
            url = f"http://fundgz.1234567.com.cn/js/{fund_code}.js?rt={int(time.time())}"
            r = self.session.get(url, timeout=3).text
            data = json.loads(r[r.find('{'):r.rfind('}') + 1])
            return float(data['gszzl']) / 100.0
        except:
            return None

    def fetch_etf_history(self, symbol, length=60):
        """Fetch historical K-line data of A-share ETFs from Sina"""
        try:
            url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={length}"
            res = self.session.get(url, timeout=10).json()
            if isinstance(res, list):
                # Ensure fields are converted to float
                formatted = []
                for item in res:
                    formatted.append({
                        "date": item.get('day'),
                        "close": float(item.get('close')),
                        "volume": float(item.get('volume')),
                        "high": float(item.get('high')),
                        "low": float(item.get('low')),
                        "open": float(item.get('open'))
                    })
                return formatted
        except Exception as e:
            print(f"[-] 抓取 ETF {symbol} 历史日K失败: {e}")
        return []

    def fetch_realtime_quotes(self, symbols_list):
        """Fetch real-time stock/index quotes from Tencent API"""
        if not symbols_list:
            return {}
        try:
            url = f"http://qt.gtimg.cn/q={','.join(symbols_list)}"
            res = requests.get(url, timeout=10).text
            quotes = {}
            for line in res.split('\n'):
                if '="' in line:
                    symbol_key = line.split('=')[0].split('_')[-1]
                    d = line.split('"')[1].split('~')
                    if len(d) > 49:
                        # Detect if US stock or A-share based on symbol prefix/suffix
                        is_us = symbol_key.startswith('us')
                        quotes[symbol_key] = {
                            "name": d[1],
                            "symbol": d[2],
                            "price": float(d[3]),
                            "prev_close": float(d[4]),
                            "open": float(d[5]),
                            "change_pct": float(d[32]) if is_us else float(d[32]),
                            "high_52w": float(d[48]) if is_us else float(d[47]),
                            "low_52w": float(d[49]) if is_us else float(d[48]),
                            "volume": float(d[6]),
                            "turnover": float(d[37]) if not is_us else 0.0,
                            "datetime": d[30]
                        }
            return quotes
        except Exception as e:
            print(f"[-] 抓取实时行情 {symbols_list} 失败: {e}")
            traceback.print_exc()
        return {}

    def fetch_etf_fund_flow(self, proxy_code, limit=10):
        """Fetch historical fund flow of A-share ETFs from Eastmoney (主力占比由本地公式计算)"""
        if not proxy_code:
            return []
        try:
            market = "1" if proxy_code.startswith("sh") else "0"
            code = proxy_code[2:]
            secid = f"{market}.{code}"
            url = f"http://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt={limit}&klt=101&secid={secid}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            res = self.session.get(url, timeout=10, verify=False).json()
            if res.get('rc') == 0 and res.get('data') and res['data'].get('klines'):
                flows = []
                for line in res['data']['klines']:
                    parts = line.split(',')
                    if len(parts) >= 6:
                        main_net = float(parts[1])
                        small_net = float(parts[2])
                        medium_net = float(parts[3])
                        large_net = float(parts[4])
                        super_large_net = float(parts[5])
                        # 本地精准计算主力净流入占比，避免 API 返回占比列为 0 的缺陷
                        total_throughput = abs(super_large_net) + abs(large_net) + abs(medium_net) + abs(small_net)
                        main_pct = main_net / total_throughput if total_throughput > 0 else 0.0
                        flows.append({
                            "date": parts[0],
                            "main_net": main_net,
                            "small_net": small_net,
                            "medium_net": medium_net,
                            "large_net": large_net,
                            "super_large_net": super_large_net,
                            "main_pct": main_pct
                        })
                return flows
        except Exception as e:
            print(f"[-] 抓取 ETF {proxy_code} 资金流向失败: {e}")
        return []

    def calculate_ma(self, prices, period):
        if len(prices) < period:
            return sum(prices) / len(prices) if prices else 0.0
        return sum(prices[:period]) / period

    def calculate_drawdown(self, current, peak):
        if peak <= 0:
            return 0.0
        return (current - peak) / peak

    def run_analysis(self):
        print("\n" + "="*60)
        print("[*] [AI Agent] AI 科技主线基金投资智能体 - 开始动态扫描诊断...")
        print("="*60)

        today_str = beijing_now().strftime('%Y-%m-%d')

        # 1. 动态发现全部基金和ETF代理
        fund_codes = [k for k in self.config if k.isdigit() or k.startswith('QD')]
        proxy_set = set()
        for code in fund_codes:
            p = self.config[code].get('proxy', '')
            if p:
                proxy_set.add(p)
        proxy_list = list(proxy_set)

        # 2. 抓取基金历史净值、均线、近期收益
        fund_data = {}
        for code in fund_codes:
            name = self.config[code]['name']
            print(f"[*] 同步基金: {name} ({code})...")
            navs, dates = self.fetch_fund_history(code, page_size=120)
            # 插入今日估算净值虚拟节点，消除交易日数据滞后
            if navs and dates and dates[0] != today_str:
                v_growth = self.fetch_today_est_growth(code)
                if v_growth is not None:
                    today_est_nav = navs[0] * (1 + v_growth)
                    navs = [today_est_nav] + navs
                    dates = [today_str] + dates
            if navs:
                current_nav = navs[0]
                fund_data[code] = {
                    "name": name,
                    "nav": current_nav,
                    "nav_date": dates[0] if dates else "未知",
                    "ma5": self.calculate_ma(navs, 5),
                    "ma10": self.calculate_ma(navs, 10),
                    "ma20": self.calculate_ma(navs, 20),
                    "ma60": self.calculate_ma(navs, 60),
                    "ma120": self.calculate_ma(navs, 120),
                    "drawdown": self.calculate_drawdown(current_nav, max(navs)),
                    "above_ma20": current_nav > self.calculate_ma(navs, 20),
                    "above_ma60": current_nav > self.calculate_ma(navs, 60),
                    "above_ma120": current_nav > self.calculate_ma(navs, 120),
                    "recent": {
                        "r5d": (current_nav - navs[min(4, len(navs)-1)]) / navs[min(4, len(navs)-1)] if len(navs) >= 5 else 0,
                        "r10d": (current_nav - navs[min(9, len(navs)-1)]) / navs[min(9, len(navs)-1)] if len(navs) >= 10 else 0,
                        "r20d": (current_nav - navs[min(19, len(navs)-1)]) / navs[min(19, len(navs)-1)] if len(navs) >= 20 else 0,
                    },
                }
            else:
                fund_data[code] = {
                    "name": name, "nav": self.config[code]['cost'],
                    "nav_date": "未知",
                    "ma5": self.config[code]['cost'], "ma10": self.config[code]['cost'],
                    "ma20": self.config[code]['cost'], "ma60": self.config[code]['cost'],
                    "ma120": self.config[code]['cost'], "drawdown": 0.0,
                    "above_ma20": True, "above_ma60": True, "above_ma120": True,
                    "recent": {"r5d": 0, "r10d": 0, "r20d": 0},
                }

        # 3. 抓取ETF代理历史K线
        etf_data = {}
        for sym in proxy_list:
            print(f"[*] 同步ETF代理: {sym}...")
            kline = self.fetch_etf_history(sym, length=120)
            if kline:
                closes = [x['close'] for x in kline]
                volumes = [x['volume'] for x in kline]
                kline_last_date = kline[-1].get('date', '')
                cp = closes[-1]
                n = len(closes)
                etf_data[sym] = {
                    "name": sym,
                    "price": cp,
                    "ma20": sum(closes[-20:])/20 if n >= 20 else cp,
                    "ma60": sum(closes[-60:])/60 if n >= 60 else cp,
                    "ma120": sum(closes[-120:])/120 if n >= 120 else cp,
                    "ma20_volume": sum(volumes[-20:])/20 if n >= 20 else 1,
                    "current_volume": volumes[-1],
                    "volume_ratio": volumes[-1] / (sum(volumes[-20:])/20) if n >= 20 and sum(volumes[-20:]) > 0 else 1.0,
                    "above_ma20": cp > (sum(closes[-20:])/20 if n >= 20 else cp),
                    "above_ma60": cp > (sum(closes[-60:])/60 if n >= 60 else cp),
                    "above_ma120": cp > (sum(closes[-120:])/120 if n >= 120 else cp),
                    "_closes": closes,
                    "_kline_last_date": kline_last_date,
                }
            else:
                etf_data[sym] = {
                    "name": sym, "price": 1.0, "ma20": 1.0, "ma60": 1.0, "ma120": 1.0,
                    "ma20_volume": 1.0, "current_volume": 1.0, "volume_ratio": 1.0,
                    "above_ma20": True, "above_ma60": True, "above_ma120": True,
                }

        # 4. 抓取ETF资金流向
        print("[*] 拉取ETF主力资金流向...")
        etf_flow_data = {}
        for sym in proxy_list:
            flows = self.fetch_etf_fund_flow(sym, limit=5)
            if flows:
                etf_flow_data[sym] = flows

        # 5. 抓取美股及ETF实时行情
        us_symbols = ["usNVDA", "usTSM", "usMSFT", "us.IXIC"]
        all_quote_syms = us_symbols + proxy_list
        print("[*] 拉取美股及全球指数实时行情...")
        realtime = self.fetch_realtime_quotes(all_quote_syms)

        market_metrics = {}
        for sym in us_symbols:
            if sym in realtime:
                q = realtime[sym]
                market_metrics[sym] = {
                    "name": q['name'], "price": q['price'],
                    "high_52w": q['high_52w'], "low_52w": q['low_52w'],
                    "drawdown": self.calculate_drawdown(q['price'], q['high_52w']),
                    "change_pct": q['change_pct'],
                }
            else:
                market_metrics[sym] = {
                    "name": sym, "price": 0, "high_52w": 0, "low_52w": 0,
                    "drawdown": 0, "change_pct": 0,
                }

        for sym in proxy_list:
            if sym in realtime:
                q = realtime[sym]
                if sym in etf_data:
                    etf_data[sym]["price"] = q['price']
                    etf_data[sym]["drawdown"] = self.calculate_drawdown(q['price'], q['high_52w'])
                    etf_data[sym]["change_pct"] = q['change_pct']
                    etf_data[sym]["high_52w"] = q['high_52w']
                    etf_data[sym]["low_52w"] = q['low_52w']

        # 5.5 用今日实时价格更新ETF收盘序列并重算均线 (消除交易日数据滞后)
        for sym in proxy_list:
            if sym in realtime and sym in etf_data:
                q = realtime[sym]
                stored = etf_data[sym]
                _closes = stored.get('_closes', [])
                _last_date = stored.get('_kline_last_date', '')
                if _closes and _last_date:
                    new_closes = (
                        _closes[:-1] + [q['price']]
                        if _last_date == today_str
                        else _closes + [q['price']]
                    )
                    n = len(new_closes)
                    cp = new_closes[-1]
                    stored["ma20"] = sum(new_closes[-20:]) / 20 if n >= 20 else cp
                    stored["ma60"] = sum(new_closes[-60:]) / 60 if n >= 60 else cp
                    stored["ma120"] = sum(new_closes[-120:]) / 120 if n >= 120 else cp
                    stored["above_ma20"] = cp > stored["ma20"]
                    stored["above_ma60"] = cp > stored["ma60"]
                    stored["above_ma120"] = cp > stored["ma120"]
                # 清理内部暂存字段
                stored.pop('_closes', None)
                stored.pop('_kline_last_date', None)

        # 6. 量化评分 (趋势60 + 回撤30 + 量能10 + 资金流向10 = 110)
        available_etfs = [s for s in proxy_list if s in etf_data]

        trend_score = 0
        for sym in available_etfs:
            d = etf_data[sym]
            if d.get("above_ma20"): trend_score += 10
            if d.get("above_ma60"): trend_score += 5

        dd_score = 0
        nasdaq_dd = abs(market_metrics.get("us.IXIC", {}).get("drawdown", 0))
        nvda_dd = abs(market_metrics.get("usNVDA", {}).get("drawdown", 0))
        if nasdaq_dd < 0.05: dd_score += 15
        elif nasdaq_dd < 0.10: dd_score += 10
        elif nasdaq_dd < 0.15: dd_score += 5
        if nvda_dd < 0.08: dd_score += 15
        elif nvda_dd < 0.15: dd_score += 10
        elif nvda_dd < 0.25: dd_score += 5

        vol_score = 0
        chips_vol = etf_data.get("sh588200", {}).get("volume_ratio", 1.0)
        if 0.8 <= chips_vol <= 1.5: vol_score = 10
        elif chips_vol > 0: vol_score = 5

        # 资金流向评分
        flow_score = 5
        domestic_flows = []
        for sym in etf_flow_data:
            if etf_flow_data[sym]:
                domestic_flows.append(etf_flow_data[sym][-1]['main_pct'])
        if domestic_flows:
            avg_flow_pct = sum(domestic_flows) / len(domestic_flows)
            if avg_flow_pct > 0.05: flow_score = 10
            elif avg_flow_pct > 0: flow_score = 8
            elif avg_flow_pct > -0.05: flow_score = 5
            else: flow_score = 2

        total_score = trend_score + dd_score + vol_score + flow_score

        if total_score >= 82: market_stage = "AI主升浪"
        elif total_score >= 60: market_stage = "高位震荡"
        elif total_score >= 33: market_stage = "回调期"
        else: market_stage = "趋势破坏"

        print(f"[*] 量化健康指数: {total_score}/110 (趋势{trend_score} 回撤{dd_score} 量能{vol_score} 资金{flow_score}) | 阶段: 【{market_stage}】")

        # 7. 本地引擎决策：调整 daily_invest / multiplier
        scales = self.config['ai_agent_settings']['daily_invest_scales']
        scale = scales.get(market_stage, 1.0)
        print(f"[*] 自动调整交易参数 (阶段系数: {scale:.1f}x)...")

        updates_summary = []
        for code in fund_codes:
            cfg = self.config[code]
            tag = cfg.get('tag', '')
            name = cfg.get('name', '')

            if tag == '科创':
                base_invest = cfg.get('base_daily_invest', cfg['daily_invest'])
                old_invest = cfg.get('daily_invest', base_invest)
                new_invest = int(base_invest * scale)
                cfg['daily_invest'] = new_invest

                base_mult = cfg.get('base_multiplier', 1.5)
                if market_stage == "回调期":
                    cfg['multiplier'] = round(base_mult * 1.3, 2)
                    cfg['drop'] = -0.015
                elif market_stage == "趋势破坏":
                    cfg['multiplier'] = round(base_mult * 0.5, 2)
                    cfg['drop'] = -0.03
                else:
                    cfg['multiplier'] = base_mult
                    cfg['drop'] = cfg.get('drop', -0.025)

                if old_invest != new_invest:
                    updates_summary.append(f"{name}: 定投 {old_invest}->{new_invest}元, 倍数{cfg['multiplier']}x")
            else:
                base_invest = cfg.get('base_daily_invest', cfg['daily_invest'])
                cfg['daily_invest'] = base_invest
                cfg['multiplier'] = cfg.get('base_multiplier', 1.0)

        self.config['ai_agent_settings']['last_market_stage'] = market_stage
        self.config['ai_agent_settings']['last_analysis_date'] = today_str
        self.save_config()
        print("[+] 参数已保存。")
        for s in updates_summary:
            print(f"    - {s}")

        # 8. 计算每只基金持仓诊断
        nav_cache, nav_date_cache = fetch_nav_batch(self.session, fund_codes)
        all_stock_codes = set()
        for code in fund_codes:
            for sc, _ in self.holdings_cache.get(code, []):
                all_stock_codes.add(sc)
            proxy = self.config[code].get('proxy', '')
            if proxy:
                all_stock_codes.add(proxy)
        stock_prices = fetch_market_prices(list(all_stock_codes))

        now = beijing_now()
        cur_t = now.time()
        is_weekend = now.weekday() >= 5
        holidays = self.config.get('global_holidays', [])
        is_market_open = (
            datetime.time(9, 30) <= cur_t < datetime.time(11, 30)
            or datetime.time(13, 0) <= cur_t < datetime.time(15, 0)
        )
        is_market_closed = is_weekend or (today_str in holidays) or not is_market_open
        is_trading_day = not is_weekend and today_str not in holidays
        after_close_today = is_trading_day and cur_t >= datetime.time(15, 0)

        trend_matrix = {}
        for code in fund_codes:
            if code in fund_data:
                fd = fund_data[code]
                trend_matrix[code] = {
                    "MA5": fd.get("ma5", 0),
                    "MA10": fd.get("ma10", 0),
                    "MA20": fd.get("ma20", 0),
                    "MA60": fd.get("ma60", 0),
                }

        # 持久化趋势矩阵，供 web_app.py 等模块读取（含今日估算净值修正后的真实均线）
        trend_path = os.path.join(self.data_dir, "trend_matrix.json")
        with open(trend_path, 'w', encoding='utf-8') as f:
            json.dump(trend_matrix, f, ensure_ascii=False, indent=4)

        fund_diags = {}
        for code in fund_codes:
            fund_diags[code] = compute_fund_diagnostic(
                code, self.config, nav_cache, nav_date_cache,
                stock_prices, self.holdings_cache, trend_matrix,
                today_str, is_market_closed, after_close_today,
            )

        # 9. 构建报告数据
        report_data = {
            "market_stage": market_stage,
            "total_score": total_score,
            "trend_score": trend_score,
            "dd_score": dd_score,
            "vol_score": vol_score,
            "flow_score": flow_score,
            "scale": scale,
            "market_metrics": market_metrics,
            "etf_data": etf_data,
            "etf_flow_data": etf_flow_data,
            "fund_data": fund_data,
            "fund_diags": fund_diags,
            "fund_codes": fund_codes,
            "updates_summary": updates_summary,
            "today_str": today_str,
        }

        # 10. 生成报告
        report_md = self.generate_report_content(report_data)
        with open(self.report_path, 'w', encoding='utf-8') as f:
            f.write(report_md)

        print(f"[+] 报告已输出至: {self.report_path}")
        print("="*60)
        return market_stage

    def generate_report_content(self, data):
        """基于实时数据生成报告。LLM可用时交由LLM润色，否则使用本地模板。"""
        stage = data["market_stage"]
        score = data["total_score"]
        scale = data["scale"]
        mm = data["market_metrics"]
        etf = data["etf_data"]
        flows = data["etf_flow_data"]
        fund_codes = data["fund_codes"]
        fd = data["fund_data"]
        diags = data["fund_diags"]
        updates = data["updates_summary"]
        source_md = build_sources_markdown(
            self.config,
            fund_codes=fund_codes,
            market_symbols=list(etf.keys()) + ["usNVDA", "usTSM", "usMSFT", "us.IXIC"],
        )
        today_str = beijing_now().strftime('%Y-%m-%d %H:%M:%S')

        # 行动建议
        action_map = {
            "AI主升浪": ("持有 + 正常定投", "避免高位追涨，锁仓享受趋势上行"),
            "高位震荡": ("持有 + 减压定投", "暂停大额加仓，保留现金，静待方向选择"),
            "回调期": ("加仓 + 放大定投", "均线附近分批低吸，逐步吸纳核心筹码"),
            "趋势破坏": ("减仓/持有 + 暂停定投", "停止科技定投，防守盘避险，观察均线企稳"),
        }
        action_desc, action_detail = action_map.get(stage, ("观望", ""))

        # 全球科技指标表
        us_rows = ""
        for sym in ["usNVDA", "usTSM", "usMSFT", "us.IXIC"]:
            m = mm.get(sym, {})
            us_rows += (
                f"| **{m.get('name', sym)}** | {m.get('price', 0):.2f} | "
                f"{m.get('high_52w', 0):.2f} | **{m.get('drawdown', 0)*100:+.2f}%** | "
                f"{m.get('change_pct', 0):+.2f}% |\n"
            )

        # ETF代理表
        etf_rows = ""
        for sym, e in etf.items():
            dd = e.get("drawdown", 0)
            dd_val = dd * 100 if isinstance(dd, (int, float)) else 0
            etf_rows += (
                f"| **{e.get('name', sym)}** | {e.get('price', 0):.3f} | **{dd_val:+.2f}%** | "
                f"{e.get('volume_ratio', 1):.2f}x | "
                f"{'MA20上' if e.get('above_ma20') else 'MA20下'} | "
                f"{'MA60上' if e.get('above_ma60') else 'MA60下'} |\n"
            )

        # 资金流向
        flow_md = ""
        if flows:
            flow_md = "\n### 主力资金动向\n\n| ETF | 日期 | 主力净流入(万) | 主力占比 |\n| :--- | :---: | :---: | :---: |\n"
            for sym, flist in flows.items():
                if flist:
                    f = flist[-1]
                    flow_md += f"| {sym} | {f['date']} | {f['main_net']/10000:+.2f}万 | {f['main_pct']*100:+.2f}% |\n"

        # 持仓基金诊断
        fund_rows = ""
        for code in fund_codes:
            f = fd.get(code, {})
            d = diags.get(code, {})
            r = f.get("recent", {})
            tag = self.config[code].get('tag', '')

            fund_rows += f"#### {f.get('name', code)} ({code}) `{tag}`\n"
            fund_rows += f"- **实时信号**: **{d.get('diag', '[巡航中]')}**\n"
            fund_rows += f"- 持有收益率: {d.get('h_yield', 0)*100:+.2f}% | 成本: {self.config[code]['cost']:.4f} | 净值: {f.get('nav', 0):.4f}\n"
            fund_rows += f"- 均线: MA20={f.get('ma20', 0):.4f}({'上' if f.get('above_ma20') else '下'}) | MA60={f.get('ma60', 0):.4f}({'上' if f.get('above_ma60') else '下'})\n"
            fund_rows += f"- 近期收益: 5日 {r.get('r5d', 0)*100:+.2f}% | 10日 {r.get('r10d', 0)*100:+.2f}% | 20日 {r.get('r20d', 0)*100:+.2f}%\n"
            fund_rows += f"- 定投: {self.config[code].get('daily_invest', 0)}元/日 | 补仓倍数: {self.config[code].get('multiplier', 1.0)}x\n"
            mf = d.get('main_flow')
            if mf is not None:
                fund_rows += f"- 主力资金: {mf:+.2f}万元\n"
            fund_rows += "\n"

        # 参数变更
        changes_md = "\n".join(f"- {u}" for u in updates) if updates else "- 本次无需调整"

        summary_label = f"趋势{data['trend_score']} + 回撤{data['dd_score']} + 量能{data['vol_score']} + 资金{data['flow_score']}"

        local_report = f"""# AI科技主线基金投资智能体决策报告

**诊断时间**: `{today_str}` | **市场阶段**: **【{stage}】** | **健康得分**: `{score}/110`
**策略系数**: `{scale:.1f}x` | **建议**: {action_desc}

{action_detail}

---

## 全球科技核心指标

| 资产 | 价格 | 52周高 | 高位回撤 | 日涨跌 |
| :--- | :---: | :---: | :---: | :---: |
{us_rows}

## 国内ETF代理

| ETF | 价格 | 52周回撤 | 量比 | MA20 | MA60 |
| :--- | :---: | :---: | :---: | :---: | :---: |
{etf_rows}

*量比 0.8~1.2x=温和, >1.5x=放量, <0.6x=缩量*

{flow_md}
---

## 持仓基金诊断

{fund_rows}
---

## 策略参数变更

{changes_md}

---

## 评分明细 ({summary_label})

| 维度 | 得分 | 满分 | 说明 |
| :--- | :---: | :---: | :--- |
| 趋势 | {data['trend_score']} | 60 | ETF代理 x MA20/MA60 |
| 回撤 | {data['dd_score']} | 30 | 纳指+NVDA高位回撤 |
| 量能 | {data['vol_score']} | 10 | 科创芯片ETF量比 |
| 资金流向 | {data['flow_score']} | 10 | 主力净流入占比 |
| **总分** | **{score}** | **110** | |

阶段阈值: >=82 AI主升浪 | >=60 高位震荡 | >=33 回调期 | <33 趋势破坏

---
*本报告由本地量化引擎驱动。参数仅由本地引擎调整，LLM 提供分析解读。*

{source_md}
"""

        # LLM润色
        agent_settings = self.config.get('ai_agent_settings', {})
        if agent_settings.get('enable_llm') and agent_settings.get('api_key'):
            try:
                llm_report = self.call_llm_cognitive_agent(local_report, data)
                if llm_report:
                    return f"{llm_report.rstrip()}\n\n{source_md}\n"
            except Exception as e:
                print(f"[-] LLM调用失败，使用本地报告。错误: {e}")

        return local_report

    def call_llm_cognitive_agent(self, local_report, data):
        """调用LLM生成分析报告并提取优化决策参数，写入fund_config.json"""
        settings = self.config['ai_agent_settings']
        provider = settings.get('llm_provider', 'gemini').lower()
        api_key = settings.get('api_key')
        base_url = settings.get('base_url')
        model = settings.get('model', '')

        if provider == 'deepseek':
            if not base_url:
                base_url = "https://api.deepseek.com"
            if not model or model == 'gemini-1.5-flash':
                model = "deepseek-chat"
        stage = data['market_stage']
        score = data['total_score']

        # 构建基金参数快照供LLM参考
        fund_params_snapshot = ""
        for code in data['fund_codes']:
            cfg = self.config[code]
            d = data['fund_diags'].get(code, {})
            fund_params_snapshot += (
                f"- {cfg['name']} ({code}) tag={cfg.get('tag','')}: "
                f"定投={cfg.get('daily_invest', 0)}元, 倍数={cfg.get('multiplier', 1.0)}x, "
                f"收益率={d.get('h_yield', 0)*100:+.2f}%, 信号={d.get('diag', '巡航中')}\n"
            )

        prompt = f"""你是一个专注于AI科技主线的基金投资智能体大模型。

以下是本地量化引擎生成的完整诊断报告（包含实时美股/A股ETF数据、持仓基金诊断、交易信号、评分明细）：

{local_report}

当前市场阶段: {stage}，健康得分: {score}/110。
各基金当前决策参数与交易信号:
{fund_params_snapshot}

本报告允许引用的可访问来源清单:
{build_sources_markdown(self.config, fund_codes=data['fund_codes'], market_symbols=list(data.get('etf_data', {}).keys()) + ['usNVDA', 'usTSM', 'usMSFT', 'us.IXIC'])}

请对这份报告进行专业润色和深度解读。要求：
1. 保持markdown格式，不要删除或修改原有的数据表格和数字。
2. 重点解读每只基金的"实时信号"含义，结合市场阶段解释为什么会有这个信号。
3. 结合当前实际数据（NVDA价格/回撤、纳斯达克点位、ETF量比、资金流向等）分析市场，避免空洞的模板化叙述。
4. 遵循核心原则：不频繁交易、分批建仓、避免情绪化追高、防守底仓稳固。
5. 语言专业、冷静，用数据说话。
6. 涉及外部行情、基金净值或资金流向时，必须在相关段落或报告末尾标注来源名称和可点击链接；不要编造来源链接。

## 决策输出要求
在报告的末尾，你必须附加一个JSON代码块，为每只进攻型基金（tag为"科创"或"海外"）推荐最优的定投金额(daily_invest)和补仓倍数(multiplier)。格式如下：
```json
{{
  "fund_code": {{"daily_invest": 金额(整数), "multiplier": 倍数(浮点)}},
  ...
}}
```
注意：
- daily_invest的单位为元，必须为整数
- multiplier为补仓放大倍数，范围0.5~2.0
- 结合当前市场阶段和基金回撤深度，给出差异化数值
- 防守型基金（黄金/红利）不需要包含在JSON中"""

        headers = {"Content-Type": "application/json"}
        llm_text = ""

        if provider == 'gemini':
            url = build_gemini_url(base_url, model, api_key)
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3}
            }
            res = requests.post(url, json=payload, headers=headers, timeout=60)
            if res.status_code == 200:
                result = res.json()
                llm_text = result['candidates'][0]['content']['parts'][0]['text']
            else:
                raise Exception(format_api_error(res))

        else:
            url = build_chat_completions_url(provider, base_url)
            headers["Authorization"] = f"Bearer {api_key}"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是一个专业的AI基金投资智能体。生成Markdown分析报告，并在末尾以JSON代码块输出进攻型基金的定投参数建议。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3
            }
            res = requests.post(url, json=payload, headers=headers, timeout=60)
            if res.status_code == 200:
                result = res.json()
                llm_text = result['choices'][0]['message']['content']
            else:
                raise Exception(format_api_error(res))

        if not llm_text:
            raise Exception(f"API returned status {res.status_code}")

        # 提取LLM的JSON决策参数并覆盖写入fund_config.json
        self.extract_and_apply_llm_decisions(llm_text)

        # 清理报告中残留的JSON块，返回纯净报告
        return self.strip_json_block(llm_text)

    def extract_and_apply_llm_decisions(self, llm_text):
        """从LLM响应中提取JSON决策块，解析并覆盖写入fund_config.json

        仅对tag为"科创"或"海外"的进攻型基金生效。
        daily_invest上限受基金cap限制，multiplier限制在0.5~2.0范围以防LLM幻觉。
        """
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', llm_text)
        if not json_match:
            print("[-] 未在LLM响应中找到JSON决策块，保留本地引擎参数")
            return False

        try:
            decisions = json.loads(json_match.group(1))
        except json.JSONDecodeError as e:
            print(f"[-] LLM JSON决策解析失败: {e}")
            return False

        applied = []
        for code, params in decisions.items():
            if code not in self.config:
                print(f"[-] 基金代码 {code} 不在配置中，跳过LLM决策")
                continue
            if not isinstance(params, dict):
                print(f"[-] 基金代码 {code} 的LLM决策格式无效，跳过")
                continue

            cfg = self.config[code]
            tag = cfg.get('tag', '')

            if tag not in ('科创', '海外'):
                continue

            try:
                daily_invest = int(params.get('daily_invest', cfg.get('daily_invest', 0)))
                multiplier = float(params.get('multiplier', cfg.get('multiplier', 1.0)))
            except (TypeError, ValueError):
                print(f"[-] 基金代码 {code} 的LLM决策数值无效，跳过")
                continue

            # 安全兜底：daily_invest不超过cap，multiplier限制在0.5~2.0
            cap = cfg.get('cap', 500)
            daily_invest = max(0, min(daily_invest, cap))
            multiplier = max(0.5, min(round(multiplier, 2), 2.0))

            old_invest = cfg.get('daily_invest', 0)
            old_mult = cfg.get('multiplier', 1.0)

            cfg['daily_invest'] = daily_invest
            cfg['multiplier'] = multiplier

            applied.append(f"{cfg['name']} ({code}): 定投 {old_invest}->{daily_invest}元, 倍数 {old_mult}->{multiplier}x")

        if applied:
            self.save_config()
            print("[+] LLM决策参数已覆盖写入 fund_config.json:")
            for s in applied:
                print(f"    - {s}")
        else:
            print("[-] LLM JSON块中无有效进攻型基金决策，保留本地引擎参数")

        return len(applied) > 0

    def strip_json_block(self, text):
        """移除LLM输出中的JSON代码块，保留纯净报告文本"""
        cleaned = re.sub(r'```json\s*\{[\s\S]*?\}\s*```', '', text)
        cleaned = re.sub(r'```\s*\{[\s\S]*?"daily_invest"[\s\S]*?\}\s*```', '', cleaned)
        return cleaned.strip()

    def start_chat_session(self):
        """Start an interactive chat session with the AI Agent in the console"""
        settings = self.config.get('ai_agent_settings', {})
        if not settings.get('enable_llm') or not settings.get('api_key'):
            print("[-] 错误：请先在 fund_config.json 中配置 API Key 并开启 enable_llm。")
            return
            
        print("\n" + "="*60)
        print("[*] 💬 已成功开启 AI 投资智能体对话模式...")
        print("[*] 您可以针对当前市场、科技板块走势、或者对智能体生成的诊断意见进行提问。")
        print("[*] 输入 'exit' 或 'quit' 可随时退出对话。")
        print("="*60)
        
        # 尝试加载最新生成的报告作为上下文
        report_content = ""
        if os.path.exists(self.report_path):
            try:
                with open(self.report_path, 'r', encoding='utf-8') as f:
                    report_content = f.read()
            except:
                pass
                
        # 初始化对话历史，注入上下文
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个专注于AI科技主线的基金投资智能体大模型。下面是目前最新的宏观数据及投资诊断报告上下文：\n\n"
                    f"{report_content}\n\n"
                    "请围绕上述报告内容和数据，结合AI科技主线（如Blackwell出货、CoWoS先进封装、HBM4、玻璃基板、全球AI算力Capex军备竞赛等），"
                    "专业、理性、客观地解答用户的疑问。遵循如下核心原则：不频繁交易、分批建仓、避免情绪化追高、防守底仓稳固。用中文回答。"
                )
            }
        ]
        
        provider = settings.get('llm_provider', 'gemini').lower()
        api_key = settings.get('api_key')
        base_url = settings.get('base_url')
        model = settings.get('model', '')
        
        if provider == 'deepseek':
            if not base_url:
                base_url = "https://api.deepseek.com"
            if not model or model == 'gemini-1.5-flash':
                model = "deepseek-chat"
        while True:
            try:
                user_input = input("\n👤 您: ")
                if user_input.strip().lower() in ['exit', 'quit']:
                    print("[*] 对话已结束。投资有风险，决策需谨慎，祝您收获满满！")
                    break
                if not user_input.strip():
                    continue
                
                print("🤖 智能体正在思考...", end="\r", flush=True)
                messages.append({"role": "user", "content": user_input})
                
                headers = {"Content-Type": "application/json"}
                if provider == 'gemini':
                    url = build_gemini_url(base_url, model, api_key)
                    gemini_contents = []
                    for msg in messages:
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
                        "model": model,
                        "messages": messages,
                        "temperature": 0.4
                    }
                    res = requests.post(url, json=payload, headers=headers, timeout=40)
                    if res.status_code == 200:
                        ans = res.json()['choices'][0]['message']['content']
                    else:
                        ans = format_api_error(res)
                
                # 清除思考中的提示字
                print(" " * 30, end="\r", flush=True)
                print(f"🤖 智能体: {ans}")
                messages.append({"role": "assistant", "content": ans})
                
            except KeyboardInterrupt:
                print("\n[*] 对话已结束。")
                break
            except Exception as e:
                print(f"\n[-] 出现异常: {e}")

if __name__ == "__main__":
    import sys
    agent = AIFundAgent()
    if len(sys.argv) > 1 and sys.argv[1] in ['--chat', '-c']:
        agent.start_chat_session()
    else:
        agent.run_analysis()
