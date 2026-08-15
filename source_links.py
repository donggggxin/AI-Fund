"""为报告和问答生成可访问的数据来源链接。"""


def _fund_url(code):
    return f"https://fund.eastmoney.com/{code}.html"


def _quote_url(symbol):
    if str(symbol).lower().startswith("us"):
        return f"https://gu.qq.com/{symbol}/gp"
    return f"https://quote.eastmoney.com/{symbol}.html"


def build_sources_markdown(config, *, fund_codes=None, market_symbols=None):
    """返回不包含密钥的来源清单，供 Markdown 报告和问答引用。"""
    codes = list(fund_codes or [])
    lines = [
        "## 数据来源与可访问链接",
        "",
        "- 基金净值、历史净值与基金资料：",
    ]
    if codes:
        for code in codes:
            name = config.get(str(code), {}).get("name", str(code))
            lines.append(f"  - [{name}（{code}）]({_fund_url(code)})（天天基金/东方财富）")
    else:
        lines.append("  - [天天基金](https://fund.eastmoney.com/)")

    lines.extend([
        "- 基金实时估值： [天天基金估值接口说明](http://fundgz.1234567.com.cn/)",
        "- ETF/A 股行情与资金流向： [东方财富行情](https://quote.eastmoney.com/)",
        "- 海外股票与指数行情： [腾讯证券行情](https://gu.qq.com/)",
        "",
        "> 页面中的“官方估算”、实时行情和资金流向可能存在延迟，交易前请以基金公司或交易所最终数据为准。",
    ])

    symbols = [str(item) for item in (market_symbols or []) if item]
    if symbols:
        lines.insert(-2, "- 本次跟踪的代理标的： " + "、".join(
            f"[{symbol}]({_quote_url(symbol)})" for symbol in symbols
        ))
    return "\n".join(lines)
