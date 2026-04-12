import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

import requests

from config.logging_config import setup_logging
from services.rss_parser import RSSParser
from services.tenant_store import TenantStore
from services.transmission_client import TransmissionClient

logger = setup_logging("multi_tenant_download")


@dataclass
class RSSItem:
    title: str
    enclosure_url: str
    link: str = ""
    summary: str = ""
    chinese_name: str = ""
    guid: str = ""

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "enclosure_url": self.enclosure_url,
            "link": self.link,
            "summary": self.summary,
            "chinese_name": self.chinese_name,
            "guid": self.guid,
        }


class MultiTenantDownloadService:
    def __init__(self, tenant_store: TenantStore):
        self.tenant_store = tenant_store
        self.rss_parser = RSSParser()

    def _tenant_client(self, tenant_key: str) -> TransmissionClient:
        config = self.tenant_store.get_tenant_config(tenant_key)
        tr = config["transmission"]
        return TransmissionClient(
            host=tr["host"],
            username=tr["username"],
            password=tr["password"],
            request_timeout=int(tr["request_timeout"]),
            max_retries=int(tr["max_retries"]),
            retry_delay=int(tr["retry_delay"]),
        )

    def _mode_config(self, tenant_key: str, mode: str) -> Dict:
        config = self.tenant_store.get_tenant_config(tenant_key)
        mode_cfg = (config.get("rss_modes") or {}).get(mode)
        if not mode_cfg:
            raise ValueError(f"无效模式: {mode}")
        if not int(mode_cfg.get("enabled", 1)):
            raise ValueError(f"模式已禁用: {mode}")
        if not str(mode_cfg.get("rss_url", "")).strip():
            raise ValueError(f"模式 {mode} 未配置 RSS URL")
        return mode_cfg

    def fetch_rss_items(self, tenant_key: str, mode: str) -> List[RSSItem]:
        mode_cfg = self._mode_config(tenant_key, mode)
        client = self._tenant_client(tenant_key)
        response = requests.get(mode_cfg["rss_url"], timeout=client.request_timeout)
        response.encoding = "utf-8"
        parsed = self.rss_parser.parse(response.text)
        return [RSSItem(**item) for item in parsed]

    def add_single_torrent(self, tenant_key: str, mode: str, url: str, title: str = "") -> Dict:
        config = self.tenant_store.get_tenant_config(tenant_key)
        mode_cfg = (config.get("rss_modes") or {}).get(mode)
        if not mode_cfg:
            return {"success": False, "message": "无效模式"}

        transmission = self._tenant_client(tenant_key)
        result = transmission.add_torrent(url, mode_cfg.get("download_dir", "/downloads"))
        rpc_result = result.get("result")
        if rpc_result in {"success", "torrent-duplicate"}:
            self.tenant_store.remember_downloaded(tenant_key, title or url, url)
            return {"success": True, "message": "添加成功" if rpc_result == "success" else "已存在"}
        return {"success": False, "message": rpc_result or "添加失败"}

    def execute_download(
        self,
        tenant_key: str,
        mode: str,
        keywords: Optional[List[str]] = None,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable[[Dict], None]] = None,
    ) -> Dict:
        mode_cfg = self._mode_config(tenant_key, mode)
        transmission = self._tenant_client(tenant_key)

        items = self.fetch_rss_items(tenant_key, mode)
        if not items:
            raise ValueError("RSS 获取失败或为空")

        if keywords:
            raw_items = [item.to_dict() for item in items]
            filtered = self.rss_parser.filter_by_keywords(raw_items, keywords)
            items = [RSSItem(**x) for x in filtered]
            if not items:
                raise ValueError("过滤后无匹配条目")

        existing_names = transmission.get_torrent_names()
        added: List[str] = []
        skipped: List[str] = []
        failed: List[Dict] = []
        in_batch_urls = set()
        total = len(items)
        start_ts = time.time()

        for idx, item in enumerate(items, start=1):
            title = item.title
            url = item.enclosure_url

            if not url or url in in_batch_urls:
                skipped.append(title or "未知标题")
                continue
            in_batch_urls.add(url)

            if self.tenant_store.is_downloaded(tenant_key, title, url):
                skipped.append(title)
                if progress_callback:
                    progress_callback(self._build_progress(idx, total, added, skipped, failed, title))
                continue

            title_lower = title.lower()
            if any(title_lower == name or title_lower in name or name in title_lower for name in existing_names):
                skipped.append(title)
                self.tenant_store.remember_downloaded(tenant_key, title, url)
                if progress_callback:
                    progress_callback(self._build_progress(idx, total, added, skipped, failed, title))
                continue

            try:
                rpc_result = transmission.add_torrent(url, mode_cfg.get("download_dir", "/downloads")).get("result")
                if rpc_result == "success":
                    added.append(title)
                    self.tenant_store.remember_downloaded(tenant_key, title, url)
                elif rpc_result == "torrent-duplicate":
                    skipped.append(title)
                    self.tenant_store.remember_downloaded(tenant_key, title, url)
                else:
                    failed.append({"title": title, "error": rpc_result or "未知错误"})
            except Exception as exc:
                logger.exception("下载项处理失败")
                failed.append({"title": title, "error": str(exc)})

            if progress_callback:
                progress_callback(self._build_progress(idx, total, added, skipped, failed, title))
            time.sleep(0.2)

        payload = {
            "task_id": task_id or "",
            "tenant_key": tenant_key,
            "mode": mode,
            "mode_name": mode_cfg.get("mode_name", mode),
            "success": True,
            "statistics": {
                "total": total,
                "added_count": len(added),
                "skipped_count": len(skipped),
                "failed_count": len(failed),
            },
            "added_torrents": added,
            "skipped_torrents": skipped,
            "failed_torrents": failed,
            "duration_seconds": round(time.time() - start_ts, 1),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.tenant_store.save_history(tenant_key, task_id or "", payload)
        return payload

    @staticmethod
    def _build_progress(
        current: int,
        total: int,
        added: List[str],
        skipped: List[str],
        failed: List[Dict],
        current_title: str,
    ) -> Dict:
        return {
            "status": "downloading",
            "current": current,
            "total": total,
            "added": len(added),
            "skipped": len(skipped),
            "failed": len(failed),
            "message": f"[{current}/{total}] {current_title[:60]}",
        }
