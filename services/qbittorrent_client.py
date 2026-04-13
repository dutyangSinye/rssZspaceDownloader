import logging
import re
import time
from typing import Dict, List, Optional, Set
from urllib.parse import urlsplit

import requests

from config.settings import Settings

logger = logging.getLogger(__name__)


class QBittorrentClient:
    """Simple qBittorrent WebUI API client."""

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

        self._session = requests.Session()
        self._logged_in = False

    @classmethod
    def _normalize_host(cls, host: str) -> str:
        value = (host or "").strip()
        if not value:
            return ""

        if cls._DUP_SCHEME_RE.match(value):
            scheme = "https" if value.lower().startswith("https://") else "http"
            value = f"{scheme}://{cls._DUP_SCHEME_RE.sub('', value)}"
        if not cls._SCHEME_RE.match(value):
            value = f"http://{value}"

        parts = urlsplit(value)
        scheme = parts.scheme.lower() if parts.scheme in {"http", "https"} else "http"
        netloc = parts.netloc
        path = (parts.path or "").rstrip("/")
        return f"{scheme}://{netloc}{path}"

    def _api_url(self, path: str) -> str:
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{self.host}{suffix}"

    def _login(self) -> bool:
        try:
            resp = self._session.post(
                self._api_url("/api/v2/auth/login"),
                data={"username": self.username or "", "password": self.password or ""},
                timeout=self.request_timeout,
            )
            ok = resp.status_code == 200 and str(resp.text or "").strip().lower().startswith("ok")
            self._logged_in = ok
            return ok
        except Exception as exc:
            logger.error("qBittorrent 登录失败: %s", exc)
            self._logged_in = False
            return False

    def _request(self, method: str, path: str, **kwargs) -> Optional[requests.Response]:
        for attempt in range(self.max_retries):
            try:
                if not self._logged_in and not self._login():
                    return None
                resp = self._session.request(method, self._api_url(path), timeout=self.request_timeout, **kwargs)
                if resp.status_code in {401, 403}:
                    self._logged_in = False
                    if self._login():
                        resp = self._session.request(method, self._api_url(path), timeout=self.request_timeout, **kwargs)
                return resp
            except requests.RequestException as exc:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
                    continue
                logger.error("qBittorrent API 请求失败: %s", exc)
                return None
        return None

    def test_connection(self) -> bool:
        resp = self._request("GET", "/api/v2/app/version")
        return bool(resp and resp.status_code == 200 and str(resp.text or "").strip())

    def get_torrent_names(self) -> Set[str]:
        resp = self._request("GET", "/api/v2/torrents/info")
        if not resp or resp.status_code != 200:
            return set()
        try:
            payload = resp.json()
            if not isinstance(payload, list):
                return set()
            names = {
                str(item.get("name") or "").strip().lower()
                for item in payload
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            }
            return names
        except Exception:
            return set()

    def get_torrents(self) -> List[Dict]:
        resp = self._request("GET", "/api/v2/torrents/info")
        if not resp or resp.status_code != 200:
            return []
        try:
            payload = resp.json()
            return payload if isinstance(payload, list) else []
        except Exception:
            return []

    def add_torrent(self, url: str, download_dir: str) -> Dict:
        data = {"urls": str(url or "").strip()}
        if str(download_dir or "").strip():
            data["savepath"] = str(download_dir or "").strip()
        resp = self._request("POST", "/api/v2/torrents/add", data=data)
        if not resp:
            return {"result": "qBittorrent 连接失败"}
        if resp.status_code == 200 and str(resp.text or "").strip().lower().startswith("ok"):
            return {"result": "success"}
        detail = str(resp.text or "").strip() or f"HTTP {resp.status_code}"
        return {"result": detail}
