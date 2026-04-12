import logging
import re
import time
from typing import Dict, List, Optional, Set
from urllib.parse import urlsplit

import requests

from config.settings import Settings

logger = logging.getLogger(__name__)


class TransmissionClient:
    """Simple Transmission RPC client."""

    _SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
    _DUP_SCHEME_RE = re.compile(r"^(https?://)+", re.IGNORECASE)

    def __init__(
        self,
        host: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        request_timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[int] = None,
    ):
        raw_host = Settings.TRANSMISSION_HOST if host is None else host
        self.host = self._normalize_host(str(raw_host or ""))
        self.username = Settings.TRANSMISSION_USERNAME if username is None else username
        self.password = Settings.TRANSMISSION_PASSWORD if password is None else password
        self.request_timeout = int(Settings.REQUEST_TIMEOUT if request_timeout is None else request_timeout)
        self.max_retries = int(Settings.MAX_RETRIES if max_retries is None else max_retries)
        self.retry_delay = int(Settings.RETRY_DELAY if retry_delay is None else retry_delay)

        self.session_id: Optional[str] = None
        self._auth = (self.username, self.password) if self.username and self.password else None

    @classmethod
    def _normalize_host(cls, host: str) -> str:
        value = (host or "").strip()
        if not value:
            return ""

        # Collapse accidental duplicated scheme, e.g. http://http://host:9091
        if cls._DUP_SCHEME_RE.match(value):
            scheme = "https" if value.lower().startswith("https://") else "http"
            value = f"{scheme}://{cls._DUP_SCHEME_RE.sub('', value)}"

        if not cls._SCHEME_RE.match(value):
            value = f"http://{value}"

        parts = urlsplit(value)
        scheme = parts.scheme.lower() if parts.scheme in {"http", "https"} else "http"
        netloc = parts.netloc
        path = parts.path or ""

        # Handle malformed input like http://host/:9091
        if path.startswith("/:") and path[2:].isdigit() and ":" not in netloc:
            netloc = f"{netloc}:{path[2:]}"
            path = ""

        lower_path = path.lower()
        if lower_path == "/transmission/rpc":
            path = ""
        elif lower_path.endswith("/transmission/rpc"):
            path = path[: -len("/transmission/rpc")]

        return f"{scheme}://{netloc}{path.rstrip('/')}"

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
