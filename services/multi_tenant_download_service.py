import time
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional
from uuid import uuid4

import requests

from config.logging_config import setup_logging
from services.downloader_client_factory import create_downloader_client
from services.rss_parser import RSSParser
from services.tenant_store import TenantStore

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

    @staticmethod
    def _normalize_torrent_name(name: str) -> str:
        return re.sub(r"\s+", " ", str(name or "").strip()).lower()

    @staticmethod
    def _extract_media_code(name: str) -> str:
        match = re.search(r"[A-Za-z]{2,10}-\d{2,6}", str(name or ""))
        return match.group(0).upper() if match else ""

    def _confirm_qb_added(
        self,
        downloader_client,
        title: str,
        known_names: set,
        max_checks: int = 8,
        delay_seconds: float = 1.2,
    ) -> bool:
        target_name = self._normalize_torrent_name(title)
        target_code = self._extract_media_code(title)
        baseline = {self._normalize_torrent_name(x) for x in (known_names or set()) if str(x or "").strip()}

        for idx in range(max_checks):
            current_raw = downloader_client.get_torrent_names() or set()
            current = {self._normalize_torrent_name(x) for x in current_raw if str(x or "").strip()}
            new_names = current - baseline

            if target_name and target_name in new_names:
                return True
            if target_code and any(target_code in str(name or "").upper() for name in new_names):
                return True
            # Some trackers expose localized RSS titles while qB stores original names.
            # If code is unavailable, treat any new queue item as a successful enqueue.
            if not target_code and new_names:
                return True
            if not target_name and not target_code and new_names:
                return True

            if idx < max_checks - 1:
                time.sleep(delay_seconds)

        return False

    def _add_torrent_with_qb_retry(
        self,
        downloader_client,
        downloader_type: str,
        title: str,
        enclosure_url: str,
        download_dir: str,
        known_names: set,
        max_attempts: int = 2,
    ) -> str:
        if downloader_type != "qbittorrent":
            return downloader_client.add_torrent(enclosure_url, download_dir).get("result")

        baseline = {self._normalize_torrent_name(x) for x in (known_names or set()) if str(x or "").strip()}
        attempts = max(1, int(max_attempts))
        last_result = ""
        for attempt in range(1, attempts + 1):
            last_result = downloader_client.add_torrent(enclosure_url, download_dir).get("result") or ""
            if last_result == "torrent-duplicate":
                return "success" if attempt > 1 else "torrent-duplicate"
            if last_result != "success":
                return last_result

            if self._confirm_qb_added(downloader_client, title or "", baseline):
                return "success"

            logger.warning(
                "qB add success but not confirmed in queue, retrying (%s/%s), title=%s",
                attempt,
                attempts,
                (title or "")[:120],
            )
            if attempt >= attempts:
                return "qb-unconfirmed"

            current_names = {
                self._normalize_torrent_name(name)
                for name in (downloader_client.get_torrent_names() or set())
                if str(name or "").strip()
            }
            baseline |= current_names
            time.sleep(1.0)

        return last_result or "qb-unconfirmed"

    @staticmethod
    def _pick_downloader(config: Dict, downloader_id: Optional[str] = None) -> Dict:
        downloaders = config.get("downloaders") or []
        active_id = str(config.get("active_downloader_id") or "").strip()
        requested_id = str(downloader_id or "").strip()
        target_id = requested_id or active_id
        for item in downloaders:
            if str((item or {}).get("id") or "") == target_id:
                return item
        if downloaders:
            return downloaders[0]
        return config.get("transmission") or {}

    def _tenant_client(self, tenant_key: str, downloader_id: Optional[str] = None):
        config = self.tenant_store.get_tenant_config(tenant_key)
        tr = self._pick_downloader(config, downloader_id=downloader_id)
        return create_downloader_client(tr)

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

    def fetch_rss_items(self, tenant_key: str, mode: str, downloader_id: Optional[str] = None) -> List[RSSItem]:
        mode_cfg = self._mode_config(tenant_key, mode)
        client = self._tenant_client(tenant_key, downloader_id=downloader_id)
        rss_url = str(mode_cfg["rss_url"]).strip()
        rss_text = self._fetch_rss_text(rss_url, client.request_timeout, client.max_retries, client.retry_delay)
        parsed = self.rss_parser.parse(rss_text)
        return [RSSItem(**item) for item in parsed]

    @staticmethod
    def _candidate_rss_urls(url: str) -> List[str]:
        raw = (url or "").strip()
        if not raw:
            return []

        urls: List[str] = [raw]
        # Some RSS servers may reset plain HTTP connections; prefer HTTPS when possible.
        if raw.startswith("http://"):
            https_url = "https://" + raw[len("http://") :]
            urls = [https_url, raw] if https_url != raw else [raw]
        return urls

    @staticmethod
    def _rss_headers() -> Dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
            "Connection": "close",
        }

    def _fetch_rss_text(self, rss_url: str, timeout: int, max_retries: int, retry_delay: int) -> str:
        candidates = self._candidate_rss_urls(rss_url)
        if not candidates:
            raise ValueError("RSS URL 不能为空")

        attempts = max(1, int(max_retries))
        last_exc: Optional[Exception] = None
        last_status_error = ""

        session = requests.Session()
        for url in candidates:
            for idx in range(attempts):
                try:
                    resp = session.get(url, timeout=max(3, int(timeout)), headers=self._rss_headers())
                    resp.raise_for_status()
                    if not resp.encoding:
                        resp.encoding = resp.apparent_encoding or "utf-8"
                    return resp.text
                except requests.HTTPError as exc:
                    status = getattr(exc.response, "status_code", "unknown")
                    last_status_error = f"RSS HTTP 状态异常: {status}"
                    last_exc = exc
                    if isinstance(status, int) and 400 <= status < 500:
                        break
                except requests.RequestException as exc:
                    last_exc = exc

                if idx < attempts - 1:
                    time.sleep(max(0, int(retry_delay)))

        if last_status_error:
            raise ValueError(last_status_error)
        raise ValueError(f"RSS 连接失败: {last_exc or 'unknown error'}")

    def add_single_torrent(self, tenant_key: str, mode: str, url: str, title: str = "", downloader_id: Optional[str] = None) -> Dict:
        config = self.tenant_store.get_tenant_config(tenant_key)
        mode_cfg = (config.get("rss_modes") or {}).get(mode)
        if not mode_cfg:
            return {"success": False, "message": "无效模式"}

        selected_downloader = self._pick_downloader(config, downloader_id=downloader_id)
        downloader_type = str((selected_downloader or {}).get("backend_type") or "").strip().lower()
        transmission = create_downloader_client(selected_downloader)
        task_id = f"single_{tenant_key}_{int(time.time())}_{uuid4().hex[:8]}"
        title_text = str(title or "").strip() or str(url or "").strip() or "未命名种子"
        started_at = time.time()

        def save_single_history(added: List[str], skipped: List[str], failed: List[Dict], success: bool):
            payload = {
                "task_id": task_id,
                "tenant_key": tenant_key,
                "mode": mode,
                "mode_name": mode_cfg.get("mode_name", mode),
                "trigger": "manual",
                "schedule_name": "",
                "downloader_id": str((selected_downloader or {}).get("id") or ""),
                "downloader_name": str((selected_downloader or {}).get("name") or ""),
                "downloader_type": str((selected_downloader or {}).get("backend_type") or ""),
                "success": bool(success),
                "statistics": {
                    "total": 1,
                    "added_count": len(added),
                    "skipped_count": len(skipped),
                    "failed_count": len(failed),
                },
                "added_torrents": added,
                "skipped_torrents": skipped,
                "failed_torrents": failed,
                "duration_seconds": round(time.time() - started_at, 1),
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.tenant_store.save_history(tenant_key, task_id, payload)

        existing_names = set()
        if downloader_type == "qbittorrent":
            existing_names = {
                self._normalize_torrent_name(name)
                for name in (transmission.get_torrent_names() or set())
                if str(name or "").strip()
            }
            title_norm = self._normalize_torrent_name(title)
            if title_norm and title_norm in existing_names:
                self.tenant_store.remember_downloaded(tenant_key, title_text, url)
                save_single_history(added=[], skipped=[title_text], failed=[], success=True)
                return {"success": True, "message": "已存在"}
        rpc_result = self._add_torrent_with_qb_retry(
            downloader_client=transmission,
            downloader_type=downloader_type,
            title=title,
            enclosure_url=url,
            download_dir=mode_cfg.get("download_dir", "/downloads"),
            known_names=existing_names,
        )
        if rpc_result in {"success", "torrent-duplicate"}:
            self.tenant_store.remember_downloaded(tenant_key, title_text, url)
            if rpc_result == "success":
                save_single_history(added=[title_text], skipped=[], failed=[], success=True)
            else:
                save_single_history(added=[], skipped=[title_text], failed=[], success=True)
            return {"success": True, "message": "添加成功" if rpc_result == "success" else "已存在"}

        error_message = "qB 返回成功但未确认任务入列（已自动重试1次）" if rpc_result == "qb-unconfirmed" else (rpc_result or "添加失败")
        save_single_history(added=[], skipped=[], failed=[{"title": title_text, "error": error_message}], success=False)
        return {"success": False, "message": error_message}

    def execute_download(
        self,
        tenant_key: str,
        mode: str,
        keywords: Optional[List[str]] = None,
        task_id: Optional[str] = None,
        trigger: str = "manual",
        schedule_name: str = "",
        downloader_id: Optional[str] = None,
        progress_callback: Optional[Callable[[Dict], None]] = None,
    ) -> Dict:
        config = self.tenant_store.get_tenant_config(tenant_key)
        mode_cfg = (config.get("rss_modes") or {}).get(mode)
        if not mode_cfg:
            raise ValueError(f"无效模式: {mode}")
        if not int(mode_cfg.get("enabled", 1)):
            raise ValueError(f"模式已禁用: {mode}")
        if not str(mode_cfg.get("rss_url", "")).strip():
            raise ValueError(f"模式 {mode} 未配置 RSS URL")

        selected_downloader = self._pick_downloader(config, downloader_id=downloader_id)
        selected_downloader_id = str((selected_downloader or {}).get("id") or "")
        transmission = create_downloader_client(selected_downloader)

        items = self.fetch_rss_items(tenant_key, mode, downloader_id=selected_downloader_id)
        if not items:
            raise ValueError("RSS 获取失败或为空")

        if keywords:
            raw_items = [item.to_dict() for item in items]
            filtered = self.rss_parser.filter_by_keywords(raw_items, keywords)
            items = [RSSItem(**x) for x in filtered]
            if not items:
                raise ValueError("过滤后无匹配条目")

        downloader_type = str((selected_downloader or {}).get("backend_type") or "").strip().lower()
        existing_names = {
            self._normalize_torrent_name(name)
            for name in (transmission.get_torrent_names() or set())
            if str(name or "").strip()
        }
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
                skipped.append(title or "未命名种子")
                continue
            in_batch_urls.add(url)

            # Refresh qB baseline per item to avoid stale baseline causing false confirmation.
            if downloader_type == "qbittorrent":
                existing_names = {
                    self._normalize_torrent_name(name)
                    for name in (transmission.get_torrent_names() or set())
                    if str(name or "").strip()
                }

            if downloader_type != "qbittorrent" and self.tenant_store.is_downloaded(tenant_key, title, url):
                skipped.append(title)
                if progress_callback:
                    progress_callback(self._build_progress(idx, total, added, skipped, failed, title))
                continue

            title_norm = self._normalize_torrent_name(title)
            if title_norm and title_norm in existing_names:
                skipped.append(title)
                if progress_callback:
                    progress_callback(self._build_progress(idx, total, added, skipped, failed, title))
                continue

            try:
                rpc_result = self._add_torrent_with_qb_retry(
                    downloader_client=transmission,
                    downloader_type=downloader_type,
                    title=title,
                    enclosure_url=url,
                    download_dir=mode_cfg.get("download_dir", "/downloads"),
                    known_names=existing_names,
                )
                if rpc_result == "success":
                    added.append(title)
                    self.tenant_store.remember_downloaded(tenant_key, title, url)
                    if title_norm:
                        existing_names.add(title_norm)
                    if downloader_type == "qbittorrent":
                        existing_names |= {
                            self._normalize_torrent_name(name)
                            for name in (transmission.get_torrent_names() or set())
                            if str(name or "").strip()
                        }
                elif rpc_result == "torrent-duplicate":
                    skipped.append(title)
                    self.tenant_store.remember_downloaded(tenant_key, title, url)
                    if title_norm:
                        existing_names.add(title_norm)
                    if downloader_type == "qbittorrent":
                        existing_names |= {
                            self._normalize_torrent_name(name)
                            for name in (transmission.get_torrent_names() or set())
                            if str(name or "").strip()
                        }
                elif rpc_result == "qb-unconfirmed":
                    failed.append({"title": title, "error": "qB 返回成功但未确认任务入列（已自动重试1次）"})
                else:
                    failed.append({"title": title, "error": rpc_result or "未知错误"})
            except Exception as exc:
                logger.exception("下载条目处理失败")
                failed.append({"title": title, "error": str(exc)})

            if progress_callback:
                progress_callback(self._build_progress(idx, total, added, skipped, failed, title))
            time.sleep(0.2)

        payload = {
            "task_id": task_id or "",
            "tenant_key": tenant_key,
            "mode": mode,
            "mode_name": mode_cfg.get("mode_name", mode),
            "trigger": "schedule" if str(trigger or "").strip().lower() == "schedule" else "manual",
            "schedule_name": str(schedule_name or "").strip(),
            "downloader_id": selected_downloader_id,
            "downloader_name": str((selected_downloader or {}).get("name") or ""),
            "downloader_type": str((selected_downloader or {}).get("backend_type") or ""),
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

