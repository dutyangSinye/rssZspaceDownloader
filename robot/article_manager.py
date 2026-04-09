# 文章管理模块
import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional
from config.settings import Settings

logger = logging.getLogger(__name__)


class ArticleManager:
    """文章历史记录管理"""

    def __init__(self):
        self.history_dir = Settings.DATA_DIR / "article_history"
        self.preview_dir = Settings.DATA_DIR / "previews"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.preview_dir.mkdir(parents=True, exist_ok=True)

    def save_article(self, article_data: Dict) -> Optional[str]:
        try:
            filename = f"article_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            filepath = self.history_dir / filename
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(article_data, f, ensure_ascii=False, indent=2)
            logger.info("文章已保存: %s", filename)
            return filename
        except Exception as e:
            logger.error("保存文章失败: %s", e)
            return None

    def load_history(self) -> List[Dict]:
        history = []
        try:
            for filename in sorted(self.history_dir.glob("*.json"), reverse=True):
                with open(filename, "r", encoding="utf-8") as f:
                    history.append(json.load(f))
        except Exception as e:
            logger.error("加载历史失败: %s", e)
        return history

    def get_article(self, article_id: str) -> Optional[Dict]:
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", article_id)
        filepath = self.history_dir / f"{safe_id}.json"
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _sanitize_preview_html(self, article_html: str) -> str:
        sanitized = re.sub(r"(?is)<(script|iframe|object|embed)[^>]*>.*?</\\1>", "", article_html)
        sanitized = re.sub(r"(?i)\\son[a-z]+\s*=\s*\"[^\"]*\"", "", sanitized)
        sanitized = re.sub(r"(?i)\\son[a-z]+\s*=\s*'[^']*'", "", sanitized)
        return sanitized

    def save_preview(self, article_html: str) -> Optional[str]:
        try:
            filename = f"article_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            filepath = self.preview_dir / filename
            body_html = self._sanitize_preview_html(article_html).replace(chr(10), "<br>")

            html_content = f"""<!DOCTYPE html>
<html lang=\"zh-CN\">
<head>
    <meta charset=\"UTF-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
    <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self'; img-src https: data:; style-src 'unsafe-inline' https:; script-src 'none';\">
    <title>文章预览</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        .container {{ background: white; padding: 40px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        img {{ max-width: 100%; border-radius: 8px; }}
        p {{ line-height: 1.8; }}
        h1, h2, h3 {{ color: #333; }}
        blockquote {{ border-left: 4px solid #6366f1; padding-left: 16px; margin: 16px 0; color: #666; }}
        .meta {{ color: #888; font-size: 14px; margin-bottom: 20px; }}
    </style>
</head>
<body>
    <div class=\"container\">
        <div class=\"meta\">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
        {body_html}
    </div>
</body>
</html>"""
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info("预览已保存: %s", filename)
            return filename
        except Exception as e:
            logger.error("保存预览失败: %s", e)
            return None

    def get_previews(self) -> List[str]:
        try:
            return sorted([f.name for f in self.preview_dir.glob("*.html")], reverse=True)
        except Exception:
            return []
