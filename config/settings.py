import os
from pathlib import Path
from typing import Dict, List


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Application settings.

    App-level runtime options stay in env/.env.
    Business configuration (tenant/transmission/rss) is stored in database.
    """

    BASE_DIR = Path(__file__).parent.parent
    DATA_DIR = BASE_DIR / "data"
    LOGS_DIR = BASE_DIR / "logs"
    DB_PATH = DATA_DIR / "app.db"

    HOST: str = "0.0.0.0"
    PORT: int = 5000
    DEBUG: bool = False
    SECRET_KEY: str = "mteam-secret-key"

    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"

    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = "yangyang83"
    DEFAULT_TENANT_KEY: str = "default"
    DEFAULT_TENANT_USERNAME: str = "admin"
    DEFAULT_TENANT_PASSWORD: str = "yangyang83"

    # Bootstrap defaults for first-time DB initialization.
    TRANSMISSION_HOST: str = "http://localhost:9091"
    TRANSMISSION_USERNAME: str = ""
    TRANSMISSION_PASSWORD: str = ""
    REQUEST_TIMEOUT: int = 30
    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 2

    # Keep these fields for compatibility with legacy news modules.
    RSS_CONFIG = {}
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    OPENAI_MODEL: str = "gpt-5.4"
    OPENAI_REASONING_EFFORT: str = "medium"
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b"
    OLLAMA_API_KEY: str = ""
    AI_MAX_TOKENS: int = 4000
    AI_TEMPERATURE: float = 0.7
    TOUTIAO_COOKIE: str = ""
    TOUTIAO_HEADLESS: bool = False
    COLLECT_KEYWORDS: List[str] = ["建筑机器人", "智能建造", "施工机器人"]
    LARK_APP_ID: str = ""
    LARK_APP_SECRET: str = ""
    LARK_RECEIVE_ID: str = ""
    LARK_RECEIVE_TYPE: str = "chat_id"

    @classmethod
    def load(cls):
        cls._load_dotenv()
        cls._load_from_env()
        cls._ensure_dirs()
        cls._build_legacy_rss_config()

    @classmethod
    def _load_dotenv(cls):
        env_file = cls.BASE_DIR / ".env"
        if not env_file.exists():
            return

        with open(env_file, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                os.environ.setdefault(key, value)

    @classmethod
    def _load_from_env(cls):
        cls.HOST = os.getenv("HOST", cls.HOST)
        cls.PORT = int(os.getenv("PORT", str(cls.PORT)))
        cls.DEBUG = _to_bool(os.getenv("DEBUG"), cls.DEBUG)
        cls.DB_PATH = Path(os.getenv("DB_PATH", str(cls.DB_PATH)))
        cls.SECRET_KEY = os.getenv("SECRET_KEY", cls.SECRET_KEY)

        cls.LOG_LEVEL = os.getenv("LOG_LEVEL", cls.LOG_LEVEL)
        cls.LOG_FILE = os.getenv("LOG_FILE", cls.LOG_FILE)

        cls.DEFAULT_ADMIN_USERNAME = os.getenv("DEFAULT_ADMIN_USERNAME", cls.DEFAULT_ADMIN_USERNAME)
        cls.DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", cls.DEFAULT_ADMIN_PASSWORD)
        cls.DEFAULT_TENANT_KEY = os.getenv("DEFAULT_TENANT_KEY", cls.DEFAULT_TENANT_KEY)
        cls.DEFAULT_TENANT_USERNAME = os.getenv("DEFAULT_TENANT_USERNAME", cls.DEFAULT_TENANT_USERNAME)
        cls.DEFAULT_TENANT_PASSWORD = os.getenv("DEFAULT_TENANT_PASSWORD", cls.DEFAULT_TENANT_PASSWORD)

        cls.TRANSMISSION_HOST = os.getenv("TRANSMISSION_HOST", cls.TRANSMISSION_HOST)
        cls.TRANSMISSION_USERNAME = os.getenv("TRANSMISSION_USERNAME", cls.TRANSMISSION_USERNAME)
        cls.TRANSMISSION_PASSWORD = os.getenv("TRANSMISSION_PASSWORD", cls.TRANSMISSION_PASSWORD)
        cls.REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", str(cls.REQUEST_TIMEOUT)))
        cls.MAX_RETRIES = int(os.getenv("MAX_RETRIES", str(cls.MAX_RETRIES)))
        cls.RETRY_DELAY = int(os.getenv("RETRY_DELAY", str(cls.RETRY_DELAY)))

        cls.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", cls.OPENAI_API_KEY)
        cls.OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", cls.OPENAI_BASE_URL)
        cls.OPENAI_MODEL = os.getenv("OPENAI_MODEL", cls.OPENAI_MODEL)
        cls.OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", cls.OPENAI_REASONING_EFFORT)
        cls.OLLAMA_URL = os.getenv("OLLAMA_URL", cls.OLLAMA_URL)
        cls.OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", cls.OLLAMA_MODEL)
        cls.OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", cls.OLLAMA_API_KEY)
        cls.AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", str(cls.AI_MAX_TOKENS)))
        cls.AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", str(cls.AI_TEMPERATURE)))
        cls.TOUTIAO_COOKIE = os.getenv("TOUTIAO_COOKIE", cls.TOUTIAO_COOKIE)
        cls.TOUTIAO_HEADLESS = _to_bool(os.getenv("TOUTIAO_HEADLESS"), cls.TOUTIAO_HEADLESS)
        cls.LARK_APP_ID = os.getenv("LARK_APP_ID", cls.LARK_APP_ID)
        cls.LARK_APP_SECRET = os.getenv("LARK_APP_SECRET", cls.LARK_APP_SECRET)
        cls.LARK_RECEIVE_ID = os.getenv("LARK_RECEIVE_ID", cls.LARK_RECEIVE_ID)
        cls.LARK_RECEIVE_TYPE = os.getenv("LARK_RECEIVE_TYPE", cls.LARK_RECEIVE_TYPE)

        keywords = os.getenv("COLLECT_KEYWORDS", "")
        if keywords:
            cls.COLLECT_KEYWORDS = [k.strip() for k in keywords.split(",") if k.strip()]

    @classmethod
    def _ensure_dirs(cls):
        dirs = [
            cls.DATA_DIR,
            cls.LOGS_DIR,
            cls.DATA_DIR / "article_history",
            cls.DATA_DIR / "previews",
            cls.DATA_DIR / "images",
            cls.DATA_DIR / "browser_data",
            cls.DATA_DIR / "screenshots",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _build_legacy_rss_config(cls):
        cls.RSS_CONFIG = {
            "movie": {
                "url": os.getenv("RSS_MOVIE_URL", ""),
                "download_dir": os.getenv("DOWNLOAD_DIR_MOVIE", "/film"),
                "name": "收藏4K电影",
            },
            "tv": {
                "url": os.getenv("RSS_TV_URL", ""),
                "download_dir": os.getenv("DOWNLOAD_DIR_TV", "/tv"),
                "name": "国产电视剧",
            },
            "adult": {
                "url": os.getenv("RSS_ADULT_URL", ""),
                "download_dir": os.getenv("DOWNLOAD_DIR_ADULT", "/av"),
                "name": "收藏成人内容",
            },
        }

    @classmethod
    def default_tenant_seed(cls) -> Dict:
        """Seed data for first default tenant initialization."""
        return {
            "transmission": {
                "host": cls.TRANSMISSION_HOST,
                "username": cls.TRANSMISSION_USERNAME,
                "password": cls.TRANSMISSION_PASSWORD,
                "request_timeout": cls.REQUEST_TIMEOUT,
                "max_retries": cls.MAX_RETRIES,
                "retry_delay": cls.RETRY_DELAY,
            },
            "rss_modes": {
                "movie": {
                    "mode_name": "收藏4K电影",
                    "rss_url": os.getenv("RSS_MOVIE_URL", ""),
                    "download_dir": os.getenv("DOWNLOAD_DIR_MOVIE", "/film"),
                    "enabled": 1,
                },
                "tv": {
                    "mode_name": "国产电视剧",
                    "rss_url": os.getenv("RSS_TV_URL", ""),
                    "download_dir": os.getenv("DOWNLOAD_DIR_TV", "/tv"),
                    "enabled": 1,
                },
                "adult": {
                    "mode_name": "收藏成人内容",
                    "rss_url": os.getenv("RSS_ADULT_URL", ""),
                    "download_dir": os.getenv("DOWNLOAD_DIR_ADULT", "/av"),
                    "enabled": 0,
                },
            },
        }


Settings.load()
