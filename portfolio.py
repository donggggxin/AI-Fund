"""组合级指标计算，保持为纯函数以便 API、Web 和测试复用。"""

from collections import defaultdict


def summarize_portfolio(rows, config):
    total_value = 0.0
    total_cost = 0.0
    estimated_day_change = 0.0
    by_tag = defaultdict(float)
    positions = []

    for row in rows:
        shares = float(row.get("shares", 0) or 0)
        nav = float(row.get("est_nav", 0) or 0)
        cost = float(row.get("cost", 0) or 0)
        value = shares * nav
        cost_basis = shares * cost
        change_rate = float(row.get("v_hyb", 0) or 0)
        day_change = value * change_rate / (1 + change_rate) if change_rate > -1 else 0
        total_value += value
        total_cost += cost_basis
        estimated_day_change += day_change
        tag = config.get(row.get("code"), {}).get("tag", "未分类")
        by_tag[tag] += value
        positions.append({
            "code": row.get("code"),
            "name": row.get("name"),
            "tag": tag,
            "market_value": round(value, 2),
            "cost_basis": round(cost_basis, 2),
            "unrealized_pnl": round(value - cost_basis, 2),
            "weight": 0.0,
        })

    for position in positions:
        position["weight"] = (
            round(position["market_value"] / total_value, 6) if total_value else 0.0
        )
    positions.sort(key=lambda item: item["market_value"], reverse=True)
    unrealized = total_value - total_cost
    return {
        "total_market_value": round(total_value, 2),
        "total_cost_basis": round(total_cost, 2),
        "unrealized_pnl": round(unrealized, 2),
        "unrealized_return": round(unrealized / total_cost, 6) if total_cost else 0.0,
        "estimated_day_change": round(estimated_day_change, 2),
        "allocation_by_tag": {
            tag: round(value, 2)
            for tag, value in sorted(by_tag.items(), key=lambda item: item[1], reverse=True)
        },
        "positions": positions,
    }
