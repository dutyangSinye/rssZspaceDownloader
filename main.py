# -*- coding: utf-8 -*-
"""PT RSS 下载器 + 资讯机器人 Web 应用"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from typing import Dict, List, Optional

from flask import Flask, jsonify, render_template, request
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.logging_config import setup_logging
from config.settings import Settings
from robot.ai_service import AIService, AIServiceError
from robot.article_manager import ArticleManager
from robot.lark_bot import LarkBot
from robot.news_collector import NewsCollector
from robot.toutiao_publisher import ToutiaoPublisher
from services.download_service import DownloadManager as DownloadService
from services.transmission_client import TransmissionClient
from utils.image_utils import ImageSearcher, embed_images

logger = setup_logging("app")

app = Flask(
    __name__,
    template_folder=str(Settings.BASE_DIR / "web" / "templates"),
    static_folder=str(Settings.BASE_DIR / "web" / "static"),
)
app.config["JSON_AS_ASCII"] = False

lark_bot = None
if Settings.LARK_APP_ID and Settings.LARK_APP_SECRET:
    lark_bot = LarkBot(Settings.LARK_APP_ID, Settings.LARK_APP_SECRET, Settings.LARK_RECEIVE_TYPE)
    lark_bot.run_in_background()
    logger.info("飞书机器人已启动，接收类型: %s", Settings.LARK_RECEIVE_TYPE)
else:
    logger.warning("未配置飞书机器人，跳过初始化")


def lark_notify(msg: str):
    if lark_bot and Settings.LARK_RECEIVE_ID:
        lark_bot.send_to_chat(Settings.LARK_RECEIVE_ID, msg)


download_service = DownloadService(str(Settings.DATA_DIR / "history.json"), notification_callback=lark_notify)
article_manager = ArticleManager()
transmission_client = TransmissionClient()
ai_service: Optional[AIService] = None
ai_service_init_error = ""

try:
    ai_service = AIService()
except AIServiceError as e:
    ai_service_init_error = str(e)
    logger.warning("AI 服务未就绪: %s", e)


NEWS_CATEGORIES: Dict[str, Dict] = {
    "ai": {
        "label": "人工智能",
        "keywords": ["人工智能", "AI", "大模型", "智能体", "机器学习", "AIGC"],
        "hashtags": ["人工智能", "大模型", "科技资讯", "行业观察"],
    },
    "internet": {
        "label": "互联网产品",
        "keywords": ["互联网", "App", "产品", "平台", "用户增长", "社交媒体"],
        "hashtags": ["互联网", "产品动态", "平台生态", "行业观察"],
    },
    "finance": {
        "label": "商业财经",
        "keywords": ["财经", "融资", "上市", "资本市场", "业绩", "投资"],
        "hashtags": ["商业财经", "市场动态", "产业趋势", "投资观察"],
    },
    "industry": {
        "label": "产业制造",
        "keywords": ["制造业", "工业", "供应链", "自动化", "工厂", "产业升级"],
        "hashtags": ["产业制造", "工业升级", "供应链", "行业资讯"],
    },
    "policy": {
        "label": "政策与市场",
        "keywords": ["政策", "监管", "标准", "产业政策", "市场", "试点"],
        "hashtags": ["政策解读", "市场观察", "行业趋势", "资讯速览"],
    },
    "construction_robot": {
        "label": "建筑机器人",
        "keywords": ["建筑机器人", "施工机器人", "砌筑机器人", "巡检机器人", "工地机器人"],
        "hashtags": ["建筑机器人", "智能建造", "工程科技", "行业资讯"],
    },
    "smart_construction": {
        "label": "智能施工",
        "keywords": ["智能施工", "智慧工地", "数字工地", "施工自动化", "BIM"],
        "hashtags": ["智能施工", "智慧工地", "工程数字化", "产业升级"],
    },
}
DEFAULT_NEWS_CATEGORIES = ["ai"]


def resolve_news_categories(categories: Optional[List[str]]) -> tuple[List[str], List[str], List[str], List[str]]:
    category_ids = []
    for cid in categories or []:
        c = str(cid).strip()
        if c and c in NEWS_CATEGORIES and c not in category_ids:
            category_ids.append(c)
    if not category_ids:
        category_ids = list(DEFAULT_NEWS_CATEGORIES)

    labels = [NEWS_CATEGORIES[cid]["label"] for cid in category_ids]
    keywords: List[str] = []
    hashtags: List[str] = []
    for cid in category_ids:
        for kw in NEWS_CATEGORIES[cid].get("keywords", []):
            if kw not in keywords:
                keywords.append(kw)
        for tag in NEWS_CATEGORIES[cid].get("hashtags", []):
            if tag not in hashtags:
                hashtags.append(tag)
    return category_ids, labels, keywords, hashtags


def filter_news_by_keywords(news_list: List[Dict], keywords: List[str]) -> List[Dict]:
    if not keywords:
        return news_list
    lowered = [k.lower() for k in keywords if k]
    if not lowered:
        return news_list

    filtered = []
    for item in news_list:
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        if any(k in text for k in lowered):
            filtered.append(item)
    return filtered


class DownloadManagerCompat:
    def __init__(self):
        self.current_task: Optional[str] = None
        self.lock = Lock()

    @property
    def task_history(self):
        return download_service.task_history

    @property
    def added_urls(self):
        return download_service.added_urls

    @property
    def downloaded_titles(self):
        return download_service.downloaded_titles

    def is_title_downloaded(self, title: str) -> bool:
        return download_service.is_title_downloaded(title)

    def add_to_history(self, task_result):
        download_service.add_to_history(task_result)

    def get_history(self, limit=20):
        return download_service.get_history(limit)

    def is_running(self) -> bool:
        with self.lock:
            return self.current_task is not None

    def set_progress(self, task_id, data):
        download_service.set_progress(task_id, data)

    def get_progress(self, task_id):
        return download_service.get_progress(task_id)


download_manager = DownloadManagerCompat()


class NewsRobot:
    def __init__(self):
        self.lock = Lock()
        self.reset()
        self.logs = []

    def reset(self):
        with self.lock:
            self.status = "idle"
            self.news_list = []
            self.analysis = ""
            self.article = ""
            self.article_with_images = ""
            self.publish_result = ""
            self.current_task_id = None
            self.news_categories = list(DEFAULT_NEWS_CATEGORIES)
            self.news_category_labels = [NEWS_CATEGORIES[cid]["label"] for cid in self.news_categories]
            self.news_category = self.news_categories[0]
            self.news_category_label = "、".join(self.news_category_labels)

    def set_status(self, status: str):
        with self.lock:
            self.status = status

    def get_status(self) -> str:
        with self.lock:
            return self.status

    def add_log(self, msg: str):
        with self.lock:
            self.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
            if len(self.logs) > 200:
                self.logs = self.logs[-100:]

    def get_logs(self, limit: int = 50):
        with self.lock:
            return self.logs[-limit:]

    def snapshot(self):
        with self.lock:
            return {
                "status": self.status,
                "news": list(self.news_list),
                "analysis": self.analysis,
                "article": self.article,
                "article_with_images": self.article_with_images,
                "publish_result": self.publish_result,
                "current_task_id": self.current_task_id,
                "news_categories": list(self.news_categories),
                "news_category_labels": list(self.news_category_labels),
                "news_category": self.news_category,
                "news_category_label": self.news_category_label,
            }


news_robot = NewsRobot()


def ensure_ai_service() -> AIService:
    if ai_service is None:
        raise AIServiceError(ai_service_init_error or "AI 服务未初始化")
    return ai_service


def collect_news(categories: Optional[List[str]] = None):
    category_ids, category_labels, category_keywords, _ = resolve_news_categories(categories)
    category_label_text = "、".join(category_labels)
    news_robot.add_log(f"开始采集资讯，类型：{category_label_text}")
    collector = NewsCollector()
    query_keywords = category_keywords or Settings.COLLECT_KEYWORDS
    collector.set_keywords(query_keywords)
    news_list = collector.collect_all()
    filtered_news = filter_news_by_keywords(news_list, category_keywords)
    if filtered_news:
        news_robot.add_log(f"按类型筛选后保留 {len(filtered_news)} 条")
        news_list = filtered_news
    elif news_list:
        news_robot.add_log("筛选后无命中，回退为原始采集结果")

    if not news_list:
        news_robot.add_log("真实采集为空，使用兜底示例数据")
        news_list = [
            {
                "title": f"{category_label_text}应用持续升温",
                "source": "系统示例",
                "url": "https://example.com/1",
                "summary": f"围绕{category_label_text}的相关场景，落地节奏持续提升。",
                "time": datetime.now().isoformat(),
            },
            {
                "title": f"多家企业加码{category_label_text}",
                "source": "系统示例",
                "url": "https://example.com/2",
                "summary": f"产业链企业持续投入{category_label_text}相关能力建设。",
                "time": datetime.now().isoformat(),
            },
        ]

    with news_robot.lock:
        news_robot.news_list = news_list
        news_robot.news_categories = category_ids
        news_robot.news_category_labels = category_labels
        news_robot.news_category = category_ids[0]
        news_robot.news_category_label = category_label_text
    news_robot.add_log(f"采集完成，共 {len(news_list)} 条")
    return news_list


def run_full_news_task(task_id=None, categories: Optional[List[str]] = None):
    category_ids, category_labels, _, category_tags = resolve_news_categories(categories)
    category_label_text = "、".join(category_labels)
    with news_robot.lock:
        news_robot.current_task_id = task_id or f"task_{int(time.time())}"
        news_robot.news_categories = category_ids
        news_robot.news_category_labels = category_labels
        news_robot.news_category = category_ids[0]
        news_robot.news_category_label = category_label_text

    try:
        service = ensure_ai_service()

        news_robot.set_status("collecting")
        collect_news(category_ids)

        news_robot.set_status("analyzing")
        news_robot.add_log("开始分析资讯")
        analysis = service.analyze_news(news_robot.snapshot()["news"], topic_label=category_label_text)
        with news_robot.lock:
            news_robot.analysis = analysis

        news_robot.set_status("writing")
        news_robot.add_log("开始生成文章")
        article = service.write_article(
            news_robot.snapshot()["news"],
            analysis,
            topic_label=category_label_text,
            topic_tags=category_tags,
        )
        with news_robot.lock:
            news_robot.article = article

        news_robot.set_status("evaluating")
        news_robot.add_log("开始评估文章")
        evaluation = service.evaluate_article(article)

        news_robot.set_status("embedding_images")
        news_robot.add_log("开始补充配图")
        searcher = ImageSearcher()
        searcher.add_news_candidates(news_robot.snapshot()["news"])
        article_with_images = embed_images(article, searcher, max_images=5)
        with news_robot.lock:
            news_robot.article_with_images = article_with_images

        article_data = {
            "id": news_robot.snapshot()["current_task_id"],
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "news_count": len(news_robot.snapshot()["news"]),
            "news_categories": news_robot.snapshot()["news_categories"],
            "news_category_labels": news_robot.snapshot()["news_category_labels"],
            "news_category": news_robot.snapshot()["news_category"],
            "news_category_label": news_robot.snapshot()["news_category_label"],
            "news": news_robot.snapshot()["news"][:10],
            "analysis": analysis,
            "article": article,
            "article_with_images": article_with_images,
            "evaluation": evaluation,
            "publish_result": news_robot.snapshot()["publish_result"],
        }
        article_manager.save_article(article_data)
        article_manager.save_preview(article_with_images)

        news_robot.add_log("资讯任务完成")
        news_robot.set_status("completed")
    except Exception as e:
        logger.exception("资讯任务失败")
        news_robot.add_log(f"任务失败: {e}")
        news_robot.set_status("error")
    finally:
        with news_robot.lock:
            news_robot.current_task_id = None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(
        {
            "transmission_connected": transmission_client.test_connection(),
            "transmission_host": Settings.TRANSMISSION_HOST,
            "running": download_manager.is_running(),
            "history_count": len(download_manager.task_history),
            "ai_ready": ai_service is not None,
            "ai_error": ai_service_init_error,
            "ai_model": ai_service.backend_label() if ai_service else Settings.OPENAI_MODEL,
        }
    )


@app.route("/api/history", methods=["GET"])
def api_history():
    limit = request.args.get("limit", 20, type=int)
    return jsonify({"success": True, "history": download_manager.get_history(limit)})


@app.route("/api/preview", methods=["POST"])
def api_preview():
    data = request.get_json() or {}
    mode = data.get("mode", "movie")
    if mode not in Settings.RSS_CONFIG:
        return jsonify({"success": False, "message": "无效模式"})

    config = Settings.RSS_CONFIG[mode]
    if not config.get("url"):
        return jsonify({"success": False, "message": f"模式 {mode} 的 RSS URL 未配置"})

    items = download_service.fetch_rss_items(mode)
    if not items:
        return jsonify({"success": False, "message": "RSS 获取失败"})

    return jsonify(
        {
            "success": True,
            "mode": mode,
            "mode_name": config["name"],
            "count": len(items),
            "items": [item.to_dict() for item in items[:50]],
        }
    )


@app.route("/api/download", methods=["POST"])
def api_download():
    if download_manager.is_running():
        return jsonify({"success": False, "message": "已有任务在执行中"})

    data = request.get_json() or {}
    mode = data.get("mode", "movie")
    keywords = data.get("keywords", "")

    if mode not in Settings.RSS_CONFIG:
        return jsonify({"success": False, "message": "无效模式"})

    config = Settings.RSS_CONFIG[mode]
    task_id = f"{mode}_{int(time.time())}"
    start_time = time.time()
    download_manager.current_task = task_id
    download_manager.set_progress(task_id, {"status": "fetching", "message": "正在获取 RSS..."})

    def run_download():
        try:
            rss_service = download_service.rss_service
            transmission = download_service.transmission

            items = rss_service.fetch_and_parse(config["url"])
            if not items:
                download_manager.set_progress(task_id, {"status": "error", "message": "RSS 获取失败或为空"})
                return

            if keywords:
                kw_list = [k.strip() for k in keywords.split() if k.strip()]
                items = rss_service.filter_by_keywords(items, kw_list)

            if not items:
                download_manager.set_progress(task_id, {"status": "error", "message": "过滤后无匹配条目"})
                return

            existing_names = transmission.get_torrent_names()
            added, skipped, failed = [], [], []
            total = len(items)

            for index, item in enumerate(items, start=1):
                title = item.title
                url = item.enclosure_url

                if url in download_manager.added_urls:
                    skipped.append(title)
                elif download_manager.is_title_downloaded(title):
                    skipped.append(title)
                    download_manager.added_urls.add(url)
                else:
                    title_lower = title.lower()
                    if any(title_lower == name or title_lower in name or name in title_lower for name in existing_names):
                        skipped.append(title)
                        download_manager.added_urls.add(url)
                    else:
                        try:
                            result = transmission.add_torrent(url, config["download_dir"])
                            if result.get("result") == "success":
                                added.append(title)
                                download_manager.added_urls.add(url)
                            elif result.get("result") == "torrent-duplicate":
                                skipped.append(title)
                                download_manager.added_urls.add(url)
                            else:
                                failed.append((title, result.get("result", "未知错误")))
                        except Exception as e:
                            failed.append((title, str(e)))

                download_manager.set_progress(
                    task_id,
                    {
                        "status": "downloading",
                        "current": index,
                        "total": total,
                        "added": len(added),
                        "skipped": len(skipped),
                        "failed": len(failed),
                        "message": f"[{index}/{total}] {title[:40]}",
                    },
                )
                time.sleep(0.3)

            result_data = {
                "task_id": task_id,
                "mode": mode,
                "mode_name": config["name"],
                "success": True,
                "statistics": {
                    "total": total,
                    "added_count": len(added),
                    "skipped_count": len(skipped),
                    "failed_count": len(failed),
                },
                "added_torrents": added,
                "skipped_torrents": skipped,
                "failed_torrents": [{"title": t, "error": e} for t, e in failed],
                "duration_seconds": round(time.time() - start_time, 1),
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            download_manager.set_progress(task_id, {"status": "completed", "result": result_data})
            download_manager.add_to_history(result_data)
        except Exception as e:
            logger.exception("下载任务失败")
            download_manager.set_progress(task_id, {"status": "error", "message": str(e)})
        finally:
            download_manager.current_task = None

    Thread(target=run_download, daemon=True).start()
    return jsonify({"success": True, "task_id": task_id})


@app.route("/api/download-one", methods=["POST"])
def api_download_one():
    data = request.get_json() or {}
    url = data.get("url")
    mode = data.get("mode", "movie")
    if not url:
        return jsonify({"success": False, "message": "URL 不能为空"})

    download_dir = Settings.RSS_CONFIG.get(mode, {}).get("download_dir", "/film")
    try:
        result = transmission_client.add_torrent(url, download_dir)
        if result.get("result") == "success":
            download_manager.added_urls.add(url)
            return jsonify({"success": True, "message": "添加成功"})
        if result.get("result") == "torrent-duplicate":
            return jsonify({"success": True, "message": "已存在"})
        return jsonify({"success": False, "message": result.get("result", "添加失败")})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/progress/<task_id>", methods=["GET"])
def api_progress(task_id):
    progress = download_manager.get_progress(task_id)
    if progress:
        return jsonify({"success": True, "progress": progress})
    return jsonify({"success": False, "message": "任务不存在"})


@app.route("/api/news/status")
def api_news_status():
    snapshot = news_robot.snapshot()
    return jsonify(
        {
            "status": snapshot["status"],
            "news_count": len(snapshot["news"]),
            "logs": news_robot.get_logs(20),
            "current_task_id": snapshot["current_task_id"],
            "news_categories": snapshot["news_categories"],
            "news_category_labels": snapshot["news_category_labels"],
            "news_category": snapshot["news_category"],
            "news_category_label": snapshot["news_category_label"],
        }
    )


@app.route("/api/news/reset", methods=["POST"])
def api_news_reset():
    news_robot.reset()
    news_robot.add_log("状态已重置")
    return jsonify({"success": True})


@app.route("/api/news/preview")
def api_news_preview():
    files = article_manager.get_previews()
    return jsonify({"success": True, "files": files, "latest": files[0] if files else None})


@app.route("/api/news")
def api_news():
    snapshot = news_robot.snapshot()
    return jsonify(
        {
            "news": snapshot["news"],
            "analysis": snapshot["analysis"],
            "article": snapshot["article"],
            "article_with_images": snapshot["article_with_images"],
            "publish_result": snapshot["publish_result"],
            "news_categories": snapshot["news_categories"],
            "news_category_labels": snapshot["news_category_labels"],
            "news_category": snapshot["news_category"],
            "news_category_label": snapshot["news_category_label"],
        }
    )


@app.route("/api/news/categories")
def api_news_categories():
    return jsonify(
        {
            "success": True,
            "default": DEFAULT_NEWS_CATEGORIES,
            "categories": [{"id": cid, "label": info["label"]} for cid, info in NEWS_CATEGORIES.items()],
        }
    )


@app.route("/api/news/start", methods=["POST"])
def api_news_start():
    if ai_service is None:
        return jsonify({"success": False, "message": ai_service_init_error or "AI 服务未就绪"})
    if news_robot.get_status() not in ["idle", "completed", "error"]:
        return jsonify({"success": False, "message": "当前已有资讯任务正在运行"})
    data = request.get_json() or {}
    categories = data.get("categories")
    if not isinstance(categories, list):
        categories = [data.get("category")] if data.get("category") else list(DEFAULT_NEWS_CATEGORIES)
    category_ids, category_labels, _, _ = resolve_news_categories(categories)
    category_label_text = "、".join(category_labels)
    news_robot.reset()
    with news_robot.lock:
        news_robot.news_categories = category_ids
        news_robot.news_category_labels = category_labels
        news_robot.news_category = category_ids[0]
        news_robot.news_category_label = category_label_text
    news_robot.add_log(f"任务启动，资讯类型：{category_label_text}")
    Thread(target=run_full_news_task, kwargs={"categories": category_ids}, daemon=True).start()
    return jsonify(
        {
            "success": True,
            "news_categories": category_ids,
            "news_category_labels": category_labels,
            "news_category": category_ids[0],
            "news_category_label": category_label_text,
        }
    )


@app.route("/api/news/optimize", methods=["POST"])
def api_news_optimize():
    data = request.get_json() or {}
    article = data.get("article", news_robot.snapshot()["article"])
    if not article:
        return jsonify({"success": False, "message": "文章为空"})
    if ai_service is None:
        return jsonify({"success": False, "message": ai_service_init_error or "AI 服务未就绪"})

    news_robot.set_status("optimizing")
    news_robot.add_log("正在优化文章")

    def do_optimize():
        try:
            service = ensure_ai_service()
            evaluation = service.evaluate_article(article)
            optimized = service.optimize_article(article, evaluation)
            searcher = ImageSearcher()
            searcher.add_news_candidates(news_robot.snapshot()["news"])
            article_with_images = embed_images(optimized, searcher, max_images=5)
            with news_robot.lock:
                news_robot.article = optimized
                news_robot.article_with_images = article_with_images
            news_robot.add_log("文章优化完成")
            news_robot.set_status("completed")
        except Exception as e:
            news_robot.add_log(f"优化失败: {e}")
            news_robot.set_status("error")

    Thread(target=do_optimize, daemon=True).start()
    return jsonify({"success": True, "message": "优化任务已启动"})


@app.route("/api/news/regenerate", methods=["POST"])
def api_news_regenerate():
    snapshot = news_robot.snapshot()
    if not snapshot["news"]:
        return jsonify({"success": False, "message": "没有可用资讯"})
    if ai_service is None:
        return jsonify({"success": False, "message": ai_service_init_error or "AI 服务未就绪"})

    news_robot.set_status("writing")
    news_robot.add_log("重新生成文章")

    def do_regenerate():
        try:
            service = ensure_ai_service()
            current_snapshot = news_robot.snapshot()
            _, category_labels, _, category_tags = resolve_news_categories(current_snapshot.get("news_categories"))
            article = service.write_article(
                current_snapshot["news"],
                current_snapshot["analysis"],
                topic_label="、".join(category_labels),
                topic_tags=category_tags,
            )
            searcher = ImageSearcher()
            searcher.add_news_candidates(current_snapshot["news"])
            article_with_images = embed_images(article, searcher, max_images=5)
            with news_robot.lock:
                news_robot.article = article
                news_robot.article_with_images = article_with_images
            news_robot.add_log("文章重新生成完成")
            news_robot.set_status("completed")
        except Exception as e:
            news_robot.add_log(f"重新生成失败: {e}")
            news_robot.set_status("error")

    Thread(target=do_regenerate, daemon=True).start()
    return jsonify({"success": True, "message": "重生成任务已启动"})


@app.route("/api/news/image/replace", methods=["POST"])
def api_news_image_replace():
    data = request.get_json() or {}
    index = data.get("index")
    try:
        index = int(index)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "无效图片索引"})

    snapshot = news_robot.snapshot()
    article_html = snapshot.get("article_with_images") or snapshot.get("article") or ""
    if not article_html:
        return jsonify({"success": False, "message": "当前没有可替换的文章内容"})

    soup = BeautifulSoup(article_html, "html.parser")
    images = soup.find_all("img")
    if not images:
        return jsonify({"success": False, "message": "当前文章没有配图"})
    if index < 0 or index >= len(images):
        return jsonify({"success": False, "message": "图片索引超出范围"})

    target_img = images[index]
    current_url = (target_img.get("src") or "").strip()
    desc = (target_img.get("alt") or "").strip() or "资讯配图"
    context = soup.get_text(" ", strip=True)[:1200]

    searcher = ImageSearcher()
    searcher.add_news_candidates(snapshot.get("news", []))
    candidates = searcher.search(
        desc,
        max_results=8,
        context=context,
        exclude_urls=[current_url] if current_url else [],
    )
    if not candidates:
        return jsonify({"success": False, "message": "没有找到可替换的候选图片"})

    new_url = candidates[0]
    target_img["src"] = new_url
    updated_html = str(soup)

    with news_robot.lock:
        news_robot.article_with_images = updated_html

    news_robot.add_log(f"第 {index + 1} 张图片已替换")
    return jsonify({"success": True, "index": index, "new_url": new_url, "article_with_images": updated_html})


@app.route("/api/news/publish", methods=["POST"])
def api_news_publish():
    data = request.get_json() or {}
    title = data.get("title", "")
    content = data.get("content", news_robot.snapshot()["article_with_images"] or news_robot.snapshot()["article"])
    auto_publish = data.get("auto_publish", True)
    if not title or not content:
        return jsonify({"success": False, "message": "标题和内容不能为空"})
    if news_robot.get_status() not in ["idle", "completed", "error"]:
        return jsonify({"success": False, "message": "当前有任务正在运行，请稍后再试"})

    news_robot.set_status("publishing")
    news_robot.add_log("开始发布到头条")

    def do_publish():
        try:
            publisher = ToutiaoPublisher(
                status_callback=lambda s: news_robot.set_status(s),
                log_callback=lambda m: news_robot.add_log(m),
            )
            result = publisher.publish(title, content, auto_publish)
            with news_robot.lock:
                news_robot.publish_result = result.get("message", "")
            news_robot.set_status("completed" if result.get("success") else "error")
        except Exception as e:
            with news_robot.lock:
                news_robot.publish_result = str(e)
            news_robot.add_log(f"发布失败: {e}")
            news_robot.set_status("error")

    Thread(target=do_publish, daemon=True).start()
    return jsonify({"success": True, "message": "发布任务已启动"})


@app.route("/api/news/history")
def api_news_history():
    return jsonify({"success": True, "history": article_manager.load_history()})


@app.route("/api/news/history/<article_id>")
def api_news_history_detail(article_id):
    article = article_manager.get_article(article_id)
    if article:
        return jsonify({"success": True, "article": article})
    return jsonify({"success": False, "message": "文章不存在"})


@app.route("/api/config", methods=["POST"])
def api_config():
    data = request.get_json() or {}
    Settings.TOUTIAO_COOKIE = data.get("cookie", Settings.TOUTIAO_COOKIE)
    return jsonify({"success": True})


@app.route("/preview/<filename>")
def serve_preview(filename):
    safe_name = Path(filename).name
    filepath = Settings.DATA_DIR / "previews" / safe_name
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return "Preview not found", 404


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("PT RSS Downloader + 资讯机器人")
    logger.info("访问地址: http://localhost:%s", Settings.PORT)
    logger.info("=" * 50)
    app.run(host=Settings.HOST, port=Settings.PORT, debug=Settings.DEBUG, threaded=True, use_reloader=False)
