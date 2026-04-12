import logging
import time
from typing import Dict, List, Optional, Set

import requests

from config.settings import Settings

logger = logging.getLogger(__name__)


class TransmissionClient:
    """Simple Transmission RPC client."""

    def __init__(
        self,
        host: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        request_timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[int] = None,
    ):
        self.host = (host or Settings.TRANSMISSION_HOST).rstrip("/")
        self.username = Settings.TRANSMISSION_USERNAME if username is None else username
        self.password = Settings.TRANSMISSION_PASSWORD if password is None else password
        self.request_timeout = int(Settings.REQUEST_TIMEOUT if request_timeout is None else request_timeout)
        self.max_retries = int(Settings.MAX_RETRIES if max_retries is None else max_retries)
        self.retry_delay = int(Settings.RETRY_DELAY if retry_delay is None else retry_delay)

        self.session_id: Optional[str] = None
        self._auth = (self.username, self.password) if self.username and self.password else None

    def _refresh_session(self) -> bool:
        try:
            resp = requests.post(
                f"{self.host}/transmission/rpc",
                json={"method": "session-get", "params": {}},
                timeout=self.request_timeout,
                auth=self._auth,
            )
            if resp.status_code in {200, 403, 409}:
                self.session_id = resp.headers.get("X-Transmission-Session-Id")
                return True
        except Exception as exc:
            logger.error("刷新 Transmission Session 失败: %s", exc)
        return False

    def _rpc_call(self, method: str, args: Optional[Dict] = None) -> Dict:
        payload = {"method": method, "arguments": args or {}}
        headers = {"Content-Type": "application/json"}
        if self.session_id:
            headers["X-Transmission-Session-Id"] = self.session_id

        for attempt in range(self.max_retries):
            try:
                resp = requests.post(
                    f"{self.host}/transmission/rpc",
                    json=payload,
                    headers=headers,
                    timeout=self.request_timeout,
                    auth=self._auth,
                )

                if resp.status_code == 409:
                    if self._refresh_session() and self.session_id:
                        headers["X-Transmission-Session-Id"] = self.session_id
                        continue

                return resp.json()
            except Exception as exc:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
                    continue
                logger.error("Transmission RPC 调用失败: %s", exc)
                return {"result": str(exc)}

        return {"result": "重试次数用尽"}

    def get_torrents(self) -> List[Dict]:
        result = self._rpc_call("torrent-get", {"fields": ["name", "hashString", "status"]})
        return result.get("arguments", {}).get("torrents", [])

    def add_torrent(self, url: str, download_dir: str) -> Dict:
        return self._rpc_call("torrent-add", {"filename": url, "download-dir": download_dir})

    def get_torrent_names(self) -> Set[str]:
        torrents = self.get_torrents()
        return {t["name"].lower() for t in torrents if "name" in t and t["name"]}

    def test_connection(self) -> bool:
        return self._refresh_session()
