# AI 基金智能体（个人版）

这是一个在个人电脑本地运行的基金监控看板。每位使用者保存自己的基金配置、持仓数据与 API Key；本分享包不含任何他人的个人数据或密钥。

## 首次使用

1. 安装 64 位 Python 3.10 或更新版本：<https://www.python.org/downloads/windows/>。
   安装时请勾选 `Add Python to PATH`。
2. 双击 `安装依赖.bat`，等待提示安装完成。
3. 首次使用时，将 `fund_config.example.json` 复制为 `fund_config.json`。
4. 双击 `启动基金大屏.bat`，浏览器会打开 `http://localhost:8501`。
5. 打开“系统参数与配置中心”。
6. 在“添加新基金”中只需录入基金代码、持仓成本和当前金额；基金名称、最新净值和份额会自动获取/计算。
7. 如需 AI 报告和问答，在“大模型参数微调”中填入自己的 API Key，并勾选“启用 AI 分析”。

## 批量导入基金

配置中心支持上传 `.csv` 或 `.xlsx`。必填列为 `code`、`name`、`cost`、
`shares`，同时支持“基金代码、基金名称、持仓成本、持有份额”等中文列名。
页面提供 CSV 模板下载，并在导入前展示新增、覆盖或跳过预览。

确认导入后，系统会先备份原配置再原子写入；模型密钥、节假日等系统配置
不会被批量基金文件覆盖。只要文件中存在一条校验错误，整批数据都不会写入。

## 交易流水

Web 或 FastAPI 登记买卖时，系统会同步追加到 SQLite 交易账本
`fund_dashboard.db`。记录包含成交时间、基金、方向、净值、份额、金额、费用、
来源以及交易后的持仓状态。配置中心可以查看最近 200 条并导出完整 CSV；后端
提供 `GET /api/trades`，支持 `limit` 和 `fund_code` 查询参数。

## 组合总览

实时监控页显示组合市值、未实现盈亏、持仓收益率、今日估算变动和标签资产
配置。系统每天更新一条 SQLite 组合快照；积累两个以上交易日后显示市值趋势。
后端通过 `GET /api/portfolio/history` 提供历史快照。

## 每日投资行动指南（新手版）

实时监控页会把原始诊断翻译成“分批买入、减仓、持有、暂停、观察或复盘”，
并逐只展示触发原因、背后原理和操作前检查项。结论直接引用你的下跌触发线、
止盈目标、均线和金额上限；休市时只提供复盘提示，不提示立即交易。

后端可通过 `GET /api/advice/today` 获取同一份结构化建议。该功能为规则解释工具，
不会自动下单，也不构成投资建议；执行前仍需核对官方净值、基金公告、费用和
交易状态。

## 使用说明

- 看板只在本机运行，关闭启动窗口后服务停止。
- 基金净值、估值、行情和资金流来自公开数据接口，可能存在延迟或短暂不可用。
- “官方估算”和“归一动能”均是参考数据，不等同于基金公司最终确认的净值。
- 智能分析报告和问答涉及外部行情时，会在末尾标注数据来源及可访问链接；链接页面的数据可能有延迟，交易前请以基金公司或交易所最终数据为准。
- 交易建议仅为程序规则输出，不构成投资建议；买卖前请自行判断并核对实际成交数据。

## 保护个人信息

- 不要把 `fund_config.json` 发给其他人：它会包含你的持仓、成本、定投参数和 API Key。
- 不要共享 `agent_report.md`、`holdings_cache.json`、`trend_matrix.json` 等运行中产生的文件。
- API Key 应由每位使用者自行申请、单独保管；如怀疑泄露，请立即到服务商后台轮换密钥。

## 文件说明

- `启动基金大屏.bat`：启动网页看板。
- `安装依赖.bat`：首次安装或依赖缺失时运行。
- `fund_config.example.json`：可安全提交的空白配置模板。
- `fund_config.json`：个人基金与模型配置，由模板复制生成，已被 Git 忽略。
- `web_app.py`：网页看板。
- `ai_agent.py`：市场分析与报告生成。
- `diagnostics.py`：共享的行情和诊断逻辑。
- `upupup.py`：可选的控制台监控程序。

## Docker 部署（前后端分层）

1. 将 `.env.example` 复制为 `.env`，并替换 `FUND_API_KEY`。
2. 运行 `docker compose up -d --build`。
3. 打开 `http://服务器地址:8501`。

部署模式包含两个服务：

- `frontend`：Streamlit 页面，通过内部网络访问后端诊断 API。
- `backend`：FastAPI 行情与诊断服务，健康检查为 `/api/health`。
- `stock-data`：基于 `stock-sdk` 的内部 Node.js 行情服务，优先提供 A 股/ETF 批量行情；不可用时 Python 后端自动回退到原腾讯接口。

后端行情采用多源降级顺序：`eltdx → stock-sdk → 腾讯直连`。其中 `eltdx`
仅允许个人学习、协议研究和非商业研究，不能用于商业或收费服务；可通过
`.env` 中的 `ELTDX_ENABLED=0` 关闭。

运行数据保存在 Docker 命名卷 `fund-data` 中，重建容器不会清空持仓配置。
本地双击启动时不要求启动 FastAPI，页面会自动使用共享诊断模块降级运行。

公网服务器部署时，前端端口默认只监听 `127.0.0.1:8501`，应通过带身份认证和
HTTPS 的 Nginx/Caddy 反向代理访问，不要将持仓配置页面直接暴露到公网。
`deploy/nginx-ai-fund.conf` 提供了 Nginx Basic Auth 反向代理示例。
该配置会将 Basic Auth 用户名透传给前端，页面右上角和侧边栏会显示当前用户；直接访问 Streamlit 端口时显示“本地用户”。

## 大模型问答 404 排查

问答助手使用 OpenAI 兼容的 `chat/completions` 接口。配置中心的“API 渠道”、
“Base URL”和“模型名称”必须属于同一个服务商。Base URL 支持以下写法：

- DeepSeek：`https://api.deepseek.com`，程序请求
  `https://api.deepseek.com/chat/completions`。
- OpenAI：`https://api.openai.com/v1`，程序请求
  `https://api.openai.com/v1/chat/completions`。
- 代理服务：可以填写以 `/v1` 结尾的 API 根地址，也可以直接填写完整的
  `/chat/completions` endpoint；程序不会重复追加路径。

如果返回 404，优先检查渠道是否和模型匹配、Base URL 是否属于该服务商，以及
模型名称是否在该服务商账号下可用。页面会显示不含 API Key 的实际接口地址和服务商
返回内容，便于区分“路径不存在”和“模型不存在”。

修改 Docker 中的 Python 代码后必须重新构建前端镜像：

```bash
docker compose up -d --build frontend
```

仅执行 `docker compose restart frontend` 只会重启旧镜像，不会把工作区的新代码复制
进容器。

开发时也可以分别启动：

```bash
uvicorn backend.main:app --reload
API_BASE_URL=http://127.0.0.1:8000 streamlit run web_app.py
```

## Conda 开发环境

项目提供 `environment.yml`，可重建 Python 3.12 开发环境：

```bash
conda env create -f environment.yml
conda activate ai-fund-dashboard
python -m unittest discover -s tests -v
```

当前机器也可以使用项目内隔离环境：

```bash
conda activate ./.conda-env
```
