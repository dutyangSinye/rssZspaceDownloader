# RSS 解析器
import re
import logging
from typing import List, Dict, Optional
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


class RSSParser:
    """RSS XML 解析器"""

    @staticmethod
    def parse(xml_content: str) -> List[Dict]:
        """解析 RSS XML 内容"""
        items = []
        try:
            root = ET.fromstring(xml_content)
            channel = root.find("channel") or root
            for item in channel.findall("item"):
                title = (item.findtext("title") or "").strip()
                enclosure = item.find("enclosure")
                enclosure_url = enclosure.get("url") if enclosure is not None else None
                link = item.findtext("link") or ""
                summary = item.findtext("description") or ""
                guid = item.findtext("guid") or ""

                if enclosure_url:
                    chinese_name = RSSParser.extract_chinese_name(summary)
                    items.append(
                        {
                            "title": title,
                            "enclosure_url": enclosure_url,
                            "link": link,
                            "summary": summary,
                            "chinese_name": chinese_name,
                            "guid": guid,
                        }
                    )
        except ET.ParseError as e:
            logger.error(f"RSS XML 解析错误: {e}")

        return items

    @staticmethod
    def extract_chinese_name(desc: str) -> str:
        """从 description 中提取中文剧名"""
        if not desc:
            return ""

        clean_desc = re.sub(r"<[^>]+>", " ", desc)
        clean_desc = (
            clean_desc.replace("\u3000", " ")
            .replace("\t", " ")
            .replace("\xa0", " ")
        )
        clean_desc = re.sub(r" {2,}", " ", clean_desc).strip()

        if "\u25ce" in clean_desc:
            parts = clean_desc.split("\u25ce")
            for part in parts:
                part = part.strip()
                for pattern in ["译名", "译 名", "中文名", "片名", "原名", "名称", "剧名"]:
                    if pattern in part:
                        idx = part.find(pattern)
                        name_part = part[idx + len(pattern) :].strip()
                        name_part = name_part.lstrip(" \t\uff1a:").strip()
                        if "/" in name_part:
                            name_part = name_part.split("/")[0].strip()
                        if name_part:
                            return name_part

        for line in clean_desc.split("\n"):
            line = line.strip()
            for p in ["译名", "译 名", "中文名", "片名", "原名", "名称", "剧名"]:
                if p in line:
                    if "\uff1a" in line:
                        name = line.split("\uff1a", 1)[1].strip()
                        if name:
                            return name.split("/")[0].strip()
                    if ":" in line:
                        name = line.split(":", 1)[1].strip()
                        if name:
                            return name.split("/")[0].strip()

        return ""

    @staticmethod
    def filter_by_keywords(items: List[Dict], keywords: List[str]) -> List[Dict]:
        """关键词过滤"""
        if not keywords:
            return items

        # AND 匹配
        and_results = []
        for item in items:
            title_norm = item["title"].replace(".", " ").replace("_", " ").lower()
            chinese_lower = (item.get("chinese_name") or "").lower()
            if all(
                kw.lower() in title_norm or kw.lower() in chinese_lower
                for kw in keywords
            ):
                and_results.append(item)

        if and_results:
            return and_results

        # OR 匹配
        or_results = []
        for item in items:
            title_norm = item["title"].replace(".", " ").replace("_", " ").lower()
            chinese_lower = (item.get("chinese_name") or "").lower()
            if any(
                kw.lower() in title_norm or kw.lower() in chinese_lower
                for kw in keywords
            ):
                or_results.append(item)

        return or_results

    @staticmethod
    def truncate_title(title: str, max_len: int = 50) -> str:
        """截断标题"""
        if len(title) > max_len:
            return title[: max_len - 3] + "..."
        return title
