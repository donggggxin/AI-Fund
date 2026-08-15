# AI Fund Dashboard 服务器部署与运维详解

本文记录 2026-08-15 将 AI Fund Dashboard 从本地电脑部署到服务器
`47.251.40.104` 的完整过程，并解释 Git、Docker、Docker Compose、Nginx、
反向代理、身份认证、数据持久化和日常运维的基本原理。

本文不会记录服务器密码、GitHub 密钥、API Key 或网站登录密码。示例中的
`<...>` 都需要替换为你自己的值，不要直接照抄尖括号内容。

> 当前部署已经可以持续运行，但访问地址仍是 HTTP。HTTP 上的 Basic Auth
> 不能加密传输内容，因此当前方案是“已设置访问门槛”，还不是最终安全形态。
> 正式长期使用应绑定域名并配置 HTTPS。

## 1. 最终部署结果

当前部署结构如下：

```text
用户浏览器
    |
    | HTTP 80（下一步应升级为 HTTPS 443）
    v
Nginx
    |-- 根据 Host 区分已有网站和基金看板
    |-- 对基金看板执行 Basic Auth
    |-- 转发 WebSocket 和普通 HTTP 请求
    v
127.0.0.1:8501
Streamlit 前端容器
    |
    | Docker 内部网络：http://backend:8000
    v
FastAPI 后端容器
    |
    | Docker 内部网络：http://stock-data:3000
    v
stock-sdk Node.js 行情容器

Streamlit 与 FastAPI
    |
    v
Docker 命名卷 fund-data
    |-- fund_config.json
    |-- fund_dashboard.db
    |-- holdings_cache.json
    |-- trend_matrix.json
    `-- agent_report.md
```

服务器上的关键位置：

| 内容 | 位置 |
| --- | --- |
| 应用发布目录 | `/opt/ai-fund` |
| Compose 配置 | `/opt/ai-fund/docker-compose.yml` |
| 服务器环境变量 | `/opt/ai-fund/.env` |
| Nginx 基金站点 | `/etc/nginx/sites-available/ai-fund` |
| Nginx 启用链接 | `/etc/nginx/sites-enabled/ai-fund` |
| Basic Auth 密码文件 | `/etc/nginx/.htpasswd-ai-fund` |
| Docker 持久化卷 | `ai-fund_fund-data` |
| GitHub 私密仓库 | `git@github.com:donggggxin/AI-Fund.git` |

## 2. 为什么不只在本地运行

本地运行适合开发和调试，但有几个限制：

- 电脑关机、休眠或终端关闭后，网站就停止。
- 家庭网络地址可能变化，而且通常没有稳定的公网入口。
- 需要一直保持 Python 环境和终端进程运行。
- 其他设备无法稳定访问。

服务器通常全天在线，Docker 可以在进程异常或服务器重启后自动恢复服务，
因此更适合持续运行。

“持续运行”不等于“永远不会失败”。它意味着系统具备自动重启、状态检查、
数据持久化和可排错能力。

## 3. 本次实际执行的操作

### 3.1 检查本地 Git 状态

部署前先确认工作区没有未提交文件，并确认最近提交：

```bash
cd /Users/dx/learning/AI-Fund-Dashboard-Personal
git status --short
git log --oneline -5
git remote -v
```

`git status --short` 没有输出，表示工作区干净。这样发布的内容能够对应到一个
明确的 Git 提交，出现问题时也容易定位版本。

### 3.2 连接 GitHub 私密仓库

为本地仓库添加远程地址并推送：

```bash
git remote add origin git@github.com:donggggxin/AI-Fund.git
git push -u origin main
```

这里的概念是：

- 本地仓库：电脑上的项目和提交历史。
- 远程仓库：GitHub 上的私密项目。
- `origin`：远程仓库的本地别名，不是特殊服务器。
- `main`：当前主分支。
- `-u`：建立本地 `main` 与远程 `origin/main` 的跟踪关系。以后可以直接
  执行 `git push`。

Git 只负责保存源代码版本，不应该保存个人持仓、数据库或密码。

### 3.3 检查服务器环境

登录服务器后检查了系统、端口、内存和磁盘：

```bash
ssh root@47.251.40.104
uname -a
docker --version
docker compose version
git --version
ss -lntup
df -h /
free -h
```

检查发现：

- 系统为 Ubuntu 24.04。
- 磁盘和内存可以运行该项目。
- Git 已安装。
- Docker 尚未安装。
- `80` 端口已经被 Nginx 使用。
- `8000` 端口已经被另一套 Python 服务使用。
- `8501` 端口未被占用。

先检查端口非常重要。如果两个程序同时绑定同一个宿主机端口，后启动的程序
会报 `address already in use`。

本项目后端的 `8000` 没有映射到宿主机，只存在于 Docker 内部网络，因此不会
与服务器上原有的 `8000` 冲突。

### 3.4 安装 Docker 与 Docker Compose

服务器执行：

```bash
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2
systemctl enable --now docker
```

各命令的作用：

- `apt-get update`：更新软件包索引，不是升级全部系统。
- `docker.io`：Ubuntu 软件源提供的 Docker Engine。
- `docker-compose-v2`：提供 `docker compose` 子命令。
- `systemctl enable docker`：设置 Docker 随系统启动。
- `systemctl start docker`：立即启动 Docker。
- `--now`：同时完成 enable 和 start。

Docker Engine 是真正创建、启动、停止容器的后台服务。Docker Compose 是一个
编排工具，它读取 `docker-compose.yml`，一次管理多个相关容器。

### 3.5 生成并上传发布包

服务器没有配置 GitHub 私密仓库的 Deploy Key。为了避免把个人 GitHub 凭据
直接放进服务器，本次从本地已提交的 `main` 分支生成发布包：

```bash
git archive --format=tar.gz \
  --output=/tmp/ai-fund-release.tar.gz main

scp /tmp/ai-fund-release.tar.gz \
  root@47.251.40.104:/tmp/ai-fund-release.tar.gz
```

服务器解压到：

```bash
mkdir -p /opt/ai-fund
tar -xzf /tmp/ai-fund-release.tar.gz -C /opt/ai-fund
```

`git archive main` 只打包 Git 已跟踪且已经提交的文件，因此不会上传本地的：

- `.env`
- `fund_config.json`
- `fund_dashboard.db`
- Conda 环境
- `node_modules`
- 缓存与日志

这比直接复制整个项目目录更干净，也降低了泄露本地持仓和密钥的风险。

这种发布方式的缺点是服务器不能直接 `git pull`。后续可以继续使用相同的
发布包流程，或者为服务器创建只读 GitHub Deploy Key。不要把个人 GitHub
私钥复制到服务器。

### 3.6 创建服务器 `.env`

服务器创建了仅部署使用的环境变量文件：

```bash
cd /opt/ai-fund
printf 'FUND_API_KEY=%s\nELTDX_ENABLED=1\n' \
  "$(openssl rand -hex 32)" > .env
chmod 600 .env
```

这里没有使用示例中的固定字符串，而是用 `openssl rand` 生成随机 API 密钥。

- `FUND_API_KEY`：保护前端到后端的内部 API 请求。
- `ELTDX_ENABLED=1`：启用 eltdx 行情源。
- `chmod 600`：只有文件所有者可以读写。

`.env` 已被 `.gitignore` 排除，不会推送到 GitHub。

注意：这个 API Key 是容器之间使用的内部密钥，不等于网站登录密码，也不等于
大模型 API Key。

### 3.7 构建并启动三个容器

执行：

```bash
cd /opt/ai-fund
docker compose config --quiet
docker compose up -d --build
```

- `docker compose config --quiet`：先验证 Compose 文件语法。
- `--build`：根据 Dockerfile 构建镜像。
- `up`：创建并启动服务。
- `-d`：后台运行，退出 SSH 后服务不会停止。

首次构建需要下载 Python、Node.js 基础镜像和依赖，因此较慢。后续没有修改
依赖时会复用镜像缓存。

## 4. Docker 相关原理

### 4.1 镜像与容器的区别

可以把镜像理解成“只读程序模板”，把容器理解成“由模板启动出来的进程”。

```text
Dockerfile -> docker build -> Image -> docker run/compose up -> Container
```

修改源代码后，旧容器不会自动获得新代码。通常需要重新构建镜像并重新创建
容器：

```bash
docker compose up -d --build
```

### 4.2 三个服务分别做什么

#### frontend

使用 `Dockerfile.frontend` 构建，启动命令是：

```text
streamlit run web_app.py --server.address=0.0.0.0 --server.port=8501
```

它负责网页界面、表单、图表和用户交互。

#### backend

使用 `Dockerfile.backend` 构建，启动 FastAPI：

```text
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

它负责诊断 API、交易记录、组合历史和每日建议。

#### stock-data

使用 Node.js 22 和 `stock-sdk`，在容器内部监听 `3000`，负责批量行情数据。

### 4.3 Docker 内部网络

Compose 会自动创建一个项目网络。在这个网络中，服务名就是可解析的主机名：

```text
frontend -> http://backend:8000
backend  -> http://stock-data:3000
```

因此不需要把后端 `8000` 和行情 `3000` 暴露到公网。

### 4.4 为什么 8501 只绑定 127.0.0.1

Compose 中配置为：

```yaml
ports:
  - "127.0.0.1:8501:8501"
```

含义从左到右是：

```text
宿主机监听地址 : 宿主机端口 : 容器端口
127.0.0.1      : 8501       : 8501
```

这意味着只有服务器自己能访问 Streamlit。外部用户必须先经过 Nginx 的认证。

如果写成 `8501:8501` 或 `0.0.0.0:8501:8501`，只要云安全组开放 8501，外部
用户就能绕过 Nginx 登录直接访问持仓配置页。

### 4.5 自动重启

三个服务都设置：

```yaml
restart: unless-stopped
```

含义是容器异常退出或 Docker 重启时自动恢复；如果管理员明确执行了停止命令，
则保持停止。

服务器恢复的完整链路是：

```text
服务器开机
  -> systemd 启动 Docker
  -> Docker 根据 restart 策略恢复容器
  -> 健康检查确认服务状态
```

### 4.6 健康检查和依赖顺序

Compose 为 stock-data 和 backend 配置了 `healthcheck`。启动顺序为：

```text
stock-data 健康
    -> backend 启动并健康
        -> frontend 启动
```

`depends_on` 主要控制启动顺序，不代表服务以后永远可用。因此代码仍然保留行情
源降级和前端本地逻辑降级。

### 4.7 数据卷为什么重要

镜像和容器可以删除重建，但用户数据必须独立保存。Compose 使用：

```yaml
volumes:
  - fund-data:/app/data
```

`fund-data` 是命名卷。前端和后端都把它挂载为 `/app/data`，因此两者读写的是
同一份配置和数据库。

执行下面的操作通常不会删除数据卷：

```bash
docker compose restart
docker compose up -d --build
docker compose down
```

但下面的命令会删除 Compose 管理的卷，不能随意执行：

```bash
docker compose down -v
```

`-v` 可能删除持仓、交易流水和报告数据。

## 5. Nginx 相关原理

### 5.1 什么是反向代理

浏览器不是直接访问 Streamlit，而是访问 Nginx：

```text
浏览器 -> Nginx:80 -> Streamlit:127.0.0.1:8501
```

Nginx 代表浏览器向内部服务请求，因此称为“反向代理”。它可以统一处理：

- 域名和端口
- HTTPS 证书
- 用户认证
- 请求日志
- WebSocket 转发
- 限流和访问控制

### 5.2 如何避免影响服务器上已有网站

服务器原有配置：

```nginx
server {
    listen 80;
    server_name creamie.com.cn www.creamie.com.cn;
    ...
}
```

基金看板使用单独的 server block：

```nginx
server {
    listen 80 default_server;
    server_name 47.251.40.104;
    ...
}
```

Nginx 根据浏览器请求中的 `Host` 选择站点：

- `Host: creamie.com.cn` 进入原网站。
- `Host: 47.251.40.104` 进入基金看板。

配置完成后分别验证两者都能正常响应，避免覆盖原网站。

### 5.3 Basic Auth

配置中的：

```nginx
auth_basic "AI Fund Dashboard";
auth_basic_user_file /etc/nginx/.htpasswd-ai-fund;
```

会让浏览器弹出用户名和密码窗口。密码文件通过 `htpasswd` 创建：

```bash
apt-get install -y apache2-utils
htpasswd -c /etc/nginx/.htpasswd-ai-fund <用户名>
chown root:www-data /etc/nginx/.htpasswd-ai-fund
chmod 640 /etc/nginx/.htpasswd-ai-fund
```

Nginx 工作进程通常以 `www-data` 用户运行，因此密码文件需要允许该组读取。

Basic Auth 只是访问认证机制，不提供传输加密。在 HTTP 上，用户名和密码只是
编码而不是加密，网络中间人仍可能截获。因此必须配合 HTTPS 才适合长期使用。

### 5.4 为什么配置 WebSocket

Streamlit 使用 WebSocket 维持浏览器和服务器的实时通信。因此 Nginx 必须包含：

```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_read_timeout 86400;
```

如果遗漏这些设置，可能出现首页能打开但内容一直加载、操作后断开或频繁重连。

### 5.5 修改 Nginx 后的正确流程

先检查，再重载：

```bash
nginx -t
systemctl reload nginx
```

`nginx -t` 会检查语法。只有检查成功才执行 reload。`reload` 会平滑加载新配置，
通常不会中断已有连接；`restart` 则会停止再启动，影响更大。

## 6. Git 中应该保存什么

### 6.1 应该提交

- Python、JavaScript 源代码
- Dockerfile
- `docker-compose.yml`
- 测试
- 配置模板
- 部署说明
- Nginx 示例配置

### 6.2 不应该提交

- `.env`
- API Key
- 服务器密码
- 网站登录密码
- `fund_config.json`
- SQLite 数据库
- AI 报告和持仓缓存
- Conda 虚拟环境
- `node_modules`

项目的 `.gitignore` 已经排除了这些内容。提交前仍应检查：

```bash
git status --short
git diff --cached
```

如果秘密已经提交过，仅在新提交中删除并不代表秘密从 Git 历史消失。此时应先
轮换秘密，再根据需要清理 Git 历史。

## 7. 日常使用与运维命令

先登录服务器：

```bash
ssh root@47.251.40.104
cd /opt/ai-fund
```

### 查看容器状态

```bash
docker compose ps
```

理想状态是 backend 和 stock-data 显示 `healthy`，frontend 显示 `Up`。

### 查看日志

```bash
docker compose logs --tail=200 frontend
docker compose logs --tail=200 backend
docker compose logs --tail=200 stock-data
```

持续观察日志：

```bash
docker compose logs -f frontend
```

按 `Ctrl+C` 只退出日志查看，不会停止容器。

### 重启服务

```bash
docker compose restart
```

只重启前端：

```bash
docker compose restart frontend
```

### 停止和重新启动

```bash
docker compose stop
docker compose start
```

### 查看资源使用

```bash
docker stats
df -h /
free -h
```

## 8. 以后如何发布代码更新

当前推荐继续使用“本地 Git 推送 + 发布包上传”的方式。

### 8.1 本地修改、测试、提交和推送

```bash
cd /Users/dx/learning/AI-Fund-Dashboard-Personal
conda activate ./.conda-env
python -m unittest discover -s tests -v
git status --short
git add <本次修改的文件>
git commit -m "说明本次修改"
git push
```

### 8.2 生成发布包

```bash
git archive --format=tar.gz \
  --output=/tmp/ai-fund-release.tar.gz main
```

### 8.3 上传并解压

```bash
scp /tmp/ai-fund-release.tar.gz \
  root@47.251.40.104:/tmp/ai-fund-release.tar.gz

ssh root@47.251.40.104
tar -xzf /tmp/ai-fund-release.tar.gz -C /opt/ai-fund
cd /opt/ai-fund
```

发布包不包含 `.env`，所以解压不会覆盖服务器密钥。Docker 数据卷也独立于
`/opt/ai-fund`，所以不会覆盖个人持仓。

### 8.4 重建和验证

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8501/_stcore/health
```

如果只改了 Nginx：

```bash
cp /opt/ai-fund/deploy/nginx-ai-fund.conf \
  /etc/nginx/sites-available/ai-fund
nginx -t
systemctl reload nginx
```

## 9. 数据备份与恢复

### 9.1 为什么代码推到 GitHub还不等于已经备份数据

GitHub 保存的是源代码。个人配置和交易数据库在 Docker 数据卷中，不进入 Git。
因此必须单独备份数据卷。

### 9.2 创建数据卷备份

下面的命令把数据卷打包到服务器 `/opt/backups`：

```bash
mkdir -p /opt/backups
docker run --rm \
  -v ai-fund_fund-data:/data:ro \
  -v /opt/backups:/backup \
  alpine \
  tar -czf /backup/ai-fund-data-$(date +%F-%H%M%S).tar.gz -C /data .
```

建议再把备份下载到自己的电脑：

```bash
scp root@47.251.40.104:/opt/backups/<备份文件名> ./
```

备份含个人持仓和可能的大模型密钥，应妥善保管，不要上传到公开位置。

### 9.3 查看已有备份

```bash
ls -lh /opt/backups
```

### 9.4 恢复原则

恢复会覆盖现有运行数据，应先停止容器并再次备份当前卷。不要在未确认目标卷名
和备份内容时执行恢复命令。

基本流程是：

```text
停止容器
  -> 备份当前数据
  -> 检查待恢复压缩包
  -> 恢复到正确的命名卷
  -> 启动容器
  -> 验证基金与交易记录
```

恢复属于高风险操作，建议需要时再按实际备份文件制定命令。

## 10. 配置 HTTPS（强烈建议）

仅使用 IP 地址通常不方便申请公开可信证书。推荐准备一个自己的子域名，例如：

```text
fund.example.com -> 47.251.40.104
```

在域名 DNS 中添加 A 记录，等待解析生效，然后把 Nginx 中的：

```nginx
server_name 47.251.40.104;
```

改为：

```nginx
server_name fund.example.com;
```

Ubuntu 上可使用 Certbot：

```bash
apt-get install -y certbot python3-certbot-nginx
nginx -t
certbot --nginx -d fund.example.com
```

Certbot 会申请 Let's Encrypt 证书并配置续期。完成后验证：

```bash
curl -I https://fund.example.com
systemctl status certbot.timer
certbot renew --dry-run
```

在 HTTPS 配置完成前，不建议在不可信 Wi-Fi 或公共网络中输入网站登录密码。

## 11. 服务器登录安全

本次部署使用了 root 密码登录。因为密码曾通过对话发送，应尽快在服务器更换：

```bash
passwd
```

更推荐使用 SSH 密钥：

```bash
# 本地电脑生成密钥，已有密钥则不要重复覆盖
ssh-keygen -t ed25519 -C "ai-fund-server"

# 将公钥安装到服务器
ssh-copy-id root@47.251.40.104
```

确认密钥登录成功后，再考虑禁止密码登录。禁止前必须保持一个已验证的 SSH
会话，并准备阿里云控制台的救援登录方式，避免把自己锁在服务器外。

更规范的方案是创建普通部署用户、授予必要的 Docker 权限，并减少直接使用
root 的次数。

## 12. 阿里云安全组与服务器防火墙

应用曾监听 `8501`，但公网访问超时，而服务器内部访问成功。这说明云安全组
没有放行该端口。

安全组相当于服务器外层防火墙。服务器进程正在监听，不代表公网一定能访问。

当前设计只需要公网开放：

- `22/tcp`：SSH，最好限制为自己的固定 IP。
- `80/tcp`：HTTP，目前使用；配置 HTTPS 后主要用于跳转和证书验证。
- `443/tcp`：HTTPS，配置域名证书后开放。

不应对公网开放：

- `8501`：Streamlit 内部端口。
- `8000`：FastAPI 内部端口。
- `3000`：stock-sdk 内部端口。

即使安全组误开放 8501，当前 Docker 也只绑定 `127.0.0.1`，形成第二层保护。
安全配置最好采用多层防护，而不是依赖单一设置。

## 13. 常见故障排查

### 网站打不开

依次检查：

```bash
docker compose ps
curl -I http://127.0.0.1:8501
nginx -t
systemctl status nginx
ss -lntp | grep -E ':80|:443|:8501'
```

判断方法：

- 本机 8501 不通：检查 frontend 容器日志。
- 8501 通但 80 不通：检查 Nginx。
- 服务器内部通但公网不通：检查阿里云安全组。
- 返回 502：Nginx 能响应，但上游 Streamlit 未正常运行。
- 返回 401：认证生效，需要输入网站用户名和密码。

### 页面打开后一直加载

检查 Nginx 是否保留 WebSocket 配置，并查看：

```bash
docker compose logs --tail=200 frontend
tail -n 200 /var/log/nginx/error.log
```

### 后端不健康

```bash
docker compose logs --tail=200 backend
docker compose exec backend \
  python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/api/health').read())"
```

### stock-sdk 不健康

```bash
docker compose logs --tail=200 stock-data
docker compose exec stock-data \
  node -e "fetch('http://localhost:3000/health').then(r=>r.text()).then(console.log)"
```

### 磁盘空间不足

```bash
df -h /
docker system df
```

不要在不了解影响时执行 `docker system prune -a --volumes`，其中 `--volumes`
可能影响数据。先确认哪些镜像、容器和卷可以删除。

### Nginx 修改后无法重载

```bash
nginx -t
```

根据输出修正配置。在 `nginx -t` 成功之前不要重启 Nginx。

## 14. 当前方案的边界与后续优化

当前已经具备：

- 私密 GitHub 源代码仓库
- Docker 前后端分层部署
- 服务健康检查和自动重启
- Docker 数据卷持久化
- Nginx 反向代理
- Basic Auth 访问认证
- 内部端口收敛
- 不影响服务器已有网站

仍建议继续完成：

1. 绑定域名并配置 HTTPS。
2. 更换服务器 root 密码。
3. 改用 SSH 密钥并限制 SSH 来源 IP。
4. 设置每日或每周的数据卷自动备份。
5. 增加应用自己的用户登录，而不是长期只依赖 Nginx Basic Auth。
6. 为服务器配置只读 GitHub Deploy Key，或使用 GitHub Actions 自动部署。
7. 增加服务异常监控和通知。

对于个人项目，最实用的优先级是：

```text
立即更换服务器密码
    -> 配置域名和 HTTPS
        -> 自动备份数据卷
            -> SSH 密钥与非 root 运维
                -> 自动化发布和监控
```

## 15. 一页式维护清单

每次发布：

```text
[ ] 本地测试通过
[ ] git status 确认无意外文件
[ ] 提交并推送 GitHub
[ ] git archive 生成已提交版本
[ ] 上传和解压到 /opt/ai-fund
[ ] docker compose config --quiet
[ ] docker compose up -d --build
[ ] docker compose ps
[ ] 检查网页登录和主要功能
[ ] 确认原有 creamie.com.cn 正常
```

定期维护：

```text
[ ] 检查 Docker 容器状态
[ ] 检查磁盘和内存
[ ] 查看异常日志
[ ] 备份 ai-fund_fund-data
[ ] 下载一份备份到本地
[ ] 验证 HTTPS 证书续期
[ ] 安装系统安全更新
[ ] 定期轮换登录密码和 API Key
```

最重要的原则只有三个：

1. 源代码、秘密和运行数据分开管理。
2. 先验证再变更，并为数据变更准备备份。
3. 内部服务不直接暴露公网，所有公网访问统一经过 HTTPS 和身份认证。

## 16. 网站使用方法

### 16.1 第一次打开网站

在浏览器中打开部署地址。Nginx 会先显示登录窗口，输入基金看板专用用户名和
密码。这个密码不是服务器 root 密码，也不是大模型 API Key。

进入页面后可以看到三个主要标签页：

1. **实时监控与智能对话**：查看组合、每日建议、诊断、登记交易和使用问答。
2. **系统参数与配置中心**：添加、修改或批量导入基金，设置策略和 AI 参数。
3. **AI 智能诊断报告**：查看最近一次生成的完整分析报告。

首次启动的数据卷为空，程序会根据 `fund_config.example.json` 自动创建运行配置，
不会自动带入你本地电脑上的个人持仓。

### 16.2 添加第一只基金

进入“系统参数与配置中心”，选择“添加新基金”。建议新手先只添加少量真实
持仓，确认数据显示和交易记录正确后再批量导入。

最重要的字段：

| 字段 | 填写内容 | 示例含义 |
| --- | --- | --- |
| 基金代码 | 基金平台显示的六位代码 | `000001` |
| 基金名称 | 自己容易识别的名称 | 某某混合基金 |
| 持仓成本 | 当前每份平均成本 | 不是当前市值 |
| 持有份额 | 平台显示的确认份额 | 不是买入金额 |
| 标签 | 基金的策略分类 | 科创、海外、红利等 |
| ETF 代理代码 | 用于盘中估算的相关 ETF | 如 `sh...` 或 `sz...` |

成本和份额必须来自交易平台的真实持仓页面。不要把“累计投入金额”直接填入
份额字段。

如果没有可靠的 ETF 代理代码，可以暂时留空。系统会尝试使用其他估值来源，
但盘中估算能力可能下降。

### 16.3 CSV/XLSX 批量导入

基金较多时，打开“批量导入 CSV / XLSX”：

1. 下载页面提供的 CSV 模板。
2. 用 Excel、Numbers 或表格软件填写。
3. 上传 `.csv` 或 `.xlsx`。
4. 阅读预览结果，确认哪些记录会新增、覆盖或跳过。
5. 确认导入。

必填列：

```text
code, name, cost, shares
```

也支持中文列名，例如：

```text
基金代码, 基金名称, 持仓成本, 持有份额
```

导入遵循“整批校验”原则：只要其中一行存在错误，整批都不会写入。这样可以
避免导入一半成功、一半失败造成持仓难以核对。

写入前程序会备份原配置，并使用原子替换写入，降低写到一半进程中断导致配置
损坏的风险。

### 16.4 策略参数怎么理解

| 参数 | 含义 | 新手注意事项 |
| --- | --- | --- |
| `drop` | 单日下跌触发线 | `-0.015` 表示跌约 1.5% 才考虑补仓 |
| `gap` | 与上次补仓价的最小距离 | `0.02` 表示至少再低约 2% |
| `cap` | 单次补仓金额上限 | 是上限，不是必须买满 |
| `tp` | 持仓止盈目标 | `0.10` 表示约 10% |
| `ratio` | 止盈破位后的卖出比例 | `0.5` 表示减仓约 50% |
| `tp_ma` | 止盈使用的移动平均线 | 常见为 MA5、MA10、MA20 |
| `multiplier` | 补仓金额放大系数 | 越高代表跌时使用资金越快 |
| `daily_invest` | 计划定投金额 | 应来自长期闲置资金 |

这些参数不是“预测市场”的魔法数字，而是把你的风险纪律写成程序。新手不应
频繁根据当天情绪修改它们。先使用保守上限，观察至少几个交易周期，再根据自己
的现金流和风险承受能力调整。

### 16.5 每天如何查看首页

推荐每天按以下顺序阅读：

#### 第一步：确认市场状态和数据时间

查看页面显示的是开盘、午休、收盘还是节假日，并查看数据时点。盘中估值不是
基金公司最终净值，休市时系统只提示复盘。

#### 第二步：查看组合级指标

- **组合市值**：估算净值乘以持有份额后求和。
- **未实现盈亏**：当前估算市值减去持仓成本金额。
- **持仓收益率**：未实现盈亏除以总成本。
- **今日估算变动**：根据各基金估算涨跌计算的当日金额变化。
- **标签配置**：不同策略标签在组合中的金额分布。

组合数据比单只基金涨跌更重要。单只基金上涨不代表整体风险下降，单只基金
下跌也不一定需要操作。

#### 第三步：看“今日投资行动指南”

系统把底层诊断翻译为：

- 分批买入
- 减仓
- 持有
- 暂停
- 观察
- 复盘

每条建议都包含：行动、触发原因、原理和操作前检查。先阅读原因中的实际数字，
确认它是否来自你设置的阈值，不要只看绿色或红色标签。

#### 第四步：核对单只基金诊断

查看今日估算涨跌、当前持有收益、止盈目标、资金流和诊断文字。如果行情接口
暂时不可用，某些字段会显示 `--`，此时不要把缺失数据理解成涨跌为零。

#### 第五步：决定是否真的操作

系统输出的是规则提示，不是必须执行的订单。操作前至少确认：

```text
[ ] 基金平台的最新官方净值或估值
[ ] 是否开放申购、赎回以及限购金额
[ ] 手续费和赎回到账时间
[ ] 使用的是长期闲置资金
[ ] 操作后单只基金占比是否过高
[ ] 规则是否仍符合自己的风险承受能力
```

### 16.6 买入或卖出后如何登记

网站不会连接券商或基金平台自动下单。你需要先在自己的合法交易平台完成交易，
然后回到看板登记。

买入登记：

1. 输入实际成交或确认净值。
2. 输入实际买入金额。
3. 输入申购费用。
4. 确认保存。

系统会计算新增份额、更新总份额和加权平均成本，并记录这次交易。

卖出登记：

1. 输入实际确认净值。
2. 选择实际卖出比例。
3. 输入赎回费用。
4. 确认保存。

系统会减少持有份额，并在 SQLite 账本追加一条不可变交易流水。这里的“不可变”
指不直接覆盖旧交易；后续状态变化通过新记录体现，更利于审计和排错。

登记前应以基金平台最终确认结果为准。盘中提交基金订单不代表以盘中看到的净值
成交，普通开放式基金通常按产品规则在之后确认净值和份额。

### 16.7 查看和导出交易流水

配置中心可以查看最近交易并导出 CSV。交易记录包括：

- 时间
- 基金代码和名称
- 买入或卖出
- 确认净值
- 份额和金额
- 手续费
- 交易来源
- 交易后份额和成本

建议定期导出，并与基金平台对账。看板是个人辅助账本，平台的正式成交记录才是
最终依据。

### 16.8 AI 报告和问答如何使用

基础行情诊断、每日行动解释和交易记账不依赖大模型 API Key。

需要完整 AI 报告时：

1. 进入“系统参数与配置中心”。
2. 选择模型提供商。
3. 填写自己的 API Key、Base URL 和模型名称。
4. 启用 AI 分析。
5. 回到首页点击“运行后台 AI 智能分析”。
6. 在“AI 智能诊断报告”查看结果。

AI 的职责是总结和解释，不应该绕过规则层直接控制交易。模型可能误解数据、
产生不准确内容或受接口错误影响，因此任何 AI 建议都需要与确定性的数字和规则
交叉验证。

大模型 API Key 会进入个人运行配置，因此数据卷备份也应视为敏感文件。

### 16.9 新手推荐的日常流程

```text
每天固定时间打开看板
  -> 确认市场状态和数据时间
  -> 先看组合，再看单只基金
  -> 阅读建议的触发数字与原因
  -> 没有触发就不操作
  -> 有触发也先去交易平台核对
  -> 在交易平台执行自己的决定
  -> 成交确认后回看板登记
  -> 每周对账，每月备份
```

不要为了“每天使用网站”而每天交易。网站每天给出“持有”或“观察”同样是有效
结果。

## 17. 行情、估值与策略原理

### 17.1 基金为什么不能像股票一样直接得到实时成交价

普通开放式基金通常每天公布一次正式净值。盘中显示的基金估值是根据已公开
持仓、相关股票或 ETF 变化推算出来的，不是基金公司的最终确认净值。

因此系统区分：

- **历史/官方净值**：基金公司公布后形成的正式数据。
- **官方估算**：第三方接口提供的盘中估值。
- **持仓穿透估算**：根据基金公开持仓及代理 ETF 计算。
- **严谨终值**：按可用数据优先级选择的当前估算结果。

“严谨”表示程序采用了明确的数据选择规则，不表示估值一定准确。

### 17.2 多行情源降级原理

当前行情顺序是：

```text
eltdx -> stock-sdk -> 腾讯行情直连
```

程序先使用前一个来源获取数据，只对缺失代码调用后一个来源。这样做的目的：

- 某个接口临时不可用时网站仍可运行。
- 减少单一数据源故障影响。
- 批量接口可降低请求次数。

降级只能提高可用性，不能保证不同数据源完全一致。不同接口的刷新时点、复权
方式和交易状态处理可能不同。

### 17.3 持仓穿透估算

如果基金公开持仓包含股票代码和权重，系统大致计算：

```text
持仓贡献 = 每只股票当日涨跌比例 × 对应权重
基金估算涨跌 = 所有已知持仓贡献之和
             + 代理 ETF 涨跌 × 未覆盖权重
```

公开持仓通常来自上一季度，基金经理可能已经调仓，因此穿透估算仍存在误差。

### 17.4 估算净值

当日正式净值已经公布时，直接使用正式净值。

盘中或收盘后当日净值尚未公布时，大致计算：

```text
估算净值 = 最近正式净值 × (1 + 今日估算涨跌比例)
```

非交易日或开盘前不会重复叠加前一交易日涨跌，避免把同一天变化计算两次。

### 17.5 持有收益率

单只基金持有收益率：

```text
持有收益率 = (估算净值 - 持仓成本) / 持仓成本
```

这是基于每份平均成本的未实现收益，不等同于考虑全部历史现金流后的年化收益率。

### 17.6 组合市值和未实现盈亏

单只基金：

```text
市场价值 = 持有份额 × 估算净值
成本金额 = 持有份额 × 每份持仓成本
未实现盈亏 = 市场价值 - 成本金额
```

组合指标是所有基金求和。标签配置比例大致为：

```text
标签权重 = 该标签基金市场价值之和 / 组合总市场价值
```

### 17.7 下跌触发线为什么不能单独决定买入

系统不是“跌了就买”，而是至少检查两层条件：

```text
条件 1：今日估算涨跌 <= drop
条件 2：当前估算净值相对上次补仓价已下降至少 gap
```

第二层被称为“空间锁”。它用于避免连续几次小跌时每天补仓，导致现金很快用完。

如果第一层满足、第二层不满足，系统显示“空间锁拦截”，建议观察而不是买入。

### 17.8 补仓金额原理

触发后，程序综合当前持仓价值、当日跌幅、放大系数和亏损程度计算参考金额，
最后限制在最低参考金额和 `cap` 上限之间。

其思想不是精确预测最低点，而是：

- 跌幅较小时少用资金。
- 跌幅较大时适度增加。
- 深度亏损时按既定系数调整。
- 无论公式结果多大，都不能超过单次上限。

金额只是规则参考。如果没有足够应急金、基金基本情况发生变化或组合占比过高，
即使出现信号也可以不执行。

### 17.9 周期基金长期趋势保护

标签为“周期”的基金，在下跌触发时还会检查 MA60。如果估算净值低于 MA60，
系统可能提示“破位，观望为上”，暂停补仓。

原因是周期资产在下降趋势中可能持续走弱，仅凭单日跌幅抄底风险较高。

### 17.10 止盈与均线确认

当持有收益率达到 `tp` 后，不一定立即全部卖出。系统继续检查目标均线：

- 净值仍高于目标均线：趋势较强，提示持有观察。
- 净值跌到目标均线下方：提示按 `ratio` 分批减仓。
- 缺少可靠均线数据：提示等待确认。

这种方法把“达到收益目标”和“趋势转弱”结合，目的是避免过早退出强趋势，
同时在趋势出现破坏时分批兑现。

均线是历史价格的平均值，会滞后，也可能产生假信号。它是纪律工具，不是预测
未来的保证。

### 17.11 止盈静默期

刚执行过卖出后，系统设置短暂静默期，避免同一个止盈信号连续多日重复触发，
导致仓位下降过快。

静默期的本质是“交易幂等保护”：同一个条件在短时间内重复出现时，不重复执行
同一动作。

### 17.12 为什么深度亏损不自动建议继续补仓

持有亏损较深但当日下跌触发条件未满足时，系统显示等待信号，而不是仅凭亏损
扩大就补仓。

亏损本身不能证明资产便宜，也不能证明马上反弹。把“我已经亏很多”当成买入
理由，容易形成沉没成本偏误。

### 17.13 每日行动指南如何生成

每日指南由确定性 Python 规则生成，而不是让大模型自由决定买卖：

```text
行情和正式净值
  -> 计算估算涨跌、估算净值和持有收益
  -> 应用用户配置的阈值
  -> 产生底层诊断
  -> 翻译为行动、原因、原理和检查清单
```

这种设计的优点是：

- 同样输入会得到同样结论。
- 每个结论可以追溯到阈值和数字。
- 不依赖大模型是否稳定。
- 便于自动测试。

AI 可以进一步解释这些结果，但不改变规则触发事实，也不会自动下单。

### 17.14 市场阶段的作用

AI 分析可以把市场概括为主升浪、高位震荡、回调期或趋势破坏，并为定投规模
提供背景参考。市场阶段属于较高层判断，应低于个人现金流、资产配置和单只基金
规则的优先级。

阶段识别可能滞后或判断错误，不能因为显示“主升浪”就追涨，也不能因为显示
“趋势破坏”就不经核对清空全部长期资产。

### 17.15 为什么系统不自动交易

当前项目刻意只做分析和记账，不连接交易账户自动下单，原因包括：

- 基金估值不是最终成交净值。
- 公开行情和接口可能延迟或失败。
- 用户的现金流、税费和真实风险承受能力无法由程序完全掌握。
- 自动交易涉及账户安全、合规、授权和不可逆资金操作。
- 新手更需要理解每次决策，而不是隐藏决策过程。

因此正确定位是“可解释的个人决策辅助工具”，不是代客理财系统，也不是收益
保证工具。

## 18. 新手投资安全边界

使用网站前应先做到：

- 保留足够生活应急金。
- 不使用借款、信用卡或短期必需资金投资。
- 不把全部资金投入单一基金、行业或同一天。
- 理解基金可能亏损，本金并不保证安全。
- 交易前阅读产品公告、费率和风险说明。
- 定期与正式交易平台对账。

看板中的红色、绿色、买入、卖出等文字都不构成投资建议。它们是根据你设置的
规则和公开估算数据生成的教育性提示。最终操作责任仍由账户持有人承担。

## 19. stock-sdk 与 eltdx 的实际接入方式

项目开发时参考并实际接入了以下两个开源项目：

- `stock-sdk`：<https://github.com/chengzuopeng/stock-sdk>
- `eltdx`：<https://github.com/electkismet/eltdx>

它们不是只写在需求或文档中，而是已经进入依赖、源代码、测试和服务器容器
运行链路。

### 19.1 为什么需要两个行情项目

基金盘中估值依赖股票和 ETF 行情。如果只依赖单一接口，当接口超时、返回字段
变化或部分代码缺失时，整个组合可能无法估算。

因此项目采用多行情源逐层补齐：

```text
需要查询的一组股票/ETF代码
    |
    v
eltdx 批量查询
    |
    | 只保留成功返回的代码
    v
把缺失代码交给 stock-sdk
    |
    | 再次只保留成功返回的代码
    v
把仍然缺失的代码交给腾讯行情直连
    |
    v
合并为统一的涨跌比例字典
```

这称为“降级”或“fallback”。降级不是同时请求所有来源再随意选择，而是先使用
优先级较高的来源，只对缺失项请求后续来源。

### 19.2 stock-sdk 如何接入

`stock-sdk` 是 Node.js 包，而项目主要后端是 Python。为了保持语言边界清晰，
项目为它创建了独立的 Node.js 内部微服务。

依赖定义位于：

```text
services/stock-data/package.json
services/stock-data/package-lock.json
```

固定版本为：

```json
{
  "dependencies": {
    "stock-sdk": "2.4.1"
  }
}
```

固定版本可以避免服务器每次构建时自动安装不同版本，降低上游更新造成接口不兼容
的概率。

服务实现位于：

```text
services/stock-data/server.js
```

代码直接导入：

```javascript
import { StockSDK } from 'stock-sdk';
```

内部微服务提供两个主要端点：

```text
GET  /health
POST /quotes/cn
```

健康检查用于 Docker 判断服务是否可以接受请求。行情接口接收一批中国市场代码，
调用 stock-sdk 获取行情，再整理为 Python 后端容易消费的 JSON。

### 19.3 stock-sdk 为什么使用微服务

如果 Python 直接通过子进程调用 Node.js，会产生进程管理、错误捕获和并发问题。
使用独立服务后，职责更清晰：

```text
Python 后端：基金诊断、组合计算、数据库和 API
Node.js 服务：封装 stock-sdk 行情能力
```

两者通过 HTTP/JSON 通信。这样以后替换 stock-sdk 版本或实现时，不需要重写基金
诊断核心。

缺点是多了一个需要部署和监控的容器，因此项目为它增加了健康检查和自动重启。

### 19.4 stock-data Docker 容器

镜像定义位于：

```text
services/stock-data/Dockerfile
```

核心步骤：

```dockerfile
FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY server.js ./
EXPOSE 3000
CMD ["npm", "start"]
```

- `node:22-alpine`：使用较小的 Node.js 22 基础镜像。
- `npm ci`：严格按照 lock 文件安装，适合可重复构建。
- `--omit=dev`：生产镜像不安装开发依赖。
- `EXPOSE 3000`：声明服务在容器内使用 3000 端口。

Compose 没有把 3000 映射到公网。它只存在于 Docker 内部网络，降低了攻击面。

### 19.5 Python 后端如何调用 stock-sdk

Compose 为 backend 注入：

```yaml
STOCK_SDK_URL: http://stock-data:3000
```

`stock-data` 是 Compose 服务名，也是 Docker 内部 DNS 主机名。

Python 调用函数位于 `diagnostics.py`：

```text
fetch_market_prices_from_stock_sdk(code_list)
```

调用过程：

```text
Python 整理缺失代码
  -> POST http://stock-data:3000/quotes/cn
  -> Node.js 调用 stock-sdk
  -> 返回 JSON 行情
  -> Python 匹配原始代码
  -> 把百分数转为小数比例
```

stock-sdk 返回的 `changePercent` 按百分数表达，例如 `2.5` 表示上涨 2.5%。项目
内部统一使用小数比例，因此转换为：

```text
2.5 / 100 = 0.025
```

如果遗漏这一步，后续基金估值会被放大 100 倍。

### 19.6 eltdx 如何接入

`eltdx` 是 Python 包，因此可以直接由 FastAPI/Python 后端调用，不需要额外语言
适配服务。

依赖固定在 `requirements.txt`：

```text
eltdx==1.3.0
```

调用代码位于 `diagnostics.py`：

```text
fetch_market_prices_from_eltdx(code_list)
```

核心调用方式：

```python
from eltdx import TdxClient

with TdxClient(timeout=3, heartbeat_interval=None) as client:
    quotes = client.get_quote(supported)
```

当前只把具有 `sh`、`sz` 或 `bj` 前缀的沪深京市场代码交给 eltdx。其他不支持
的代码不会强行查询，而是留给后续行情源处理。

eltdx 的涨跌幅同样会转换为项目内部的小数比例：

```text
quote.change_pct / 100
```

### 19.7 如何启用或关闭 eltdx

服务器 `.env` 中使用：

```text
ELTDX_ENABLED=1
```

关闭时改为：

```text
ELTDX_ENABLED=0
```

修改后重新创建后端容器，使环境变量生效：

```bash
cd /opt/ai-fund
docker compose up -d --force-recreate backend frontend
```

关闭 eltdx 后，系统会从 stock-sdk 开始获取行情，并继续保留腾讯直连兜底。

eltdx 使用通达信协议连接外部行情服务器，部署网络可能无法访问相应端口，或者
连接存在不稳定情况。程序设置了较短超时，并在异常时静默进入下一数据源，避免
页面被单一连接长时间卡住。

### 19.8 完整行情合并算法

统一入口位于：

```text
diagnostics.py -> fetch_market_prices(code_list)
```

逻辑可以简化为：

```python
prices = {}

prices.update(eltdx成功结果)

missing = 原始代码 - prices已有代码
prices.update(stock_sdk对missing的成功结果)

missing = 原始代码 - prices已有代码
prices.update(腾讯接口对missing的成功结果)

return prices
```

假设需要 10 个代码：

```text
eltdx 成功返回 7 个
  -> stock-sdk 只查询剩余 3 个，并成功返回 2 个
      -> 腾讯接口只查询最后 1 个
          -> 合并返回最多 10 个结果
```

这样不会让后面的低优先级来源覆盖前面已经成功获取的数据。

### 19.9 异常处理原则

行情属于外部依赖，常见失败包括：

- 网络超时
- DNS 或端口不可达
- 上游接口临时限制
- 返回 JSON 格式变化
- 个别代码不存在或停牌
- 上游服务进程异常

项目的原则是：

```text
单一来源失败
  -> 不让整个诊断崩溃
  -> 尝试下一来源
  -> 最终仍缺失时使用明确的缺失/零估算状态
```

这里需要区分“容错”和“准确”。容错保证页面尽可能继续运行，但不能把缺失数据
变成真实数据。用户仍应关注数据时点和缺失字段。

### 19.10 测试覆盖

Python 测试中验证了百分数归一化：

```text
tests/test_diagnostics.py
```

包括：

- eltdx 百分数是否正确除以 100。
- stock-sdk 百分数是否正确除以 100。
- 行情变化是否正确进入基金诊断。

Node.js 服务测试位于：

```text
services/stock-data/server.test.js
```

本地可执行：

```bash
cd services/stock-data
npm test
```

Python 测试：

```bash
cd /Users/dx/learning/AI-Fund-Dashboard-Personal
conda activate ./.conda-env
python -m unittest discover -s tests -v
```

### 19.11 服务器上如何确认两者正在工作

查看三个容器：

```bash
ssh root@47.251.40.104
cd /opt/ai-fund
docker compose ps
```

`ai-fund-stock-data-1` 对应 stock-sdk 服务。理想状态为 `healthy`。

查看 stock-sdk 日志：

```bash
docker compose logs --tail=200 stock-data
```

在容器内检查健康端点：

```bash
docker compose exec stock-data \
  node -e "fetch('http://localhost:3000/health').then(r=>r.text()).then(console.log)"
```

确认后端是否启用 eltdx：

```bash
docker compose exec backend printenv ELTDX_ENABLED
```

查看 Python 后端日志：

```bash
docker compose logs --tail=200 backend
```

需要注意：当前降级逻辑会捕获 eltdx 的连接异常并继续运行，因此日志中没有报错
不一定表示每次请求都由 eltdx 成功返回。若以后需要精确观察每个请求的数据源，
可以增加来源统计、耗时和失败次数指标。

### 19.12 当前服务器中的实际状态

本次部署实际启动了：

```text
ai-fund-frontend-1
ai-fund-backend-1
ai-fund-stock-data-1
```

部署验证时 `stock-data` 和 `backend` 均显示 `healthy`。服务器配置为：

```text
ELTDX_ENABLED=1
STOCK_SDK_URL=http://stock-data:3000
```

因此当前运行链路确实包含 eltdx 和 stock-sdk，不是仅安装未使用。

### 19.13 使用限制与风险

这两个项目以及它们连接的上游数据源都可能更新、限制访问或调整使用条款。
部署前和升级依赖时应重新检查各自仓库的许可证、README 和数据源使用规则。

尤其需要注意：

- 只应用于个人学习、研究和个人辅助判断。
- 不应把未经授权的行情数据转售或用于收费数据服务。
- 不应把估算行情描述为基金公司官方实时净值。
- 上游仓库更新前先在测试环境验证，不要直接取消版本固定。
- 行情服务异常时不要依据缺失或延迟数据执行交易。

如果未来把项目改成商业产品，需要重新评估数据授权、许可证、稳定性、合规和
服务等级，不能直接沿用当前个人研究部署方案。
