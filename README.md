# rssZspaceDownloader

一个面向通用 PT 站点 RSS 的自动化下载与资讯处理工具。  
你可以把不同 PT 站点提供的 RSS 订阅链接接入进来，按分类抓取种子并自动下发到 Transmission，同时保留 AI 资讯采集与内容处理能力。

## 功能概览

### 通用 PT RSS 下载
- 支持多个 RSS 订阅源（如电影、剧集、其他分类）
- 自动解析 RSS 条目并提取下载信息
- 自动添加任务到 Transmission
- 支持关键词过滤、去重与下载记录管理

### AI 资讯处理（可选）
- 多来源新闻采集
- AI 分析与摘要生成
- 自动生成文章草稿
- 自动配图与发布流程（按当前配置启用）

## 快速开始

### 1. 安装依赖

```bash
# 首次运行会自动创建虚拟环境并安装依赖
start.bat
```

或手动安装：

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置环境变量

复制配置模板：

```bash
copy .env.example .env
```

常用配置项（示例）：
- `TRANSMISSION_HOST` / `TRANSMISSION_PORT` / `TRANSMISSION_USERNAME` / `TRANSMISSION_PASSWORD`
- `RSS_MOVIE_URL` / `RSS_TV_URL` / `RSS_ADULT_URL`（可替换为你自己的 PT 站 RSS 链接）
- `OLLAMA_URL` / `OLLAMA_MODEL`（如启用 AI 功能）

### 3. 运行项目

```bash
start.bat
```

## 使用说明

1. 在 `.env` 中填入可访问的 PT 站 RSS 链接。
2. 确保 Transmission 服务可连接并账号权限正常。
3. 启动后程序会按配置拉取 RSS，过滤后自动下发下载。

## 注意事项

- 请确保 RSS 链接与下载行为符合你所在站点和地区的使用规则。
- 建议先在小范围关键词下测试，确认筛选策略后再长期运行。
- 含敏感信息的 `.env` 不要提交到公开仓库。
