# rssZspaceDownloader

从 M-TEAM PT 站 RSS 订阅获取种子并自动添加到 Transmission 下载，同时支持 AI 驱动的资讯采集、分析和自动发布到头条号。

## 功能特性

### M-TEAM 下载器
- RSS 订阅解析（电影/电视剧/成人内容）
- 自动添加到 Transmission BT 客户端
- 关键词过滤、去重、进度追踪

### AI 资讯机器人
- 多源新闻采集（36氪、新浪、百度等 8 个来源）
- AI 分析行业趋势（Ollama 本地部署）
- 自动撰写今日头条风格文章
- 文章质量评估与优化
- 自动搜索并嵌入配图
- 自动发布到头条号

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

### 2. 配置

复制 `.env.example` 为 `.env` 并填写配置：

```bash
copy .env.example .env
```

必填配置项：
- `TRANSMISSION_HOST` - Transmission BT 客户端地址
- `RSS_MOVIE_URL` / `RSS_TV_URL` / `RSS_ADULT_URL` - M-TEAM RSS 订阅链接
- `OLLAMA_URL` / `OLLAMA_MODEL` - AI 服务地址和模型

### 3. 运行

```bash
start.bat
# 或
python main.py
```

访问 http://localhost:5000

## 项目结构

```
mteam-downloader/
├── main.py                 # 主入口（Flask 应用）
├── config/                 # 配置模块
│   ├── settings.py         # 配置管理（.env 加载）
│   └── logging_config.py   # 日志配置
├── services/               # 服务层
│   ├── download_service.py # 下载管理器
│   ├── rss_parser.py       # RSS 解析器
│   └── transmission_client.py # Transmission 客户端
├── robot/                  # 资讯机器人
│   ├── news_collector.py   # 新闻爬虫
│   ├── ai_service.py       # AI 服务
│   ├── toutiao_publisher.py # 头条发布器
│   └── article_manager.py  # 文章管理
├── utils/                  # 工具模块
│   └── image_utils.py      # 图片搜索和嵌入
├── web/                    # Web 前端
│   └── templates/
│       └── index.html      # 单页应用
├── data/                   # 数据目录
│   ├── article_history/    # 文章历史
│   ├── previews/           # HTML 预览
│   ├── images/             # 图片缓存
│   └── browser_data/       # 浏览器配置
├── logs/                   # 日志目录
├── .env                    # 配置文件
└── requirements.txt        # 依赖清单
```

## 配置说明

### Transmission BT 客户端
确保 Transmission 已启用 RPC 接口：
```json
{
    "rpc-enabled": true,
    "rpc-port": 9091,
    "rpc-authentication-required": false
}
```

### Ollama AI 服务
安装 Ollama 并拉取模型：
```bash
ollama pull qwen2.5:7b
```

### M-TEAM RSS
登录 M-TEAM 后在 RSS 订阅页面获取专属链接。

## 技术栈

- **后端**: Python 3.8+, Flask
- **AI**: Ollama (Qwen 模型)
- **爬虫**: requests, BeautifulSoup4, lxml
- **浏览器自动化**: Playwright
- **前端**: 原生 HTML/CSS/JS

## 注意事项

- 首次发布到头条号需要扫码登录
- RSS 链接包含个人认证信息，请勿泄露
- 建议定期清理 `data/` 目录下的历史文件
