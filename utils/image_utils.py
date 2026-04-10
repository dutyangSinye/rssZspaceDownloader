# 图片搜索和嵌入模块
import re
import logging
import requests
from urllib.parse import urljoin
from typing import Dict, List, Tuple
from bs4 import BeautifulSoup
from config.settings import Settings

logger = logging.getLogger(__name__)

# 通过标签打分选图，避免“关键词=单张固定图”导致文不对题
IMAGE_LIBRARY: List[Dict] = [
    {
        "url": "https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=1200&q=80",
        "tags": ["机器人", "机械臂", "人工智能", "自动化", "科技"],
    },
    {
        "url": "https://images.unsplash.com/photo-1504307651254-35680f356dfd?w=1200&q=80",
        "tags": ["施工", "工地", "建筑", "工程", "项目"],
    },
    {
        "url": "https://images.unsplash.com/photo-1541888946425-d81bb19240f5?w=1200&q=80",
        "tags": ["工地", "施工", "建筑", "基建", "现场"],
    },
    {
        "url": "https://images.unsplash.com/photo-1563203369-26f2e4a5ccf7?w=1200&q=80",
        "tags": ["机械臂", "制造业", "工业", "自动化", "智能制造"],
    },
    {
        "url": "https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=1200&q=80",
        "tags": ["数据", "分析", "可视化", "平台", "数字化"],
    },
    {
        "url": "https://images.unsplash.com/photo-1449824913935-59a10b8d2000?w=1200&q=80",
        "tags": ["城市", "基础设施", "智慧城市", "建设", "交通"],
    },
    {
        "url": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=1200&q=80",
        "tags": ["财经", "市场", "投资", "产业", "增长"],
    },
    {
        "url": "https://images.unsplash.com/photo-1460925895917-afdab827c52f?w=1200&q=80",
        "tags": ["风控", "数据大屏", "金融分析", "图表", "银行"],
    },
    {
        "url": "https://images.unsplash.com/photo-1554224155-8d04cb21cd6c?w=1200&q=80",
        "tags": ["银行", "贷款", "金融", "风险", "报表"],
    },
    {
        "url": "https://images.unsplash.com/photo-1560520653-9e0e4c89eb11?w=1200&q=80",
        "tags": ["房地产", "楼盘", "房价", "住房", "市场"],
    },
    {
        "url": "https://images.unsplash.com/photo-1477959858617-67f85cf4f1df?w=1200&q=80",
        "tags": ["土地", "地块", "城市更新", "建设", "规划"],
    },
    {
        "url": "https://images.unsplash.com/photo-1460472178825-e5240623afd5?w=1200&q=80",
        "tags": ["地图", "热力图", "城市", "区域", "可视化"],
    },
    {
        "url": "https://images.unsplash.com/photo-1531973968078-9bb02785f13d?w=1200&q=80",
        "tags": ["会议", "拍卖", "竞价", "发布会", "决策"],
    },
    {
        "url": "https://images.unsplash.com/photo-1521791136064-7986c2920216?w=1200&q=80",
        "tags": ["社区", "物业", "运营", "服务", "数字化管理"],
    },
    {
        "url": "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?w=1200&q=80",
        "tags": ["办公", "决策", "团队", "行业分析", "商业"],
    },
    {
        "url": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1200&q=80",
        "tags": ["互联网", "平台", "网络", "数字化", "科技"],
    },
    {
        "url": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1200&q=80",
        "tags": ["芯片", "技术", "创新", "研发", "人工智能"],
    },
    {
        "url": "https://images.unsplash.com/photo-1486325212027-8081e485255e?w=1200&q=80",
        "tags": ["办公室", "团队", "产品", "商业", "创新"],
    },
]

KEYWORD_ALIASES: Dict[str, List[str]] = {
    "建筑机器人": ["机器人", "建筑", "施工", "工地"],
    "智能施工": ["施工", "工地", "自动化", "工程"],
    "智能建造": ["建筑", "施工", "数字化", "自动化"],
    "人工智能": ["人工智能", "机器人", "算法", "模型"],
    "商业财经": ["财经", "市场", "投资", "增长"],
    "互联网产品": ["互联网", "平台", "产品", "用户"],
    "房地产": ["房地产", "楼盘", "住房", "房价", "土地"],
    "土拍": ["土地", "地块", "拍卖", "竞价", "城市规划"],
    "银行风险": ["银行", "贷款", "风险", "风控", "报表"],
}

FALLBACK_IMAGES = [
    "https://images.unsplash.com/photo-1504307651254-35680f356dfd?w=1200&q=80",
    "https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=1200&q=80",
    "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1200&q=80",
    "https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=1200&q=80",
    "https://images.unsplash.com/photo-1486325212027-8081e485255e?w=1200&q=80",
]


class ImageSearcher:
    """图片搜索器"""

    def __init__(self):
        self.save_dir = Settings.DATA_DIR / "images"
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.used_images = set()
        self.dynamic_images: List[Dict] = []
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )

    def _extract_page_images(self, page_url: str) -> List[str]:
        try:
            resp = self.session.get(page_url, timeout=10)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            logger.debug("抓取页面图片失败 %s: %s", page_url, e)
            return []

        candidates: List[str] = []
        for selector, attr in [
            ('meta[property="og:image"]', "content"),
            ('meta[name="twitter:image"]', "content"),
            ('meta[itemprop="image"]', "content"),
        ]:
            el = soup.select_one(selector)
            if el and el.get(attr):
                candidates.append(el.get(attr).strip())

        for img in soup.select("article img, .article img, .content img, img"):
            src = (img.get("src") or "").strip()
            if not src:
                continue
            candidates.append(src)
            if len(candidates) >= 10:
                break

        cleaned = []
        for u in candidates:
            full = urljoin(page_url, u)
            lu = full.lower()
            if any(x in lu for x in ["logo", "avatar", "icon", "qrcode", "wechat"]):
                continue
            if full.startswith("http") and full not in cleaned:
                cleaned.append(full)
        return cleaned[:5]

    def add_news_candidates(self, news_list: List[Dict], limit: int = 8):
        """从资讯原文提取候选图片，提高语义匹配度"""
        collected: List[Dict] = []
        for item in (news_list or [])[:limit]:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            title = (item.get("title") or "").strip()
            summary = (item.get("summary") or "").strip()
            tags = self._expand_tokens(f"{title} {summary}")
            for img_url in self._extract_page_images(url):
                collected.append({"url": img_url, "tags": tags})
        dedup = {}
        for it in collected:
            dedup[it["url"]] = it
        self.dynamic_images = list(dedup.values())

    def _expand_tokens(self, text: str) -> List[str]:
        text = (text or "").strip()
        if not text:
            return []
        base_tokens = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+", text)
        expanded = list(base_tokens)
        # 通过“词在文本中出现”提取短语关键词，提升中文长句匹配效果
        known_terms = set()
        for entry in IMAGE_LIBRARY:
            for t in entry.get("tags", []):
                known_terms.add(t)
        for k, aliases in KEYWORD_ALIASES.items():
            known_terms.add(k)
            for a in aliases:
                known_terms.add(a)
        for term in known_terms:
            if term and term in text:
                expanded.append(term)

        for token in base_tokens:
            for k, aliases in KEYWORD_ALIASES.items():
                if k in token or token in k:
                    expanded.extend(aliases)
        dedup = []
        for t in expanded:
            t = t.strip().lower()
            if t and t not in dedup:
                dedup.append(t)
        return dedup

    def _score_image(self, tokens: List[str], tags: List[str]) -> int:
        tags_lower = [t.lower() for t in tags]
        score = 0
        for token in tokens:
            for tag in tags_lower:
                if token == tag:
                    score += 4
                elif token in tag or tag in token:
                    score += 2
        return score

    def search(
        self,
        keyword: str,
        max_results: int = 5,
        context: str = "",
        exclude_urls: List[str] | None = None,
    ) -> List[str]:
        """按描述和上下文打分搜索图片"""
        excludes = set(exclude_urls or [])
        tokens = self._expand_tokens(f"{keyword} {context}")
        ranked: List[Tuple[int, str]] = []
        merged_library = self.dynamic_images + IMAGE_LIBRARY
        for item in merged_library:
            url = item["url"]
            if url in self.used_images or url in excludes:
                continue
            score = self._score_image(tokens, item.get("tags", []))
            # 优先使用真实资讯原文配图
            if item in self.dynamic_images:
                score += 3
            ranked.append((score, url))

        ranked.sort(key=lambda x: x[0], reverse=True)
        urls = []
        for score, url in ranked:
            if score <= 0 and len(urls) > 0:
                continue
            urls.append(url)
            self.used_images.add(url)
            if len(urls) >= max_results:
                return urls

        for img_url in FALLBACK_IMAGES:
            if img_url not in self.used_images and img_url not in excludes:
                urls.append(img_url)
                self.used_images.add(img_url)
                if len(urls) >= max_results:
                    return urls

        return urls[:max_results]


def extract_image_placeholders(article: str) -> List[str]:
    """从文章中提取图片占位符"""
    placeholders = re.findall(r"\[图片描述[：:](.*?)\]", article)
    cleaned = []
    for p in placeholders:
        p = re.sub(r"\s+", " ", p).strip(" ，。；;、")
        if p:
            cleaned.append(p)
    return cleaned


def extract_article_context(article: str) -> str:
    """提取文章前部上下文，辅助图片匹配"""
    head = article[:1200]
    head = re.sub(r"\[图片描述[：:].*?\]", " ", head)
    return re.sub(r"\s+", " ", head)


def embed_images(article: str, image_searcher: ImageSearcher, max_images: int = 5) -> str:
    """为文章嵌入图片"""
    placeholders = extract_image_placeholders(article)
    if not placeholders:
        return article

    final_article = article
    article_context = extract_article_context(article)
    for desc in placeholders[:max_images]:
        urls = image_searcher.search(desc, max_results=2, context=article_context)
        if urls:
            img_html = (
                f'\n\n<p style="text-align:center"><img src="{urls[0]}" alt="{desc}" '
                f'style="max-width:100%;border-radius:8px"></p>\n'
                f'<p style="text-align:center;color:#888;font-size:13px">{desc}</p>\n\n'
            )
            # 兼容占位符中是否有空格、全角/半角冒号等格式差异
            final_article, replaced_count = re.subn(
                r"\[图片描述[：:]\s*.*?\]",
                img_html,
                final_article,
                count=1,
            )
            if replaced_count == 0:
                logger.warning("占位符替换失败，描述: %s", desc)

    return final_article
