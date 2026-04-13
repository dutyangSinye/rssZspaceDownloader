from typing import Any, Dict

from services.qbittorrent_client import QBittorrentClient
from services.transmission_client import TransmissionClient

SUPPORTED_DOWNLOADER_TYPES = {"transmission", "qbittorrent"}


def normalize_downloader_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"qb", "qbt", "qbittorrent"}:
        return "qbittorrent"
    return "transmission"


def create_downloader_client(transmission_config: Dict[str, Any]):
    config = transmission_config or {}
    downloader_type = normalize_downloader_type(config.get("backend_type"))
    common_kwargs = {
        "host": config.get("host"),
        "username": config.get("username"),
        "password": config.get("password"),
        "request_timeout": config.get("request_timeout"),
        "max_retries": config.get("max_retries"),
        "retry_delay": config.get("retry_delay"),
    }
    if downloader_type == "qbittorrent":
        return QBittorrentClient(**common_kwargs)
    return TransmissionClient(**common_kwargs)
