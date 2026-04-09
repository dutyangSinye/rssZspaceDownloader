# Transmission RPC 客户端
import logging
from typing import Dict, List, Set, Optional
import requests
from config.settings import Settings

logger = logging.getLogger(__name__)


class TransmissionClient:
    """Transmission BitTorrent 客户端"""

    def __init__(self, host: str = None):
        self.host = (host or Settings.TRANSMISSION_HOST).rstrip("/")
        self.session_id = None
        self.username = Settings.TRANSMISSION_USERNAME
        self.password = Settings.TRANSMISSION_PASSWORD
        self._auth = (
            (self.username, self.password)
            if self.username and self.password
            else None
        )

    def _refresh_session(self) -> bool:
        """刷新 Session ID"""
        try:
            resp = requests.post(
                f"{self.host}/transmission/rpc",
                json={"method": "session-get", "params": {}},
                timeout=Settings.REQUEST_TIMEOUT,
                auth=self._auth,
            )
            if resp.status_code in (200, 403, 409):
                self.session_id = resp.headers.get("X-Transmission-Session-Id")
                return True
        except Exception as e:
            logger.error(f"刷新 Session 失败: {e}")
        return False

    def _rpc_call(self, method: str, args: Dict = None) -> Dict:
        """调用 RPC 方法"""
        if args is None:
            args = {}

        payload = {"method": method, "arguments": args}
        headers = {"Content-Type": "application/json"}
        if self.session_id:
            headers["X-Transmission-Session-Id"] = self.session_id

        for attempt in range(Settings.MAX_RETRIES):
            try:
                resp = requests.post(
                    f"{self.host}/transmission/rpc",
                    json=payload,
                    headers=headers,
                    timeout=Settings.REQUEST_TIMEOUT,
                    auth=self._auth,
                )

                if resp.status_code == 409:
                    if self._refresh_session():
                        headers["X-Transmission-Session-Id"] = self.session_id
                        continue

                return resp.json()
            except Exception as e:
                if attempt < Settings.MAX_RETRIES - 1:
                    import time
                    time.sleep(Settings.RETRY_DELAY)
                    continue
                logger.error(f"RPC 调用失败: {e}")
                return {"result": str(e)}

        return {"result": "重试次数用尽"}

    def get_torrents(self) -> List[Dict]:
        """获取所有种子"""
        result = self._rpc_call(
            "torrent-get", {"fields": ["name", "hashString", "status"]}
        )
        return result.get("arguments", {}).get("torrents", [])

    def add_torrent(self, url: str, download_dir: str) -> Dict:
        """添加种子"""
        return self._rpc_call(
            "torrent-add", {"filename": url, "download-dir": download_dir}
        )

    def get_torrent_names(self) -> Set[str]:
        """获取所有种子名称（小写）"""
        torrents = self.get_torrents()
        return {t["name"].lower() for t in torrents if "name" in t}

    def test_connection(self) -> bool:
        """测试连接"""
        try:
            return self._refresh_session()
        except Exception:
            return False
