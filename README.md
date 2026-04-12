# rssZspaceDownloader

多租户版 PT RSS 自动下载器。当前项目主入口只保留 **RSS 下载功能**，并已拆分为「用户前台」与「管理员后台」。

可以登录http://nas.sinyerobot.com:5000/进行测试。

## 这次改造内容

- 功能拆分：`main.py` 仅承载下载器功能。
- 多租户：支持多个租户独立配置、独立下载历史、独立去重数据。
- 配置入库：Transmission 与 RSS 模式配置统一写入 SQLite（`data/app.db`）。
- 前后台分离：
  - 用户前台：租户用户登录后配置 Transmission/RSS，执行下载。
  - 管理后台：管理员登录后管理租户（新增、停用、删除）。
- 前端组件：前后台页面改为 Element Plus（CDN 方式）。
- 用户无感租户 Key：前台登录仅用户名+密码；注册时系统自动生成租户 Key。
- 角色约束：租户侧仅普通用户（`user`），不再区分租户管理员角色。
- 租户自注册：租户可在前台自主注册（创建租户 + 初始登录用户）。
- 运维能力：支持租户启用/停用、软删除/硬删除、配置审计日志。
- 历史迁移：支持将旧版 `data/history.json` 一键迁移到数据库历史表。

## 项目结构（关键）

- `main.py`：下载器主入口（多租户 + 数据库配置）
- `services/tenant_store.py`：租户与配置存储
- `services/multi_tenant_download_service.py`：下载执行逻辑
- `news_app.py`：资讯机器人旧能力入口（独立运行）
- `legacy_all_in_one_app.py`：改造前的混合入口备份

## 快速开始

1. 安装依赖

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

2. 配置 `.env`（仅应用级配置 + 默认租户初始化种子）

```bash
copy .env.example .env
```

3. 启动

```bash
python main.py
```

4. 打开页面

- 用户前台：`http://localhost:5000/user/login`
- 管理后台：`http://localhost:5000/admin/login`

## Docker 启动

1. 准备环境文件（首次）

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

2. 启动

```bash
docker compose up -d --build
```

3. 查看日志

```bash
docker compose logs -f
```

4. 停止

```bash
docker compose down
```

说明：
- `docker-compose.yml` 已把 `./data` 和 `./logs` 挂载到容器，数据与日志会持久化。
- 容器内访问宿主机 Transmission 时，建议把 `TRANSMISSION_HOST` 配成 `http://host.docker.internal:9091`（Linux 环境可用宿主机实际 IP）。
- Docker 镜像默认使用 `requirements-docker.txt`（轻量依赖，不包含 Playwright），更适合 NAS 场景快速部署。

## 默认账号

- 首次启动会自动初始化数据库并创建 `default` 租户。
- 默认管理员账号：`admin`
- 默认管理员密码：`admin`
- 默认租户登录账号：`admin`
- 默认租户登录密码：`admin`

## 配置说明

- `.env` 中的 Transmission / RSS 字段仅用于默认租户首次种子导入。
- 后续请在 Web 控制台里修改配置（写入数据库）。

## 数据库表

- `tenants`
- `tenant_configs`
- `tenant_rss_modes`
- `download_history`
- `downloaded_items`
- `tenant_audit_logs`
- `migration_jobs`
- `admin_accounts`
- `tenant_users`

## 常用接口

### 用户前台
- `POST /api/user/register`：租户自注册
- `POST /api/user/login` / `POST /api/user/logout`：登录/退出
- `GET /api/user/config` / `PUT /api/user/config`：查看/更新本租户配置
- `POST /api/user/preview` / `POST /api/user/download`：预览 RSS / 启动下载

### 管理后台
- `POST /api/admin/login` / `POST /api/admin/logout`：管理员登录/退出
- `GET /api/admin/tenants` / `POST /api/admin/tenants`：租户列表/新增租户
- `PATCH /api/admin/tenant/<tenant_key>/status`：启用/停用租户
- `DELETE /api/admin/tenant/<tenant_key>`：软删除租户（默认）
- `DELETE /api/admin/tenant/<tenant_key>?hard=true`：硬删除租户
- `GET /api/admin/tenant/<tenant_key>/audits`：查看租户审计日志
- `POST /api/admin/migrations/legacy-history`：迁移 `data/history.json` 到数据库

## 注意

- 请确保 RSS 链接与下载行为符合你的站点规则和当地法律。
- 生产部署建议把 `data/app.db` 与日志目录挂载到持久化存储。
- 如需使用旧资讯机器人功能，可手动运行 `python news_app.py`。
