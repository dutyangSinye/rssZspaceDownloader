# M-TEAM 下载服务层
import logging
import time
from threading import Lock
from typing import Dict, List, Optional, Set
from datetime import datetime
from config.settings import Settings
from config.logging_config import setup_logging
from services.rss_parser import RSSParser
from services.transmission_client import TransmissionClient

logger = setup_logging("download_service")


class RSSItem:
    """RSS 条目数据类"""

    def __init__(
        self,
        title: str,
        enclosure_url: str,
        link: str = "",
        summary: str = "",
        chinese_name: str = "",
        guid: str = "",
    ):
        self.title = title
        self.enclosure_url = enclosure_url
        self.link = link
        self.summary = summary
        self.chinese_name = chinese_name
        self.guid = guid

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "chinese_name": self.chinese_name,
            "enclosure_url": self.enclosure_url,
            "link": self.link,
            "summary": self.summary,
            "guid": self.guid,
        }


class RSSService:
    """RSS Feed 获取和解析服务"""

    def __init__(self):
        self.parser = RSSParser()

    def fetch_and_parse(self, url: str) -> List[RSSItem]:
        """获取并解析 RSS Feed"""
        if not url:
            logger.warning("RSS URL 为空")
            return []

        try:
            import requests
            resp = requests.get(url, timeout=Settings.REQUEST_TIMEOUT)
            resp.encoding = "utf-8"
            items = self.parser.parse(resp.text)
            return [RSSItem(**item) for item in items]
        except Exception as e:
            logger.error(f"获取 RSS 失败: {e}")
            return []

    def filter_by_keywords(self, items: List[RSSItem], keywords: List[str]) -> List[RSSItem]:
        """关键词过滤"""
        dicts = [item.to_dict() for item in items]
        filtered = RSSParser.filter_by_keywords(dicts, keywords)
        return [RSSItem(**d) for d in filtered]

    @staticmethod
    def truncate_title(title: str, max_len: int = 50) -> str:
        return RSSParser.truncate_title(title, max_len)


class DownloadManager:
    """下载任务管理器"""

    def __init__(self, history_file: str = None, notification_callback=None):
        from pathlib import Path
        self.history_file = Path(history_file or Settings.DATA_DIR / "history.json")
        self.rss_service = RSSService()
        self.transmission = TransmissionClient()
        self.lock = Lock()
        self.notification_callback = notification_callback

        self.current_task: Optional[str] = None
        self.task_history = self._load_history()
        self.progress = {}
        self.added_urls: Set[str] = set()
        self.downloaded_titles: Set[str] = self._load_downloaded_titles()

    def _load_history(self) -> List[Dict]:
        import json
        try:
            if self.history_file.exists():
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载历史记录失败: {e}")
        return []

    def _save_history(self):
        import json
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.task_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存历史记录失败: {e}")

    def _load_downloaded_titles(self) -> Set[str]:
        titles = set()
        for task in self.task_history:
            if task.get("success") and task.get("added_torrents"):
                for title in task.get("added_torrents", []):
                    titles.add(title)
        return titles

    def is_title_downloaded(self, title: str) -> bool:
        return title in self.downloaded_titles

    def add_to_history(self, task_result: Dict):
        self.task_history.insert(0, task_result)
        if len(self.task_history) > 50:
            self.task_history.pop()

        if task_result.get("success") and task_result.get("added_torrents"):
            for title in task_result.get("added_torrents", []):
                self.downloaded_titles.add(title)

        self._save_history()

    def get_history(self, limit: int = 20) -> List[Dict]:
        return self.task_history[:limit]

    def is_running(self) -> bool:
        with self.lock:
            return self.current_task is not None

    def set_progress(self, task_id: str, data: Dict):
        with self.lock:
            self.progress[task_id] = data

    def get_progress(self, task_id: str) -> Optional[Dict]:
        with self.lock:
            return self.progress.get(task_id)

    def fetch_rss_items(self, mode: str) -> List[RSSItem]:
        """获取 RSS 条目"""
        if mode not in Settings.RSS_CONFIG:
            logger.error(f"未知的下载模式: {mode}")
            return []

        config = Settings.RSS_CONFIG[mode]
        if not config.get("url"):
            logger.warning(f"模式 {mode} 的 RSS URL 未配置，请在 .env 中设置 RSS_{mode.upper()}_URL")
            return []

        logger.info(f"正在获取 RSS: {config['name']}")
        items = self.rss_service.fetch_and_parse(config["url"])
        logger.info(f"获取到 {len(items)} 条 RSS 条目")
        return items

    def execute_download(self, mode: str, keywords: List[str] = None) -> Dict:
        """执行下载任务"""
        if mode not in Settings.RSS_CONFIG:
            return {"success": False, "message": f"未知的下载模式: {mode}"}

        config = Settings.RSS_CONFIG[mode]

        items = self.fetch_rss_items(mode)
        if not items:
            return {"success": False, "message": "未获取到 RSS 条目"}

        if keywords:
            items = self.rss_service.filter_by_keywords(items, keywords)
            logger.info(f"关键词过滤后剩余 {len(items)} 条")

        existing_names = self.transmission.get_torrent_names()
        logger.info(f"Transmission 中已有 {len(existing_names)} 个种子")

        added, skipped, failed = [], [], []

        for item in items:
            title = item.title
            url = item.enclosure_url

            if url in self.added_urls:
                skipped.append(title)
                continue

            if self.is_title_downloaded(title):
                skipped.append(title)
                self.added_urls.add(url)
                continue

            title_lower = title.lower()
            if any(
                title_lower == name or title_lower in name or name in title_lower
                for name in existing_names
            ):
                skipped.append(title)
                self.added_urls.add(url)
                continue

            try:
                result = self.transmission.add_torrent(url, config["download_dir"])
                if result.get("result") == "success":
                    added.append(title)
                    self.added_urls.add(url)
                    logger.info(f"添加成功: {self.rss_service.truncate_title(title)}")
                elif result.get("result") == "torrent-duplicate":
                    skipped.append(title)
                    self.added_urls.add(url)
                else:
                    failed.append((title, result.get("result", "未知错误")))
                    logger.warning(f"添加失败: {self.rss_service.truncate_title(title)}")
            except Exception as e:
                failed.append((title, str(e)))
                logger.error(f"添加异常: {self.rss_service.truncate_title(title)} - {e}")

            time.sleep(0.5)

        result = {
            "success": True,
            "mode": mode,
            "mode_name": config["name"],
            "statistics": {
                "total": len(items),
                "added": len(added),
                "skipped": len(skipped),
                "failed": len(failed),
            },
            "added_torrents": added,
            "skipped_torrents": skipped,
            "failed_torrents": [{"title": t, "error": e} for t, e in failed],
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        self.add_to_history(result)
        
        # 发送飞书通知
        if self.notification_callback:
            try:
                msg = f"📥 下载任务完成\n模式: {config['name']}\n总数: {len(items)}\n新增: {len(added)}\n跳过: {len(skipped)}\n失败: {len(failed)}"
                if added:
                    msg += f"\n\n新增资源:\n" + "\n".join([f"• {self.rss_service.truncate_title(t, 30)}" for t in added[:5]])
                    if len(added) > 5:
                        msg += f"\n... 及其他 {len(added)-5} 个资源"
                self.notification_callback(msg)
            except Exception as e:
                logger.error(f"发送通知失败: {e}")
                
        return result
