# 新闻采集模块
import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from config.settings import Settings

logger = logging.getLogger(__name__)


class NewsCollector:
    """多源新闻采集器"""

    def __init__(self):
        self.timeout = 15
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        self.keywords = Settings.COLLECT_KEYWORDS

    def set_keywords(self, keywords: List[str]):
        self.keywords = keywords or Settings.COLLECT_KEYWORDS

    def collect_all(self) -> List[Dict]:
        all_news = []
        sources = [
            ("36Kr", self._collect_36kr),
            ("新浪新闻", self._collect_sina),
            ("百度新闻", self._collect_baidu),
            ("网易新闻", self._collect_163),
            ("腾讯新闻", self._collect_qq),
            ("搜狐新闻", self._collect_sohu),
            ("凤凰网", self._collect_ifeng),
            ("今日头条", self._collect_toutiao_search),
        ]

        for name, collector in sources:
            try:
                news_list = collector()
                all_news.extend(news_list)
                logger.info("%s: 采集 %s 条", name, len(news_list))
            except Exception as e:
                logger.warning("%s: 采集失败 - %s", name, e)
            time.sleep(0.5)

        return self._deduplicate(all_news)

    def _collect_36kr(self) -> List[Dict]:
        return self._generic_collect(
            "36Kr",
            lambda kw: f"https://so.36kr.com/news?q={quote_plus(kw)}",
            ["div.news-item", "div.article-item", "li.news-item", "div.search-result-item"],
            "https://36kr.com",
        )

    def _collect_sina(self) -> List[Dict]:
        return self._generic_collect(
            "新浪新闻",
            lambda kw: f"https://search.sina.com.cn/news.php?q={quote_plus(kw)}&c=news&range=all",
            ["div.result", "div.news-item", "div.box-result"],
            None,
        )

    def _collect_baidu(self) -> List[Dict]:
        return self._generic_collect(
            "百度新闻",
            lambda kw: f"https://news.baidu.com/ns?word={quote_plus(kw)}&pn=0&cl=2&ct=0&tn=news&rn=20",
            ["div.result", "div.news-item", ".result-op"],
            None,
        )

    def _collect_163(self) -> List[Dict]:
        return self._generic_collect(
            "网易新闻",
            lambda kw: f"https://search.163.com/search?q={quote_plus(kw)}&site=news",
            ["div.news_item", ".item", ".search-result"],
            None,
        )

    def _collect_qq(self) -> List[Dict]:
        return self._generic_collect(
            "腾讯新闻",
            lambda kw: f"https://new.qq.com/search?query={quote_plus(kw)}",
            ["div.Q-tpWrap", ".result", ".news-item"],
            "https://new.qq.com",
        )

    def _collect_sohu(self) -> List[Dict]:
        return self._generic_collect(
            "搜狐新闻",
            lambda kw: f"https://so.sohu.com/news?keyword={quote_plus(kw)}",
            ["div.news-list", ".result", ".news-item"],
            None,
        )

    def _collect_ifeng(self) -> List[Dict]:
        return self._generic_collect(
            "凤凰网",
            lambda kw: f"https://search.ifeng.com/sofeng/search.action?q={quote_plus(kw)}&type=news",
            ["div.newsItem", ".result", ".news-item"],
            None,
        )

    def _collect_toutiao_search(self) -> List[Dict]:
        return self._generic_collect(
            "今日头条",
            lambda kw: f"https://www.toutiao.com/search/?keyword={quote_plus(kw)}",
            ["div.news-item", ".result", ".article-item"],
            "https://www.toutiao.com",
        )

    def _generic_collect(self, source_name: str, url_builder, selectors: List[str], base_url: Optional[str]) -> List[Dict]:
        news_list = []
        for keyword in self.keywords[:2]:
            try:
                url = url_builder(keyword)
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding or "utf-8"
                if "text/html" not in resp.headers.get("Content-Type", ""):
                    logger.debug("%s 返回非 HTML 内容，跳过: %s", source_name, url)
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                items = []
                for selector in selectors:
                    items = soup.select(selector)
                    if items:
                        break

                if not items:
                    items = soup.find_all("a", href=re.compile(r"/news/"))[:20]

                for item in items[:10]:
                    title_el = item.select_one("h3, .title, .news-title, a") or item
                    title = title_el.get_text(strip=True)
                    href = item.get("href", "") or title_el.get("href", "")
                    if base_url and href and not href.startswith("http"):
                        href = urljoin(base_url, href)

                    summary_el = item.select_one("p, .summary, .text, .c-abstract")
                    summary = summary_el.get_text(strip=True) if summary_el else ""

                    if title and len(title) > 8:
                        news_list.append(
                            {
                                "title": title,
                                "source": source_name,
                                "url": href,
                                "summary": summary[:200],
                                "time": datetime.now().isoformat(),
                            }
                        )
            except Exception as e:
                logger.debug("%s 采集关键词 '%s' 失败: %s", source_name, keyword, e)
                continue
        return news_list

    def _deduplicate(self, news_list: List[Dict]) -> List[Dict]:
        seen = set()
        unique = []
        for news in news_list:
            title = news.get("title", "").strip()
            simple = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", title.lower())
            if simple and simple not in seen and len(simple) > 5:
                seen.add(simple)
                unique.append(news)
        return unique

    def fetch_article_content(self, url: str) -> Optional[str]:
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()

            content_el = soup.select_one(
                "article, .article-content, .content, .post-content, #articleContent, .news_content"
            )
            if content_el:
                paragraphs = content_el.find_all("p")
                text = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20)
                return text[:3000] if text else None

            paragraphs = soup.find_all("p")
            text = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)
            return text[:3000] if text else None
        except Exception as e:
            logger.error("抓取文章正文失败 %s: %s", url, e)
            return None
