import html
import logging
import re
import xml.etree.ElementTree as ET
from typing import Dict, List

logger = logging.getLogger(__name__)

_NAME_LABELS = (
    "\u8bd1\u540d",
    "\u8bd1 \u540d",
    "\u4e2d\u6587\u540d",
    "\u4e2d\u6587\u540d\u79f0",
    "\u7247\u540d",
    "\u5267\u540d",
    "\u540d\u79f0",
    "\u539f\u540d",
    "\u539f\u6807\u9898",
    "\u53c8\u540d",
)

_BULLET = "\u25ce"
_FULL_COLON = "\uff1a"

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


class RSSParser:
    """RSS parser helpers."""

    @staticmethod
    def parse(xml_content: str) -> List[Dict]:
        items: List[Dict] = []
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as exc:
            logger.error("RSS XML parse error: %s", exc)
            return items

        channel = root.find("channel") or root
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            enclosure = item.find("enclosure")
            enclosure_url = ""
            if enclosure is not None:
                enclosure_url = (enclosure.get("url") or "").strip()

            link = (item.findtext("link") or "").strip()
            summary = (item.findtext("description") or "").strip()
            guid = (item.findtext("guid") or "").strip()

            download_url = enclosure_url or RSSParser._fallback_download_url(link, summary)
            if not download_url:
                continue

            chinese_name = RSSParser.extract_chinese_name(summary)
            if not chinese_name:
                chinese_name = RSSParser.extract_chinese_name(title)

            items.append(
                {
                    "title": title,
                    "enclosure_url": download_url,
                    "link": link,
                    "summary": summary,
                    "chinese_name": chinese_name,
                    "guid": guid,
                }
            )

        return items

    @staticmethod
    def _fallback_download_url(link: str, summary: str) -> str:
        raw_link = (link or "").strip()
        if raw_link.startswith("magnet:?"):
            return raw_link
        if re.search(r"\.torrent($|\?)", raw_link, flags=re.IGNORECASE):
            return raw_link
        if re.search(r"download|dl|torrent", raw_link, flags=re.IGNORECASE):
            return raw_link

        raw_summary = html.unescape(summary or "")
        magnet_match = re.search(r"(magnet:\?[^\s\"'<>]+)", raw_summary, flags=re.IGNORECASE)
        if magnet_match:
            return magnet_match.group(1)
        torrent_match = re.search(r"(https?://[^\s\"'<>]+\.torrent(?:\?[^\s\"'<>]*)?)", raw_summary, flags=re.IGNORECASE)
        if torrent_match:
            return torrent_match.group(1)
        return ""

    @staticmethod
    def extract_chinese_name(desc: str) -> str:
        if not desc:
            return ""

        text = html.unescape(desc)
        text = _TAG_RE.sub(" ", text)
        text = text.replace("\u3000", " ").replace("\xa0", " ")
        text = _SPACE_RE.sub(" ", text)

        # Strong pattern: label + separator + value.
        for label in _NAME_LABELS:
            pattern = rf"(?:{re.escape(_BULLET)}\s*)?{re.escape(label)}\s*[{_FULL_COLON}:]\s*([^\n\r|/{_BULLET}]+)"
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                name = RSSParser._clean_name(match.group(1))
                if name:
                    return name

        # Fallback pattern for descriptors like: ◎译名 XXX / YYY
        label_union = "|".join(re.escape(v) for v in _NAME_LABELS if v.strip())
        fallback = re.search(
            rf"{re.escape(_BULLET)}\s*(?:{label_union})\s+([^\n\r]+)",
            text,
            flags=re.IGNORECASE,
        )
        if fallback:
            name = RSSParser._clean_name(fallback.group(1))
            if name:
                return name

        return ""

    @staticmethod
    def _clean_name(value: str) -> str:
        val = (value or "").strip()
        if not val:
            return ""
        val = re.split(rf"[|/{_BULLET}]", val)[0].strip()
        val = val.strip("-：:[]()（） ")
        val = _SPACE_RE.sub(" ", val)
        return val

    @staticmethod
    def filter_by_keywords(items: List[Dict], keywords: List[str]) -> List[Dict]:
        if not keywords:
            return items

        and_results: List[Dict] = []
        for item in items:
            title_norm = (item.get("title") or "").replace(".", " ").replace("_", " ").lower()
            chinese_lower = (item.get("chinese_name") or "").lower()
            if all(kw.lower() in title_norm or kw.lower() in chinese_lower for kw in keywords):
                and_results.append(item)

        if and_results:
            return and_results

        or_results: List[Dict] = []
        for item in items:
            title_norm = (item.get("title") or "").replace(".", " ").replace("_", " ").lower()
            chinese_lower = (item.get("chinese_name") or "").lower()
            if any(kw.lower() in title_norm or kw.lower() in chinese_lower for kw in keywords):
                or_results.append(item)

        return or_results

    @staticmethod
    def truncate_title(title: str, max_len: int = 50) -> str:
        if len(title) > max_len:
            return title[: max_len - 3] + "..."
        return title
