# -*- coding: utf-8 -*-
import sys, time, datetime, json, os, requests, urllib3, unicodedata, re, traceback, io

from storage import initialize_data_files, save_json
from time_utils import beijing_now

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

# ==========================================
# 核心设置中心
# ==========================================
REFRESH_INTERVAL = 60  
TOTAL_LINE_WIDTH = 112 

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ⚠️ 注意：STRATEGY_MAP 已被彻底移除！
# 所有策略参数现已完全解耦，转移至 fund_config.json 进行集中管理。

def strip_ansi(text): return re.sub(r'\033\[[0-9;]*m', '', text)

def align_str(text, width, align='center'):
    text = str(text).replace('\ufe0f', '').replace('\u200b', '')
    raw_text = strip_ansi(text)
    cn_count = len([c for c in raw_text if unicodedata.east_asian_width(c) in ('F', 'W', 'A')])
    pad_len = max(0, width - (len(raw_text) + cn_count))
    if align == 'left': return text + ' ' * pad_len
    elif align == 'right': return ' ' * pad_len + text
    else:
        l = pad_len // 2; r = pad_len - l
        return ' ' * l + text + ' ' * r

def format_color(val, width, is_bold=False):
    if val is None: return align_str("--", width)
    raw = "{:+.2f}%".format(val * 100); color = "\033[31m" if val > 0 else "\033[32m" 
    if abs(val) < 0.00005: color, raw = "", "0.00%"
    bold = "\033[1m" if is_bold else ""
    return align_str(f"{bold}{color}{raw}\033[0m", width)

class UltimateTitanRadarV29:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'http://fundf10.eastmoney.com/'})
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.getenv("FUND_DATA_DIR", self.base_dir)
        initialize_data_files(self.data_dir, self.base_dir)
        self.config_path = os.path.join(self.data_dir, "fund_config.json")
        
        with open(self.config_path, 'r', encoding='utf-8-sig') as f:
            self.fund_config = json.load(f)
            
        cfg_changed = False
        for code, cfg in self.fund_config.items():
            if (code.isdigit() or code.startswith('QD')) and 'last_replenish_price' not in cfg:
                cfg['last_replenish_price'] = cfg['cost']
                cfg_changed = True
        if cfg_changed: self._save_config()

        self.cost_cache_file = os.path.join(self.data_dir, "cost_cache.json")
        self._validate_and_update_costs()
        
        self.nav_cache, self.nav_date_cache = {}, {}
        self.last_stock_prices, self.trend_matrix = {}, {}
        self.holdings_cache = {}
        
        self.cache_file = os.path.join(self.data_dir, "holdings_cache.json")
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8-sig') as f: self.holdings_cache = json.load(f)
            except: pass
        self.pred_file = os.path.join(self.data_dir, "prediction_log.json")
        self.pred_cache = {}
        if os.path.exists(self.pred_file):
            try:
                with open(self.pred_file, 'r', encoding='utf-8-sig') as f: self.pred_cache = json.load(f)
            except: pass

    def _save_config(self):
        try:
            save_json(self.config_path, self.fund_config)
        except: pass

    def _validate_and_update_costs(self):
        old_costs = {}
        if os.path.exists(self.cost_cache_file):
            try:
                with open(self.cost_cache_file, 'r', encoding='utf-8-sig') as f: old_costs = json.load(f)
            except: pass
        costs_updated = False
        for code, cfg in self.fund_config.items():
            if not (code.isdigit() or code.startswith('QD')): continue
            if code in old_costs and old_costs[code] > 0:
                if abs(cfg['cost'] - old_costs[code]) / old_costs[code] > 0.08:
                    print(f"\n🚨 [安全拦截] 基金 {code} 成本波动异常！"); time.sleep(5); sys.exit(1)
            old_costs[code] = cfg['cost']
            costs_updated = True 
        if costs_updated:
            save_json(self.cost_cache_file, old_costs)

    def _init_trend_matrix(self):
        print("\n[*] ⚙️ 同步 MA5/10/20/60 趋势分化引擎 (全域均线就绪)...")
        fund_codes = [k for k in self.fund_config.keys() if (k.isdigit() or k.startswith('QD'))]
        updated_matrix = {}
        for code in fund_codes:
            try:
                url = f"http://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=60"
                data = self.session.get(url, timeout=5, verify=False).json()
                if data.get('Data', {}).get('LSJZList'):
                    ls = data['Data']['LSJZList']
                    navs = [float(x.get('DWJZ') or x.get('LJJZ')) for x in ls if x.get('DWJZ') or x.get('LJJZ')]
                    if len(navs) >= 5:
                        updated_matrix[code] = {
                            "MA5": round(sum(navs[:min(5,len(navs))])/min(5,len(navs)), 4),
                            "MA10": round(sum(navs[:min(10,len(navs))])/min(10,len(navs)), 4),
                            "MA20": round(sum(navs[:min(20,len(navs))])/min(20,len(navs)), 4),
                            "MA60": round(sum(navs[:min(60,len(navs))])/min(60,len(navs)), 4)
                        }
            except: pass
        self.trend_matrix = updated_matrix

    def sync_all_bases(self):
        print("[*] 正在同步底层净值并执行拟合回测...\n", flush=True)
        # 启动 AI 智能体进行宏观大盘诊断与策略参数自适应调节
        try:
            from ai_agent import AIFundAgent
            agent = AIFundAgent()
            agent.run_analysis()
            # 重新加载最新的配置文件
            with open(self.config_path, 'r', encoding='utf-8-sig') as f:
                self.fund_config = json.load(f)
        except Exception as e:
            print(f"[-] AI 智能体评估失败: {e}")
        today_str = beijing_now().strftime('%Y-%m-%d')
        for code in self.fund_config.keys():
            if not (code.isdigit() or code.startswith('QD')): continue
            try:
                url = f"http://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=1"
                res = self.session.get(url, timeout=5, verify=False).json()
                if res.get('Data') and res['Data'].get('LSJZList'):
                    self.nav_cache[code] = float(res['Data']['LSJZList'][0]['DWJZ'])
                    self.nav_date_cache[code] = res['Data']['LSJZList'][0]['FSRQ']
            except: pass
            if code not in self.holdings_cache: self.holdings_cache[code] = self._fetch_holdings_api(code)
        
        if self.pred_cache:
            valid_backtests = []
            for code, name in [(k, v['name']) for k, v in self.fund_config.items() if (k.isdigit() or k.startswith('QD'))]:
                if code in self.pred_cache and code in self.nav_cache:
                    pred_date = self.pred_cache[code].get('date', '')
                    actual_date = self.nav_date_cache.get(code, '')
                    # 严谨的时间锁：只有预测日期和官方公布日期完全一致，才证明是真实的回测
                    if pred_date == actual_date and pred_date != '':
                        valid_backtests.append((name, self.pred_cache[code]['est_nav'], self.nav_cache[code]))
            
            if valid_backtests:
                print("[*] --- 算法拟合度真实回测 ---")
                for name, pred, real in valid_backtests:
                    print(f"    - {name}: 实际{real:.4f} | 预测{pred:.4f} | 误差{(pred-real)/real*100:+.2f}%")
        self._init_trend_matrix()

    def _fetch_holdings_api(self, fund_code):
        try:
            mapping = {'017470': '017469', '015795': '015794', '019875': '019874', '017992': '017991', '020274': '020273'}
            qc = mapping.get(fund_code, fund_code)
            url = f"https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition?FCODE={qc}&deviceid=Wap&version=2.0.0"
            res = requests.get(url, timeout=4).json()
            stocks = res.get('Data', {}).get('fundStocks', [])
            return [('hk'+s['GPDM'] if len(s['GPDM'])==5 else ('sh'+s['GPDM'] if s['GPDM'].startswith(('6','5')) else 'sz'+s['GPDM']), float(s['JZBL'])/100) for s in stocks]
        except: return []

    def fetch_market_prices(self, code_list):
        if not code_list: return
        try:
            res = requests.get(f"http://qt.gtimg.cn/q={','.join(code_list)}", timeout=5).text
            for line in res.split('\n'):
                if '="' in line:
                    d = line.split('"')[1].split('~')
                    if len(d) > 5:
                        key = next((c for c in code_list if c.endswith(line.split('=')[0].split('_')[-1])), "")
                        if key and float(d[4]) > 0: self.last_stock_prices[key] = (float(d[3]) - float(d[4])) / float(d[4])
        except: pass

    def run_scan(self):
        now = beijing_now()
        cur_t, today_str = now.time(), now.strftime('%Y-%m-%d')
        is_weekend = now.weekday() >= 5
        holidays = self.fund_config.get('global_holidays', [])
        
        # 每天 14:45 - 15:00 之间触发收盘决策前智能体评估，更新配置参数
        if not is_weekend and today_str not in holidays:
            agent_settings = self.fund_config.get('ai_agent_settings', {})
            if agent_settings.get('last_analysis_date') != today_str and datetime.time(14, 45) <= cur_t <= datetime.time(15, 0):
                print("\n[*] [系统通知] 到达盘尾交易决策时刻，触发 AI 智能体收盘诊断...")
                try:
                    from ai_agent import AIFundAgent
                    agent = AIFundAgent()
                    agent.run_analysis()
                    with open(self.config_path, 'r', encoding='utf-8-sig') as f:
                        self.fund_config = json.load(f)
                except Exception as e:
                    print(f"[-] AI 智能体收盘诊断失败: {e}")
        
        if is_weekend or (today_str in holidays):
            status_text, icon, is_m_closed = "节假休市", "💤", True
        elif cur_t < datetime.time(9, 30):
            status_text, icon, is_m_closed = "夜间休市", "💤", True
        elif datetime.time(9, 30) <= cur_t < datetime.time(11, 30):
            status_text, icon, is_m_closed = "正在开盘", "💰", False
        elif datetime.time(11, 30) <= cur_t < datetime.time(13, 0):
            status_text, icon, is_m_closed = "午间休市", "☕", True
        elif datetime.time(13, 0) <= cur_t < datetime.time(14, 50):
            status_text, icon, is_m_closed = "正在开盘", "💰", False
        elif datetime.time(14, 50) <= cur_t < datetime.time(15, 0):
            status_text, icon, is_m_closed = "决战收盘", "🔥", False
        else:
            status_text, icon, is_m_closed = "夜间休市", "💤", True

        is_save_time = datetime.time(14, 50) <= cur_t <= datetime.time(15, 0)
        is_trading_day = not is_weekend and today_str not in holidays
        after_close_today = is_trading_day and cur_t >= datetime.time(15, 0)
        
        all_codes = set()
        sorted_keys = [k for k in self.fund_config.keys() if (k.isdigit() or k.startswith('QD'))]
        for code in sorted_keys:
            cfg = self.fund_config[code]
            if cfg.get('proxy'): all_codes.add(cfg['proxy'])
            for sc, _ in self.holdings_cache.get(code, []): all_codes.add(sc)
        self.fetch_market_prices(list(all_codes))
        
        print("\n\n+" + "-" * TOTAL_LINE_WIDTH + "+") 
        print("|" + align_str(f"DATE: {now.strftime('%H:%M:%S')} | {icon} {status_text} ", TOTAL_LINE_WIDTH) + "|")
        print("+" + "-" * TOTAL_LINE_WIDTH + "+")
        print("|" + align_str('资产名称', 24) + "|" + align_str('归一动能', 10) + "|" + align_str('官方估算', 10) + "|" + align_str('严谨终值', 10) + "|" + align_str('实时持有收益', 14) + "|" + align_str('止盈目标', 12) + "|" + align_str('诊断建议', 26) + "|")
        print("+" + "-" * TOTAL_LINE_WIDTH + "+")
        
        snapshot, changed, auto_saved = {}, False, False
        for code in sorted_keys:
            cfg = self.fund_config[code]
            
            # ==========================================
            # 数据解耦引擎：完全智能读取 JSON 内的策略参数
            # (带有托底保护机制，即使 JSON 忘写某项也不会报错)
            # ==========================================
            strat = {
                'tag': cfg.get('tag', '默认'),
                'drop': cfg.get('drop', -0.015),
                'gap': cfg.get('gap', 0.02),
                'cap': cfg.get('cap', 200),
                'tp': cfg.get('tp', 0.10),
                'ratio': cfg.get('ratio', 0.50),
                'tp_ma': cfg.get('tp_ma', 20)
            }
            
            h = self.holdings_cache.get(code, [])
            e_contrib, sw_sum = 0.0, 0.0
            for sc, wt in h:
                if sc in self.last_stock_prices: e_contrib += self.last_stock_prices[sc] * wt; sw_sum += wt
            fb = cfg.get('proxy')
            v_pure = e_contrib + (self.last_stock_prices[fb] * max(0.0, 1.0 - sw_sum)) if fb and fb in self.last_stock_prices else (e_contrib if sw_sum > 0 else None)

            try:
                r = requests.get(f"http://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time())}", timeout=2).text
                v_off = float(json.loads(r[r.find('{'):r.rfind('}')+1])['gszzl']) / 100
            except: v_off = None
            
            v_hyb = v_pure if v_pure is not None else (v_off if v_off is not None else 0.0)
            base = self.nav_cache.get(code, cfg['cost'])
            
            is_today_updated = (self.nav_date_cache.get(code) == today_str)

            # 净值已公布→实际值；盘中/收盘未出→叠加估算；非交易日/开盘前→不叠加(避免重复)
            if is_today_updated:
                est_nav = base
            elif not is_m_closed or after_close_today:
                est_nav = base * (1 + v_hyb)
            else:
                est_nav = base

            h_yield = (est_nav - cfg['cost']) / cfg['cost']
            
            ma = self.trend_matrix.get(code, {"MA5":0, "MA10":0, "MA20":0, "MA60":0})
            last_p = cfg.get('last_replenish_price', cfg.get('cost', est_nav))
            target_ma_val = ma.get(f"MA{strat['tp_ma']}", 0)
            
            is_silenced = False
            days_passed = 0
            if 'last_sell_date' in cfg:
                try:
                    last_sell_date = datetime.datetime.strptime(cfg['last_sell_date'], '%Y-%m-%d').date()
                    days_passed = (now.date() - last_sell_date).days
                    if days_passed >= 10 or h_yield < (strat['tp'] - 0.05):
                        cfg.pop('last_sell_date')
                        changed = True
                    else:
                        is_silenced = True
                except: pass

            diag = "[巡航中]"
            if is_m_closed:
                diag = f"[{status_text[2:]}]"
            else:
                if h_yield >= strat['tp']:
                    if is_silenced:
                        diag = f"[✅ 止盈静默, 剩余{10 - days_passed}天]"
                    else:
                        if target_ma_val > 0 and est_nav > target_ma_val:
                            diag = f"🔥 极强(MA{strat['tp_ma']}护航中)"
                        elif target_ma_val > 0 and est_nav <= target_ma_val:
                            if strat['tag'] == '救援':
                                diag = f"🚨 救援结束! 建议清仓"
                            else:
                                diag = f"🚨 破MA{strat['tp_ma']}! 建议卖{strat['ratio']*100:.0f}%"
                        else:
                            diag = f"⭐ 达标! 等待均线确认"
                
                elif v_hyb <= strat['drop']:
                    p_gap = (est_nav - last_p) / last_p
                    if p_gap > -strat['gap']: diag = f"⏳ 空间锁拦截({p_gap:+.1%})"
                    elif strat['tag'] == '周期' and ma.get('MA60',0) > 0 and est_nav < ma['MA60']: diag = "⚠️ 破位! 观望为上"
                    elif strat['tag'] == '限购': diag = "🚫 限购暂停"
                    else:
                        val = cfg.get('shares', 0) * est_nav
                        bonus = 1.0 + (abs(min(0, h_yield)) // 0.05) * 0.3
                        buy_amt = int(val * abs(v_hyb) * cfg.get('multiplier', 1.5) * bonus) if val > 0 else 500
                        diag = f"💰 狙击补仓 +{min(max(50, buy_amt), strat['cap'])}元"
                elif h_yield < -0.12: diag = "💀 深度被套(等信号)"

            print("|" + align_str(cfg['name'], 24) + "|" + format_color(v_pure, 10) + "|" + format_color(v_off, 10) + "|" + format_color(v_hyb, 10, True) + "|" + format_color(h_yield, 14) + "|" + align_str(f"{strat['tp']*100:.2f}%", 12) + "|" + align_str(diag, 26) + "|")
            
            if is_save_time and cfg.get('daily_invest', 0) > 0 and self.fund_config.get("last_auto_save_date") != today_str:
                old_shares = cfg.get('shares', 0.0)
                old_cost = cfg.get('cost', 0.0)
                invest_amt = cfg['daily_invest']
                new_shares = old_shares + (invest_amt / est_nav)
                if new_shares > 0:
                    new_cost = (old_cost * old_shares + invest_amt) / new_shares
                else:
                    new_cost = old_cost
                cfg['shares'] = round(new_shares, 2)
                cfg['cost'] = round(new_cost, 4)
                changed = True
                auto_saved = True
            snapshot[code] = {'est_nav': est_nav, 'date': today_str}

        if changed:
            if auto_saved:
                self.fund_config["last_auto_save_date"] = today_str
            self._save_config()
            notice = "定投份额与状态已自动更新入账" if auto_saved else "状态已自动更新"
            print(f"\n💾 [系统通知] {notice}。")
            
        print("+" + "-" * TOTAL_LINE_WIDTH + "+")
        if snapshot:
            try:
                save_json(self.pred_file, snapshot)
            except: pass
            
        return 10 if is_save_time else REFRESH_INTERVAL

if __name__ == "__main__":
    try:
        radar = UltimateTitanRadarV29()
        radar.sync_all_bases()
        while True:
            sleep_time = radar.run_scan()
            time.sleep(sleep_time)
    except Exception as e:
        import traceback
        traceback.print_exc()
        input("\n程序发生严重错误，按回车键退出...")
