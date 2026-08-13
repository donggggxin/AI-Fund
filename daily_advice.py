"""面向投资新手的可解释每日行动建议（纯规则、无自动交易）。"""

import datetime
import re


ACTION_ORDER = {"减仓": 0, "分批买入": 1, "暂停": 2, "观察": 3, "持有": 4, "复盘": 5}


def _pct(value):
    return f"{float(value or 0):+.2%}"


def _fund_advice(row, cfg, is_market_closed):
    diag = str(row.get("diag", "[巡航中]"))
    day_move = float(row.get("v_hyb", 0) or 0)
    holding_return = float(row.get("h_yield", 0) or 0)
    drop = float(cfg.get("drop", -0.015))
    gap = float(cfg.get("gap", 0.02))
    target = float(cfg.get("tp", row.get("tp", 0.10)))
    item = {
        "code": row.get("code"),
        "name": row.get("name", row.get("code")),
        "action": "持有",
        "action_detail": "按原计划持有，今天不因短期波动临时改变策略。",
        "reasons": [
            f"今日估算涨跌 {_pct(day_move)}，策略下跌触发线为 {_pct(drop)}。",
            f"当前持有收益 {_pct(holding_return)}，止盈目标为 {_pct(target)}。",
        ],
        "principle": "没有触发预先设定的条件时少操作，可减少追涨杀跌和频繁交易。",
        "checklist": ["核对基金公司最新净值", "确认当日是否开放申购或赎回"],
        "source_diagnostic": diag,
    }

    if is_market_closed:
        item.update(
            action="复盘",
            action_detail="当前休市，不执行盘中建议；只检查持仓和下一交易日计划。",
            principle="非交易时段的估值可能不是当日可成交价格，等待有效数据比抢先操作更重要。",
        )
        return item

    if "建议清仓" in diag:
        item.update(
            action="减仓",
            action_detail="规则提示退出该笔救援仓位；执行前确认真实净值、费用和到账时间。",
            principle="救援仓的目标是控制风险并退出，不应在目标完成后变成长线重仓。",
        )
    elif "建议卖" in diag or diag.startswith("破MA"):
        match = re.search(r"卖(\d+)%", diag)
        ratio = int(match.group(1)) if match else round(float(cfg.get("ratio", 0.5)) * 100)
        ma_period = int(cfg.get("tp_ma", 20))
        ma_value = float((row.get("ma") or {}).get(f"MA{ma_period}", 0) or 0)
        item.update(
            action="减仓",
            action_detail=f"规则建议分批减仓 {ratio}%，不要一次性凭情绪处理全部仓位。",
            reasons=[
                f"持有收益 {_pct(holding_return)} 已达到止盈目标 {_pct(target)}。",
                f"估算净值 {float(row.get('est_nav', 0)):.4f} 已不高于 MA{ma_period} {ma_value:.4f}。",
            ],
            principle="盈利达标后又跌破趋势线，分批兑现可以在保留后续上涨机会的同时降低回撤。",
        )
    elif diag.startswith("狙击补仓"):
        match = re.search(r"\+(\d+(?:\.\d+)?)元", diag)
        amount = float(match.group(1)) if match else float(cfg.get("cap", 200))
        item.update(
            action="分批买入",
            action_detail=f"规则触发小额分批买入，参考上限 ¥{amount:,.0f}；不是必须成交的指令。",
            reasons=[
                f"今日估算涨跌 {_pct(day_move)} 已达到下跌触发线 {_pct(drop)}。",
                f"价格相对上次补仓已满足至少 {_pct(-gap)} 的间隔要求。",
            ],
            principle="分批买入把资金拆开，只在预设跌幅出现时使用，可避免一次押注和无计划抄底。",
            checklist=["确认这笔钱至少数年内不用", "确认未超过单次上限", "核对基金是否限购"],
        )
    elif "空间锁" in diag:
        item.update(
            action="观察",
            action_detail="今天不加仓，等待价格与上次买入拉开足够距离。",
            principle="连续小跌时反复补仓会过快用完现金；价格间隔锁用于保留后续弹药。",
        )
    elif "破位" in diag:
        item.update(
            action="暂停",
            action_detail="暂停新增仓位，等待长期趋势重新稳定。",
            principle="周期资产跌破长期趋势时，便宜可能继续变得更便宜，先控制仓位比猜底更重要。",
        )
    elif "限购" in diag:
        item.update(
            action="暂停",
            action_detail="基金处于限购策略，今天不新增买入。",
            principle="遵守产品交易限制，避免订单失败或实际金额偏离计划。",
        )
    elif "深度被套" in diag:
        item.update(
            action="观察",
            action_detail="不要因为亏损扩大就冲动补仓，等待既定下跌信号。",
            principle="亏损本身不是买入理由；只有估值、趋势和资金计划共同满足条件才行动。",
        )
    elif "止盈静默" in diag:
        item.update(
            action="持有",
            action_detail="近期已执行止盈，静默期内不重复卖出。",
            principle="设置冷静期可以避免同一个信号被重复执行，导致仓位下降过快。",
        )
    elif "极强" in diag:
        item.update(
            action="持有",
            action_detail="收益虽已达标，但趋势仍强；继续观察均线，不追涨加仓。",
            principle="用移动平均线跟随趋势，可以让盈利继续奔跑，同时保留明确的退出条件。",
        )
    elif "达标" in diag:
        item.update(
            action="观察",
            action_detail="止盈目标已达到，但均线数据尚未给出确认，暂不操作。",
            principle="价格目标与趋势确认配合，能减少仅因一次短期波动就交易。",
        )
    return item


def build_daily_advice(rows, config, market, now=None):
    """根据诊断结果生成结构化的新手行动指南。"""
    now = now or datetime.datetime.now()
    is_closed = bool(market.get("is_market_closed", False))
    stage = config.get("ai_agent_settings", {}).get("last_market_stage", "尚未判断")
    items = [
        _fund_advice(row, config.get(str(row.get("code")), {}), is_closed)
        for row in rows
    ]
    items.sort(key=lambda item: (ACTION_ORDER.get(item["action"], 99), item["code"] or ""))
    counts = {}
    for item in items:
        counts[item["action"]] = counts.get(item["action"], 0) + 1

    if is_closed:
        headline = "今天休市：只复盘，不根据盘中估值下单"
        plan = "检查持仓、可用现金和下一交易日条件单，等待开市后再确认。"
    elif counts.get("减仓"):
        headline = f"今天优先处理 {counts['减仓']} 个风险/止盈信号"
        plan = "先核对官方数据，再分批执行减仓；其他基金按各自规则等待。"
    elif counts.get("分批买入"):
        headline = f"今天有 {counts['分批买入']} 个小额分批买入信号"
        plan = "只使用长期闲置资金，并遵守单次金额上限，不因下跌临时加码。"
    else:
        headline = "今天没有必须执行的交易"
        plan = "继续持有或观察；没有信号也是一种明确的策略结果。"

    return {
        "date": now.date().isoformat(),
        "as_of": market.get("as_of", now.isoformat(timespec="seconds")),
        "market_status": market.get("status", "未知"),
        "market_stage": stage,
        "headline": headline,
        "today_plan": plan,
        "action_counts": counts,
        "items": items,
        "beginner_rules": [
            "先留好生活应急金，再考虑投资；不要借钱买基金。",
            "分批行动，不把全部资金押在同一天或单一基金。",
            "只执行事先设定的触发条件，不根据当天情绪追涨杀跌。",
        ],
        "disclaimer": "这是基于你自定义阈值和公开估算数据的教育性规则解释，不构成投资建议，也不保证收益。操作前请核对基金公告、官方净值、费用和交易状态。",
    }
