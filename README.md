# rss-downloader

多租户 RSS 自动下载系统（前后台分离，配置入库）。
支持TRANSMISSION、QB下载工具

## 功能

- 用户前台与管理员后台分离登录。
- 多租户隔离：配置、下载历史、去重数据互相独立。
- 所有租户配置（Transmission\QB + RSS 模式）写入 SQLite 数据库。
- 租户可自注册；管理员可新增、启用、停用、删除租户。
- 支持 RSS 预览、单条下载、批量下载、下载进度与历史查看。

## 项目结构（核心）

- `main.py`：主入口（Flask）
- `services/tenant_store.py`：租户与配置存储
- `services/multi_tenant_download_service.py`：RSS 抓取与下载执行
- `services/transmission_client.py`：Transmission 客户端
- `web/templates/user_*.html`：用户前台页面
- `web/templates/admin_*.html`：管理后台页面

## 快速开始

1. 安装依赖

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

2. 准备环境变量

```bash
copy .env.example .env
```

3. 启动

```bash
python main.py
```

4. 访问

- 用户前台：`http://localhost:5000/user/login`
- 管理后台：`http://localhost:5000/admin/login`

## Docker

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f
```

停止：

```bash
docker compose down
```

## 默认账号（首次建库）

- 管理员：`admin / admin`
- 默认租户用户：`admin / admin`

## 数据说明

- 业务配置存储在 `data/app.db`。
- `.env` 中 Transmission/RSS 仅用于默认租户首次种子初始化。
- 数据库结构不变时，已有租户配置不会因重启覆盖。

## 主要接口

### 用户端
- `POST /api/user/register`
- `POST /api/user/login`
- `GET /api/user/config`
- `PUT /api/user/config`
- `POST /api/user/preview`
- `POST /api/user/download`
- `POST /api/user/download-one`

### 管理端
- `POST /api/admin/login`
- `GET /api/admin/tenants`
- `POST /api/admin/tenants`
- `PATCH /api/admin/tenant/<tenant_key>/status`
- `DELETE /api/admin/tenant/<tenant_key>`
- `GET /api/admin/tenant/<tenant_key>/audits`
