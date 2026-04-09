# 图片搜索和嵌入模块
import os
import re
import logging
import requests
from typing import List, Optional
from config.settings import Settings

logger = logging.getLogger(__name__)

# 关键词到图片URL的映射
KEYWORD_IMAGES = {
    "建筑机器人": "https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=800&q=80",
    "施工": "https://images.unsplash.com/photo-1504307651254-35680f356dfd?w=800&q=80",
    "工地": "https://images.unsplash.com/photo-1541888946425-d81bb19240f5?w=800&q=80",
    "智能建造": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80",
    "机械臂": "https://images.unsplash.com/photo-1563203369-26f2e4a5ccf7?w=800&q=80",
    "自动化": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80",
    "科技": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80",
    "数据": "https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=800&q=80",
    "机器人": "https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=800&q=80",
    "城市": "https://images.unsplash.com/photo-1449824913935-59a10b8d2000?w=800&q=80",
    "创新": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80",
    "市场": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800&q=80",
    "未来": "https://images.unsplash.com/photo-1486325212027-8081e485255e?w=800&q=80",
}

FALLBACK_IMAGES = [
    "https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=800&q=80",
    "https://images.unsplash.com/photo-1504307651254-35680f356dfd?w=800&q=80",
    "https://images.unsplash.com/photo-1541888946425-d81bb19240f5?w=800&q=80",
    "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80",
    "https://images.unsplash.com/photo-1486325212027-8081e485255e?w=800&q=80",
]


class ImageSearcher:
    """图片搜索器"""

    def __init__(self):
        self.save_dir = Settings.DATA_DIR / "images"
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.used_images = set()
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )

    def search(self, keyword: str, max_results: int = 5) -> List[str]:
        """搜索图片"""
        urls = []
        for kw, img_url in KEYWORD_IMAGES.items():
            if kw in keyword and img_url not in self.used_images:
                urls.append(img_url)
                self.used_images.add(img_url)
                if len(urls) >= max_results:
                    return urls

        for img_url in FALLBACK_IMAGES:
            if img_url not in self.used_images:
                urls.append(img_url)
                self.used_images.add(img_url)
                if len(urls) >= max_results:
                    return urls

        return urls[:max_results]


def extract_image_placeholders(article: str) -> List[str]:
    """从文章中提取图片占位符"""
    return re.findall(r"\[图片描述[：:](.*?)\]", article)


def embed_images(article: str, image_searcher: ImageSearcher, max_images: int = 5) -> str:
    """为文章嵌入图片"""
    placeholders = extract_image_placeholders(article)
    if not placeholders:
        return article

    final_article = article
    for desc in placeholders[:max_images]:
        urls = image_searcher.search(desc, max_results=2)
        if urls:
            img_html = (
                f'\n\n<p style="text-align:center"><img src="{urls[0]}" alt="{desc}" '
                f'style="max-width:100%;border-radius:8px"></p>\n'
                f'<p style="text-align:center;color:#888;font-size:13px">{desc}</p>\n\n'
            )
            for variant in [f"[图片描述:{desc}]", f"[图片描述：{desc}]"]:
                if variant in final_article:
                    final_article = final_article.replace(variant, img_html, 1)
                    break

    return final_article
