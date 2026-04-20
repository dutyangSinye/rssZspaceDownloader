import hashlib
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from werkzeug.security import check_password_hash, generate_password_hash


DEFAULT_TRANSMISSION = {
    "backend_type": "transmission",
    "host": "http://localhost:9091",
    "username": "",
    "password": "",
    "request_timeout": 30,
    "max_retries": 3,
    "retry_delay": 2,
}

DEFAULT_RSS_MODES = {
    "movie": {"mode_name": "收藏4K电影", "rss_url": "", "download_dir": "/film", "enabled": 1},
    "tv": {"mode_name": "国产电视剧", "rss_url": "", "download_dir": "/tv", "enabled": 1},
    "adult": {"mode_name": "收藏成人内容", "rss_url": "", "download_dir": "/av", "enabled": 0},
}

DEFAULT_SCHEDULE_TZ = "Asia/Shanghai"
FALLBACK_SCHEDULE_TZ = timezone(timedelta(hours=8), name="UTC+08")


class TenantStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _hash_api_key(api_key: str) -> str:
        return hashlib.sha256(str(api_key or "").encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str):
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if column not in {row["name"] for row in rows}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_key TEXT NOT NULL UNIQUE,
                    tenant_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tenant_configs (
                    tenant_id INTEGER NOT NULL,
                    config_key TEXT NOT NULL,
                    config_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, config_key),
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS tenant_rss_modes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    mode_name TEXT NOT NULL,
                    rss_url TEXT NOT NULL DEFAULT '',
                    download_dir TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    UNIQUE (tenant_id, mode),
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS download_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    task_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS downloaded_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    enclosure_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (tenant_id, title),
                    UNIQUE (tenant_id, enclosure_url),
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS tenant_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS migration_jobs (
                    job_key TEXT PRIMARY KEY,
                    meta_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tenant_api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    key_name TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'admin',
                    key_status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT,
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
                    UNIQUE (tenant_id, key_hash)
                );
                CREATE TABLE IF NOT EXISTS admin_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    account_status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tenant_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    user_role TEXT NOT NULL DEFAULT 'user',
                    account_status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
                    UNIQUE (tenant_id, username)
                );
                CREATE TABLE IF NOT EXISTS tenant_download_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    schedule_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    downloader_id TEXT NOT NULL DEFAULT '',
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    run_time TEXT NOT NULL DEFAULT '03:00',
                    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_date TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_download_history_tenant ON download_history(tenant_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_downloaded_items_tenant ON downloaded_items(tenant_id);
                CREATE INDEX IF NOT EXISTS idx_tenant_audit_logs_tenant ON tenant_audit_logs(tenant_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_tenant_api_keys_tenant ON tenant_api_keys(tenant_id, key_status, id DESC);
                CREATE INDEX IF NOT EXISTS idx_tenant_users_tenant ON tenant_users(tenant_id, account_status, id DESC);
                CREATE INDEX IF NOT EXISTS idx_tenant_download_schedules_tenant ON tenant_download_schedules(tenant_id, enabled, id DESC);
                """
            )
            self._ensure_column(conn, "tenants", "tenant_status", "TEXT NOT NULL DEFAULT 'active'")
            self._ensure_column(
                conn,
                "tenant_download_schedules",
                "timezone",
                f"TEXT NOT NULL DEFAULT '{DEFAULT_SCHEDULE_TZ}'",
            )
            self._ensure_column(conn, "tenant_download_schedules", "last_run_date", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "tenant_download_schedules", "downloader_id", "TEXT NOT NULL DEFAULT ''")
            conn.execute("UPDATE tenant_users SET user_role = 'user' WHERE user_role <> 'user'")

    def _get_tenant_row(self, conn: sqlite3.Connection, tenant_key: str) -> Optional[sqlite3.Row]:
        return conn.execute(
            "SELECT id, tenant_key, tenant_name, tenant_status, created_at, updated_at FROM tenants WHERE tenant_key = ?",
            (tenant_key,),
        ).fetchone()

    def _get_tenant_id(self, conn: sqlite3.Connection, tenant_key: str) -> Optional[int]:
        row = self._get_tenant_row(conn, tenant_key)
        return int(row["id"]) if row else None

    def _upsert_config(self, conn: sqlite3.Connection, tenant_id: int, key: str, value: str):
        conn.execute(
            """
            INSERT INTO tenant_configs(tenant_id, config_key, config_value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tenant_id, config_key)
            DO UPDATE SET config_value=excluded.config_value, updated_at=excluded.updated_at
            """,
            (tenant_id, key, value, self._now()),
        )

    def _upsert_mode(self, conn: sqlite3.Connection, tenant_id: int, mode: str, mode_data: Dict[str, Any]):
        conn.execute(
            """
            INSERT INTO tenant_rss_modes(tenant_id, mode, mode_name, rss_url, download_dir, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, mode)
            DO UPDATE SET
                mode_name=excluded.mode_name,
                rss_url=excluded.rss_url,
                download_dir=excluded.download_dir,
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
            """,
            (
                tenant_id,
                mode,
                mode_data.get("mode_name", DEFAULT_RSS_MODES.get(mode, {}).get("mode_name", mode)),
                mode_data.get("rss_url", ""),
                mode_data.get("download_dir", DEFAULT_RSS_MODES.get(mode, {}).get("download_dir", "/downloads")),
                int(mode_data.get("enabled", 1)),
                self._now(),
            ),
        )

    def _log_audit(self, conn: sqlite3.Connection, tenant_id: int, action: str, actor: str, detail: Dict[str, Any]):
        conn.execute(
            """
            INSERT INTO tenant_audit_logs(tenant_id, action, actor, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tenant_id, action, (actor or "system")[:64], json.dumps(detail or {}, ensure_ascii=False), self._now()),
        )

    @staticmethod
    def _normalize_schedule_time(value: Any) -> str:
        text = str(value or "").strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
            raise ValueError("自动下载时间格式无效，请使用 HH:MM（24小时制）")
        return text

    @staticmethod
    def _normalize_schedule_keywords(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        text = str(value or "").strip()
        if not text:
            return []
        return [v for v in re.split(r"[\s,，]+", text) if v]

    @staticmethod
    def _normalize_downloader_type(value: Any) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"qb", "qbt", "qbittorrent"}:
            return "qbittorrent"
        if raw in {"", "tr", "transmission"}:
            return "transmission"
        raise ValueError(f"下载器类型无效: {value}")

    @staticmethod
    def _normalize_positive_int(value: Any, default_value: int, min_value: int = 0) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = int(default_value)
        if parsed < int(min_value):
            parsed = int(min_value)
        return parsed

    @staticmethod
    def _normalize_downloader_id(value: Any, fallback: str = "") -> str:
        raw = re.sub(r"[^a-z0-9_-]+", "_", str(value or "").strip().lower())
        raw = re.sub(r"_+", "_", raw).strip("_")
        if raw:
            return raw[:40]
        fallback_raw = re.sub(r"[^a-z0-9_-]+", "_", str(fallback or "").strip().lower())
        fallback_raw = re.sub(r"_+", "_", fallback_raw).strip("_")
        if fallback_raw:
            return fallback_raw[:40]
        return f"dl_{secrets.token_hex(3)}"

    def _normalize_downloader_profile(self, raw: Any, fallback_id: str = "", fallback_name: str = "") -> Dict[str, Any]:
        item = raw if isinstance(raw, dict) else {}
        normalized_id = self._normalize_downloader_id(item.get("id"), fallback_id)
        normalized_name = str(item.get("name") or fallback_name or normalized_id).strip()[:40] or normalized_id
        return {
            "id": normalized_id,
            "name": normalized_name,
            "backend_type": self._normalize_downloader_type(item.get("backend_type")),
            "host": str(item.get("host") or "").strip(),
            "username": str(item.get("username") or "").strip(),
            "password": str(item.get("password") or ""),
            "request_timeout": self._normalize_positive_int(item.get("request_timeout"), DEFAULT_TRANSMISSION["request_timeout"], 1),
            "max_retries": self._normalize_positive_int(item.get("max_retries"), DEFAULT_TRANSMISSION["max_retries"], 1),
            "retry_delay": self._normalize_positive_int(item.get("retry_delay"), DEFAULT_TRANSMISSION["retry_delay"], 0),
        }

    def _load_legacy_transmission(self, raw_config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            backend_type = self._normalize_downloader_type(raw_config.get("backend_type", DEFAULT_TRANSMISSION["backend_type"]))
        except Exception:
            backend_type = "transmission"
        return {
            "backend_type": backend_type,
            "host": str(raw_config.get("host", DEFAULT_TRANSMISSION["host"])),
            "username": str(raw_config.get("username", DEFAULT_TRANSMISSION["username"])),
            "password": str(raw_config.get("password", DEFAULT_TRANSMISSION["password"])),
            "request_timeout": self._normalize_positive_int(raw_config.get("request_timeout"), DEFAULT_TRANSMISSION["request_timeout"], 1),
            "max_retries": self._normalize_positive_int(raw_config.get("max_retries"), DEFAULT_TRANSMISSION["max_retries"], 1),
            "retry_delay": self._normalize_positive_int(raw_config.get("retry_delay"), DEFAULT_TRANSMISSION["retry_delay"], 0),
        }

    def _load_downloader_profiles(self, raw_config: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
        legacy = self._load_legacy_transmission(raw_config)

        profiles: List[Dict[str, Any]] = []
        seen_ids = set()
        raw_json = str(raw_config.get("downloaders_json") or "").strip()
        if raw_json:
            try:
                parsed = json.loads(raw_json)
            except Exception:
                parsed = []
            if isinstance(parsed, list):
                for idx, item in enumerate(parsed, start=1):
                    if not isinstance(item, dict):
                        continue
                    try:
                        profile = self._normalize_downloader_profile(item, fallback_id=f"dl_{idx}", fallback_name=f"下载器{idx}")
                    except Exception:
                        continue
                    if profile["id"] in seen_ids:
                        continue
                    seen_ids.add(profile["id"])
                    profiles.append(profile)

        if not profiles:
            default_profile = self._normalize_downloader_profile(
                {
                    "id": "default",
                    "name": "默认下载器",
                    **legacy,
                },
                fallback_id="default",
                fallback_name="默认下载器",
            )
            profiles = [default_profile]
            seen_ids = {default_profile["id"]}

        active_id = self._normalize_downloader_id(raw_config.get("active_downloader_id"), fallback=profiles[0]["id"])
        if active_id not in seen_ids:
            active_id = profiles[0]["id"]
        return profiles, active_id

    def _list_schedules_by_tenant_id(self, conn: sqlite3.Connection, tenant_id: int) -> List[Dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT id, schedule_name, mode, downloader_id, keywords_json, run_time, timezone, enabled, last_run_date, created_at, updated_at
            FROM tenant_download_schedules
            WHERE tenant_id = ?
            ORDER BY id DESC
            """,
            (tenant_id,),
        ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            keywords: List[str] = []
            try:
                parsed = json.loads(row["keywords_json"] or "[]")
                if isinstance(parsed, list):
                    keywords = [str(v).strip() for v in parsed if str(v).strip()]
            except Exception:
                keywords = []
            result.append(
                {
                    "id": int(row["id"]),
                    "schedule_name": str(row["schedule_name"]),
                    "mode": str(row["mode"]),
                    "downloader_id": str(row["downloader_id"] or ""),
                    "keywords": keywords,
                    "run_time": str(row["run_time"]),
                    "timezone": str(row["timezone"] or DEFAULT_SCHEDULE_TZ),
                    "enabled": 1 if int(row["enabled"] or 0) == 1 else 0,
                    "last_run_date": str(row["last_run_date"] or ""),
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                }
            )
        return result

    def ensure_default_tenant(self, seed: Optional[Dict[str, Any]] = None):
        seed = seed or {}
        transmission_seed = {**DEFAULT_TRANSMISSION, **(seed.get("transmission") or {})}
        rss_seed = dict(DEFAULT_RSS_MODES)
        rss_seed.update(seed.get("rss_modes") or {})
        with self._lock, self._connect() as conn:
            now = self._now()
            cur = conn.execute(
                "INSERT OR IGNORE INTO tenants(tenant_key, tenant_name, tenant_status, created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
                ("default", "默认租户", now, now),
            )
            # Preserve existing tenant configuration. Seed defaults only on first creation.
            if int(cur.rowcount or 0) == 0:
                return

            tenant_id = self._get_tenant_id(conn, "default")
            if tenant_id is None:
                return
            for key, value in transmission_seed.items():
                self._upsert_config(conn, tenant_id, key, str(value))
            default_profile = self._normalize_downloader_profile(
                {"id": "default", "name": "默认下载器", **transmission_seed},
                fallback_id="default",
                fallback_name="默认下载器",
            )
            self._upsert_config(conn, tenant_id, "downloaders_json", json.dumps([default_profile], ensure_ascii=False))
            self._upsert_config(conn, tenant_id, "active_downloader_id", default_profile["id"])

            for mode, mode_data in rss_seed.items():
                self._upsert_mode(conn, tenant_id, mode, mode_data)
            conn.execute("UPDATE tenants SET updated_at = ? WHERE id = ?", (self._now(), tenant_id))

    def ensure_default_identities(
        self,
        admin_username: str = "admin",
        admin_password: str = "admin",
        tenant_key: str = "default",
        tenant_username: str = "admin",
        tenant_password: str = "admin",
    ):
        admin_username = (admin_username or "admin").strip()
        tenant_key = (tenant_key or "default").strip().lower() or "default"
        tenant_username = (tenant_username or "admin").strip()
        now = self._now()
        with self._lock, self._connect() as conn:
            admin_row = conn.execute(
                "SELECT id FROM admin_accounts WHERE username = ?",
                (admin_username,),
            ).fetchone()
            if not admin_row:
                conn.execute(
                    """
                    INSERT INTO admin_accounts(username, password_hash, account_status, created_at, updated_at)
                    VALUES (?, ?, 'active', ?, ?)
                    """,
                    (admin_username, generate_password_hash(admin_password), now, now),
                )

            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                return
            user_row = conn.execute(
                "SELECT id FROM tenant_users WHERE tenant_id = ? AND username = ?",
                (tenant_id, tenant_username),
            ).fetchone()
            if not user_row:
                conn.execute(
                    """
                    INSERT INTO tenant_users(tenant_id, username, password_hash, user_role, account_status, created_at, updated_at)
                    VALUES (?, ?, ?, 'user', 'active', ?, ?)
                    """,
                    (tenant_id, tenant_username, generate_password_hash(tenant_password), now, now),
                )

    def verify_admin_login(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        normalized = (username or "").strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash, account_status FROM admin_accounts WHERE username = ?",
                (normalized,),
            ).fetchone()
            if not row:
                return None
            if row["account_status"] != "active":
                return None
            if not check_password_hash(row["password_hash"], password or ""):
                return None
            return {"id": int(row["id"]), "username": row["username"], "role": "admin"}

    def verify_tenant_login(self, tenant_key: str, username: str, password: str) -> Optional[Dict[str, Any]]:
        normalized_tenant = (tenant_key or "").strip().lower()
        normalized_user = (username or "").strip()
        with self._connect() as conn:
            tenant_row = self._get_tenant_row(conn, normalized_tenant)
            if tenant_row is None:
                return None
            if tenant_row["tenant_status"] != "active":
                return None
            user_row = conn.execute(
                """
                SELECT id, username, password_hash, user_role, account_status
                FROM tenant_users
                WHERE tenant_id = ? AND username = ?
                LIMIT 1
                """,
                (int(tenant_row["id"]), normalized_user),
            ).fetchone()
            if not user_row:
                return None
            if user_row["account_status"] != "active":
                return None
            if not check_password_hash(user_row["password_hash"], password or ""):
                return None
            return {
                "id": int(user_row["id"]),
                "username": str(user_row["username"]),
                "tenant_key": normalized_tenant,
                "tenant_name": str(tenant_row["tenant_name"]),
                "role": "tenant_user",
                "user_role": str(user_row["user_role"]),
            }

    def verify_tenant_login_auto(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        normalized_user = (username or "").strip()
        if not normalized_user:
            return None

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    u.id AS user_id,
                    u.username,
                    u.password_hash,
                    u.user_role,
                    u.account_status,
                    t.tenant_key,
                    t.tenant_name,
                    t.tenant_status
                FROM tenant_users u
                JOIN tenants t ON t.id = u.tenant_id
                WHERE u.username = ?
                """,
                (normalized_user,),
            ).fetchall()

            matched: List[sqlite3.Row] = []
            for row in rows:
                if row["account_status"] != "active":
                    continue
                if row["tenant_status"] != "active":
                    continue
                if check_password_hash(row["password_hash"], password or ""):
                    matched.append(row)

            if not matched:
                return None
            if len(matched) > 1:
                raise ValueError("该用户名对应多个租户，请联系管理员处理账号冲突")

            row = matched[0]
            return {
                "id": int(row["user_id"]),
                "username": str(row["username"]),
                "tenant_key": str(row["tenant_key"]),
                "tenant_name": str(row["tenant_name"]),
                "role": "tenant_user",
                "user_role": str(row["user_role"]),
            }

    def change_tenant_user_password(
        self,
        tenant_key: str,
        username: str,
        old_password: str,
        new_password: str,
        actor: str = "system",
    ):
        normalized_tenant = (tenant_key or "").strip().lower()
        normalized_user = (username or "").strip()
        if not normalized_tenant or not normalized_user:
            raise ValueError("账号信息无效")
        if len(new_password or "") < 6:
            raise ValueError("新密码至少 6 位")

        with self._lock, self._connect() as conn:
            tenant_row = self._get_tenant_row(conn, normalized_tenant)
            if tenant_row is None:
                raise ValueError("租户不存在")
            tenant_id = int(tenant_row["id"])

            user_row = conn.execute(
                """
                SELECT id, password_hash, account_status
                FROM tenant_users
                WHERE tenant_id = ? AND username = ?
                LIMIT 1
                """,
                (tenant_id, normalized_user),
            ).fetchone()
            if user_row is None:
                raise ValueError("用户不存在")
            if user_row["account_status"] != "active":
                raise ValueError("用户已停用")
            if not check_password_hash(user_row["password_hash"], old_password or ""):
                raise ValueError("旧密码错误")

            now = self._now()
            conn.execute(
                "UPDATE tenant_users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (generate_password_hash(new_password), now, int(user_row["id"])),
            )
            self._log_audit(
                conn,
                tenant_id,
                action="tenant.user.password.change",
                actor=actor,
                detail={"username": normalized_user},
            )

    def change_admin_password(
        self,
        username: str,
        old_password: str,
        new_password: str,
        sync_default_tenant: bool = True,
        default_tenant_key: str = "default",
        default_tenant_username: str = "admin",
        actor: str = "system",
    ) -> Dict[str, Any]:
        normalized_user = (username or "").strip()
        normalized_tenant_key = (default_tenant_key or "default").strip().lower() or "default"
        normalized_tenant_user = (default_tenant_username or "admin").strip() or "admin"

        if not normalized_user:
            raise ValueError("管理员账号无效")
        if len(new_password or "") < 6:
            raise ValueError("新密码至少 6 位")

        with self._lock, self._connect() as conn:
            admin_row = conn.execute(
                """
                SELECT id, password_hash, account_status
                FROM admin_accounts
                WHERE username = ?
                LIMIT 1
                """,
                (normalized_user,),
            ).fetchone()
            if admin_row is None:
                raise ValueError("管理员账号不存在")
            if admin_row["account_status"] != "active":
                raise ValueError("管理员账号已停用")
            if not check_password_hash(admin_row["password_hash"], old_password or ""):
                raise ValueError("旧密码错误")

            now = self._now()
            conn.execute(
                "UPDATE admin_accounts SET password_hash = ?, updated_at = ? WHERE id = ?",
                (generate_password_hash(new_password), now, int(admin_row["id"])),
            )

            result = {"admin_updated": True, "default_tenant_synced": False}
            if not sync_default_tenant:
                return result

            tenant_row = self._get_tenant_row(conn, normalized_tenant_key)
            if tenant_row is None:
                raise ValueError(f"默认租户不存在: {normalized_tenant_key}")
            tenant_id = int(tenant_row["id"])
            tenant_user_row = conn.execute(
                """
                SELECT id, account_status
                FROM tenant_users
                WHERE tenant_id = ? AND username = ?
                LIMIT 1
                """,
                (tenant_id, normalized_tenant_user),
            ).fetchone()
            if tenant_user_row is None:
                raise ValueError(f"默认租户账号不存在: {normalized_tenant_user}")
            if tenant_user_row["account_status"] != "active":
                raise ValueError("默认租户账号已停用，无法同步密码")

            conn.execute(
                "UPDATE tenant_users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (generate_password_hash(new_password), now, int(tenant_user_row["id"])),
            )
            self._log_audit(
                conn,
                tenant_id,
                action="admin.password.sync_default_tenant",
                actor=actor,
                detail={"admin_username": normalized_user, "username": normalized_tenant_user},
            )
            result["default_tenant_synced"] = True
            return result

    def register_tenant_with_user(
        self,
        tenant_key: str,
        tenant_name: str,
        username: str,
        password: str,
        copy_from: str = "",
        actor: str = "self-register",
    ) -> Dict[str, Any]:
        normalized_user = (username or "").strip()
        if not normalized_user:
            raise ValueError("用户名不能为空")
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT id FROM tenant_users WHERE username = ? LIMIT 1",
                (normalized_user,),
            ).fetchone()
            if exists:
                raise ValueError("该用户名已被占用，请更换后重试")

        new_tenant = self.create_tenant(tenant_key, tenant_name, copy_from=copy_from, actor=actor)
        now = self._now()
        with self._lock, self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, new_tenant["tenant_key"])
            if tenant_id is None:
                raise ValueError("租户注册失败")
            conn.execute(
                """
                INSERT INTO tenant_users(tenant_id, username, password_hash, user_role, account_status, created_at, updated_at)
                VALUES (?, ?, ?, 'user', 'active', ?, ?)
                """,
                (tenant_id, normalized_user, generate_password_hash(password), now, now),
            )
            self._log_audit(
                conn,
                tenant_id,
                action="tenant.self_register",
                actor=actor,
                detail={"username": normalized_user},
            )
        return new_tenant

    def register_tenant_with_owner(
        self,
        tenant_key: str,
        tenant_name: str,
        owner_username: str,
        owner_password: str,
        copy_from: str = "",
        actor: str = "self-register",
    ) -> Dict[str, Any]:
        # Backward-compatible alias for previous API naming.
        return self.register_tenant_with_user(
            tenant_key=tenant_key,
            tenant_name=tenant_name,
            username=owner_username,
            password=owner_password,
            copy_from=copy_from,
            actor=actor,
        )

    def list_tenants(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT tenant_key, tenant_name, tenant_status, created_at, updated_at FROM tenants ORDER BY tenant_key ASC"
            ).fetchall()
        return [dict(row) for row in rows]
    def create_tenant(self, tenant_key: str, tenant_name: str, copy_from: str = "default", actor: str = "system") -> Dict[str, Any]:
        normalized_key = (tenant_key or "").strip().lower()
        normalized_name = (tenant_name or "").strip() or normalized_key
        if not normalized_key:
            raise ValueError("tenant_key 不能为空")
        template = self.get_tenant_config(copy_from) if copy_from else None

        with self._lock, self._connect() as conn:
            now = self._now()
            conn.execute(
                "INSERT INTO tenants(tenant_key, tenant_name, tenant_status, created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
                (normalized_key, normalized_name, now, now),
            )
            new_tenant_id = self._get_tenant_id(conn, normalized_key)
            if new_tenant_id is None:
                raise ValueError("创建租户失败")

            if template is None:
                # Create a clean tenant profile when not copying from another tenant.
                transmission = {
                    "backend_type": "transmission",
                    "host": "",
                    "username": "",
                    "password": "",
                    "request_timeout": DEFAULT_TRANSMISSION["request_timeout"],
                    "max_retries": DEFAULT_TRANSMISSION["max_retries"],
                    "retry_delay": DEFAULT_TRANSMISSION["retry_delay"],
                }
                downloaders = [
                    self._normalize_downloader_profile(
                        {
                            "id": "default",
                            "name": "默认下载器",
                            **transmission,
                        },
                        fallback_id="default",
                        fallback_name="默认下载器",
                    )
                ]
                active_downloader_id = downloaders[0]["id"]
                rss_modes = {}
                schedules: List[Dict[str, Any]] = []
            else:
                transmission = (template or {}).get("transmission", DEFAULT_TRANSMISSION)
                template_downloaders = (template or {}).get("downloaders") or []
                downloaders = []
                seen_ids = set()
                for idx, item in enumerate(template_downloaders, start=1):
                    if not isinstance(item, dict):
                        continue
                    profile = self._normalize_downloader_profile(item, fallback_id=f"dl_{idx}", fallback_name=f"下载器{idx}")
                    if profile["id"] in seen_ids:
                        continue
                    seen_ids.add(profile["id"])
                    downloaders.append(profile)
                if not downloaders:
                    downloaders = [
                        self._normalize_downloader_profile(
                            {"id": "default", "name": "默认下载器", **(transmission or {})},
                            fallback_id="default",
                            fallback_name="默认下载器",
                        )
                    ]
                active_downloader_id = self._normalize_downloader_id((template or {}).get("active_downloader_id"), fallback=downloaders[0]["id"])
                if active_downloader_id not in {d["id"] for d in downloaders}:
                    active_downloader_id = downloaders[0]["id"]
                rss_modes = (template or {}).get("rss_modes", DEFAULT_RSS_MODES)
                schedules = (template or {}).get("schedules", [])
            for key, value in transmission.items():
                self._upsert_config(conn, new_tenant_id, key, str(value))
            self._upsert_config(conn, new_tenant_id, "downloaders_json", json.dumps(downloaders, ensure_ascii=False))
            self._upsert_config(conn, new_tenant_id, "active_downloader_id", active_downloader_id)
            for mode, mode_data in rss_modes.items():
                self._upsert_mode(conn, new_tenant_id, mode, mode_data)
            for schedule in schedules:
                mode_key = str((schedule or {}).get("mode") or "").strip().lower()
                if not mode_key:
                    continue
                schedule_downloader_id = self._normalize_downloader_id(
                    (schedule or {}).get("downloader_id"),
                    fallback=active_downloader_id,
                )
                if schedule_downloader_id not in {d["id"] for d in downloaders}:
                    schedule_downloader_id = active_downloader_id
                run_time = str((schedule or {}).get("run_time") or "03:00").strip()
                if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", run_time):
                    run_time = "03:00"
                keywords = self._normalize_schedule_keywords((schedule or {}).get("keywords", []))
                schedule_name = str((schedule or {}).get("schedule_name") or mode_key).strip() or mode_key
                enabled = 1 if int((schedule or {}).get("enabled", 1)) == 1 else 0
                conn.execute(
                    """
                    INSERT INTO tenant_download_schedules(
                        tenant_id, schedule_name, mode, downloader_id, keywords_json, run_time, timezone, enabled, last_run_date, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
                    """,
                    (
                        new_tenant_id,
                        schedule_name[:80],
                        mode_key,
                        schedule_downloader_id,
                        json.dumps(keywords, ensure_ascii=False),
                        run_time,
                        DEFAULT_SCHEDULE_TZ,
                        enabled,
                        now,
                        now,
                    ),
                )

            self._log_audit(
                conn,
                new_tenant_id,
                action="tenant.create",
                actor=actor,
                detail={"copy_from": copy_from or "", "tenant_name": normalized_name},
            )
            return {
                "tenant_key": normalized_key,
                "tenant_name": normalized_name,
                "tenant_status": "active",
                "created_at": now,
                "updated_at": now,
            }

    def get_tenant_config(self, tenant_key: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = self._get_tenant_row(conn, tenant_key)
            if not row:
                raise ValueError(f"租户不存在: {tenant_key}")
            tenant_id = int(row["id"])

            conf_rows = conn.execute("SELECT config_key, config_value FROM tenant_configs WHERE tenant_id = ?", (tenant_id,)).fetchall()
            raw_config = {r["config_key"]: r["config_value"] for r in conf_rows}
            downloaders, active_downloader_id = self._load_downloader_profiles(raw_config)
            active_downloader = next((d for d in downloaders if d.get("id") == active_downloader_id), downloaders[0])
            transmission = {
                "backend_type": str(active_downloader.get("backend_type") or DEFAULT_TRANSMISSION["backend_type"]),
                "host": str(active_downloader.get("host") or DEFAULT_TRANSMISSION["host"]),
                "username": str(active_downloader.get("username") or DEFAULT_TRANSMISSION["username"]),
                "password": str(active_downloader.get("password") or DEFAULT_TRANSMISSION["password"]),
                "request_timeout": self._normalize_positive_int(
                    active_downloader.get("request_timeout"),
                    DEFAULT_TRANSMISSION["request_timeout"],
                    1,
                ),
                "max_retries": self._normalize_positive_int(
                    active_downloader.get("max_retries"),
                    DEFAULT_TRANSMISSION["max_retries"],
                    1,
                ),
                "retry_delay": self._normalize_positive_int(
                    active_downloader.get("retry_delay"),
                    DEFAULT_TRANSMISSION["retry_delay"],
                    0,
                ),
            }

            mode_rows = conn.execute(
                "SELECT mode, mode_name, rss_url, download_dir, enabled FROM tenant_rss_modes WHERE tenant_id = ? ORDER BY mode ASC",
                (tenant_id,),
            ).fetchall()
            rss_modes: Dict[str, Dict[str, Any]] = {}
            for mr in mode_rows:
                rss_modes[mr["mode"]] = {
                    "mode_name": mr["mode_name"],
                    "rss_url": mr["rss_url"],
                    "download_dir": mr["download_dir"],
                    "enabled": int(mr["enabled"]),
                }

            active_key_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM tenant_api_keys WHERE tenant_id = ? AND key_status = 'active'",
                    (tenant_id,),
                ).fetchone()["c"]
            )
            schedules = self._list_schedules_by_tenant_id(conn, tenant_id)

            return {
                "tenant_key": row["tenant_key"],
                "tenant_name": row["tenant_name"],
                "tenant_status": row["tenant_status"],
                "auth_required": active_key_count > 0,
                "active_api_key_count": active_key_count,
                "transmission": transmission,
                "downloaders": downloaders,
                "active_downloader_id": active_downloader_id,
                "rss_modes": rss_modes,
                "schedules": schedules,
            }

    def update_tenant_config(self, tenant_key: str, payload: Dict[str, Any], actor: str = "system") -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = self._get_tenant_row(conn, tenant_key)
            if row is None:
                raise ValueError(f"租户不存在: {tenant_key}")
            tenant_id = int(row["id"])

            new_name = (payload.get("tenant_name") or "").strip()
            if new_name:
                conn.execute("UPDATE tenants SET tenant_name = ?, updated_at = ? WHERE id = ?", (new_name, self._now(), tenant_id))

            if payload.get("tenant_status") in {"active", "disabled"}:
                conn.execute(
                    "UPDATE tenants SET tenant_status = ?, updated_at = ? WHERE id = ?",
                    (payload["tenant_status"], self._now(), tenant_id),
                )

            raw_rows = conn.execute(
                "SELECT config_key, config_value FROM tenant_configs WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
            existing_raw_config = {str(r["config_key"]): r["config_value"] for r in raw_rows}
            existing_downloaders, existing_active_downloader_id = self._load_downloader_profiles(existing_raw_config)

            transmission_payload = payload.get("transmission")
            downloaders_payload = payload.get("downloaders")
            active_downloader_id_payload = payload.get("active_downloader_id")

            transmission_touched = isinstance(transmission_payload, dict)
            downloaders_touched = isinstance(downloaders_payload, list)
            active_downloader_touched = active_downloader_id_payload is not None

            next_downloaders: List[Dict[str, Any]] = [dict(item) for item in existing_downloaders]
            next_active_downloader_id = str(existing_active_downloader_id or "").strip() or (next_downloaders[0]["id"] if next_downloaders else "")

            if downloaders_touched:
                normalized_downloaders: List[Dict[str, Any]] = []
                seen_ids = set()
                for idx, item in enumerate(downloaders_payload, start=1):
                    if not isinstance(item, dict):
                        continue
                    profile = self._normalize_downloader_profile(item, fallback_id=f"dl_{idx}", fallback_name=f"下载器{idx}")
                    if profile["id"] in seen_ids:
                        continue
                    seen_ids.add(profile["id"])
                    normalized_downloaders.append(profile)
                if not normalized_downloaders:
                    raise ValueError("至少保留一个下载器")
                next_downloaders = normalized_downloaders
                requested_active = self._normalize_downloader_id(active_downloader_id_payload, fallback="")
                if requested_active and requested_active in {d["id"] for d in next_downloaders}:
                    next_active_downloader_id = requested_active
                elif next_active_downloader_id not in {d["id"] for d in next_downloaders}:
                    next_active_downloader_id = next_downloaders[0]["id"]

            if active_downloader_touched and not downloaders_touched:
                requested_active = self._normalize_downloader_id(active_downloader_id_payload, fallback="")
                if requested_active and requested_active not in {d["id"] for d in next_downloaders}:
                    raise ValueError("选择的下载器不存在")
                if requested_active:
                    next_active_downloader_id = requested_active

            if transmission_touched:
                if not next_downloaders:
                    next_downloaders = [
                        self._normalize_downloader_profile(
                            {"id": "default", "name": "默认下载器", **self._load_legacy_transmission(existing_raw_config)},
                            fallback_id="default",
                            fallback_name="默认下载器",
                        )
                    ]
                    next_active_downloader_id = next_downloaders[0]["id"]

                target_id = next_active_downloader_id if next_active_downloader_id else next_downloaders[0]["id"]
                updated_downloaders: List[Dict[str, Any]] = []
                found_target = False
                for profile in next_downloaders:
                    current = dict(profile)
                    if current.get("id") == target_id:
                        found_target = True
                        merged = dict(current)
                        for key in DEFAULT_TRANSMISSION.keys():
                            if key not in transmission_payload:
                                continue
                            incoming = transmission_payload.get(key)
                            if key == "backend_type":
                                merged[key] = self._normalize_downloader_type(incoming)
                            elif key in {"request_timeout", "max_retries"}:
                                merged[key] = self._normalize_positive_int(incoming, merged.get(key, DEFAULT_TRANSMISSION[key]), 1)
                            elif key == "retry_delay":
                                merged[key] = self._normalize_positive_int(incoming, merged.get(key, DEFAULT_TRANSMISSION[key]), 0)
                            else:
                                merged[key] = str(incoming or "")
                        current = self._normalize_downloader_profile(merged, fallback_id=current.get("id") or "default", fallback_name=current.get("name") or "")
                    updated_downloaders.append(current)
                if not found_target and updated_downloaders:
                    next_active_downloader_id = updated_downloaders[0]["id"]
                next_downloaders = updated_downloaders

            downloader_config_changed = transmission_touched or downloaders_touched or active_downloader_touched
            if downloader_config_changed:
                if not next_downloaders:
                    raise ValueError("至少保留一个下载器")
                valid_ids = {d["id"] for d in next_downloaders}
                if next_active_downloader_id not in valid_ids:
                    next_active_downloader_id = next_downloaders[0]["id"]

                active_profile = next((d for d in next_downloaders if d["id"] == next_active_downloader_id), next_downloaders[0])
                self._upsert_config(conn, tenant_id, "downloaders_json", json.dumps(next_downloaders, ensure_ascii=False))
                self._upsert_config(conn, tenant_id, "active_downloader_id", next_active_downloader_id)
                for key in DEFAULT_TRANSMISSION.keys():
                    self._upsert_config(conn, tenant_id, key, str(active_profile.get(key, DEFAULT_TRANSMISSION[key])))

            rss_modes_payload = payload.get("rss_modes")
            rss_modes = rss_modes_payload if isinstance(rss_modes_payload, dict) else {}
            rss_modes_touched = isinstance(rss_modes_payload, dict)
            rss_modes_replace = bool(payload.get("rss_modes_replace")) and rss_modes_touched

            normalized_modes: Dict[str, Dict[str, Any]] = {}
            for mode, mode_data in rss_modes.items():
                mode_key = str(mode or "").strip().lower()
                if not mode_key:
                    continue
                if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", mode_key):
                    raise ValueError(f"RSS 模式 key 无效: {mode_key}")

                incoming = mode_data or {}
                merged_mode = {
                    "mode_name": str(incoming.get("mode_name", mode_key)).strip() or mode_key,
                    "rss_url": str(incoming.get("rss_url", "")).strip(),
                    "download_dir": str(incoming.get("download_dir", "")).strip() or "/downloads",
                    "enabled": 1 if int(incoming.get("enabled", 1)) == 1 else 0,
                }
                normalized_modes[mode_key] = merged_mode
                self._upsert_mode(conn, tenant_id, mode_key, merged_mode)

            if rss_modes_replace:
                keep_modes = tuple(normalized_modes.keys())
                if keep_modes:
                    placeholders = ",".join(["?"] * len(keep_modes))
                    conn.execute(
                        f"DELETE FROM tenant_rss_modes WHERE tenant_id = ? AND mode NOT IN ({placeholders})",
                        (tenant_id, *keep_modes),
                    )
                else:
                    conn.execute("DELETE FROM tenant_rss_modes WHERE tenant_id = ?", (tenant_id,))

            schedules_payload = payload.get("schedules")
            schedules_touched = isinstance(schedules_payload, list)
            schedules_replace = bool(payload.get("schedules_replace")) and schedules_touched
            normalized_schedules: List[Dict[str, Any]] = []
            if schedules_touched:
                def schedule_signature(schedule_name: str, mode: str, downloader_id: str, run_time: str, keywords_json: str) -> str:
                    return "||".join(
                        [
                            str(schedule_name or "").strip(),
                            str(mode or "").strip().lower(),
                            str(downloader_id or "").strip(),
                            str(run_time or "").strip(),
                            str(keywords_json or "[]").strip(),
                        ]
                    )

                existing_schedule_rows = conn.execute(
                    """
                    SELECT id, schedule_name, mode, downloader_id, run_time, keywords_json, last_run_date
                    FROM tenant_download_schedules
                    WHERE tenant_id = ?
                    """,
                    (tenant_id,),
                ).fetchall()
                existing_schedule_by_id: Dict[int, Dict[str, Any]] = {}
                existing_last_run_by_signature: Dict[str, str] = {}
                for row in existing_schedule_rows:
                    sid = int(row["id"] or 0)
                    signature = schedule_signature(
                        str(row["schedule_name"] or ""),
                        str(row["mode"] or ""),
                        str(row["downloader_id"] or ""),
                        str(row["run_time"] or ""),
                        str(row["keywords_json"] or "[]"),
                    )
                    last_run_date = str(row["last_run_date"] or "")
                    if sid > 0:
                        existing_schedule_by_id[sid] = {"signature": signature, "last_run_date": last_run_date}
                    if signature and last_run_date:
                        existing_last_run_by_signature[signature] = last_run_date

                mode_rows = conn.execute(
                    "SELECT mode FROM tenant_rss_modes WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchall()
                valid_modes = {str(r["mode"]).strip().lower() for r in mode_rows if str(r["mode"]).strip()}
                valid_downloader_ids = {str((d or {}).get("id") or "").strip() for d in next_downloaders if str((d or {}).get("id") or "").strip()}
                default_schedule_downloader_id = next_active_downloader_id if next_active_downloader_id in valid_downloader_ids else (next_downloaders[0]["id"] if next_downloaders else "")

                for raw in schedules_payload:
                    if not isinstance(raw, dict):
                        continue
                    mode_key = str(raw.get("mode") or "").strip().lower()
                    if not mode_key:
                        continue
                    if mode_key not in valid_modes:
                        raise ValueError(f"自动下载任务模式不存在: {mode_key}")
                    schedule_downloader_id = self._normalize_downloader_id(raw.get("downloader_id"), fallback=default_schedule_downloader_id)
                    if schedule_downloader_id not in valid_downloader_ids:
                        schedule_downloader_id = default_schedule_downloader_id
                    run_time = self._normalize_schedule_time(raw.get("run_time"))
                    keywords = self._normalize_schedule_keywords(raw.get("keywords"))
                    schedule_name = str(raw.get("schedule_name") or mode_key).strip() or mode_key
                    schedule_id = int(raw.get("id") or 0)
                    keywords_json = json.dumps(keywords, ensure_ascii=False)
                    signature = schedule_signature(schedule_name[:80], mode_key, schedule_downloader_id, run_time, keywords_json)
                    keep_last_run_date = ""
                    existing_by_id = existing_schedule_by_id.get(schedule_id)
                    if existing_by_id and existing_by_id.get("signature") == signature:
                        keep_last_run_date = str(existing_by_id.get("last_run_date") or "")
                    elif signature in existing_last_run_by_signature:
                        keep_last_run_date = str(existing_last_run_by_signature.get(signature) or "")
                    normalized_schedules.append(
                        {
                            "id": schedule_id,
                            "schedule_name": schedule_name[:80],
                            "mode": mode_key,
                            "downloader_id": schedule_downloader_id,
                            "keywords_json": keywords_json,
                            "run_time": run_time,
                            "timezone": DEFAULT_SCHEDULE_TZ,
                            "enabled": 1 if int(raw.get("enabled", 1)) == 1 else 0,
                            "last_run_date": keep_last_run_date,
                        }
                    )

                if schedules_replace:
                    conn.execute("DELETE FROM tenant_download_schedules WHERE tenant_id = ?", (tenant_id,))

                for schedule in normalized_schedules:
                    conn.execute(
                        """
                        INSERT INTO tenant_download_schedules(
                            tenant_id, schedule_name, mode, downloader_id, keywords_json, run_time, timezone, enabled, last_run_date, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tenant_id,
                            schedule["schedule_name"],
                            schedule["mode"],
                            schedule["downloader_id"],
                            schedule["keywords_json"],
                            schedule["run_time"],
                            schedule["timezone"],
                            schedule["enabled"],
                            schedule.get("last_run_date", ""),
                            self._now(),
                            self._now(),
                        ),
                    )

            conn.execute("UPDATE tenants SET updated_at = ? WHERE id = ?", (self._now(), tenant_id))
            self._log_audit(
                conn,
                tenant_id,
                action="tenant.config.update",
                actor=actor,
                detail={
                    "updated_name": bool(new_name),
                    "updated_status": payload.get("tenant_status") if payload.get("tenant_status") in {"active", "disabled"} else "",
                    "updated_transmission": transmission_touched,
                    "updated_downloaders": downloaders_touched,
                    "updated_active_downloader": active_downloader_touched,
                    "downloader_count": len(next_downloaders) if (transmission_touched or downloaders_touched or active_downloader_touched) else 0,
                    "updated_modes": list(normalized_modes.keys()) if rss_modes_touched else [],
                    "rss_modes_replace": rss_modes_replace,
                    "updated_schedules": len(normalized_schedules) if schedules_touched else 0,
                    "schedules_replace": schedules_replace,
                },
            )
        return self.get_tenant_config(tenant_key)

    def set_tenant_status(self, tenant_key: str, tenant_status: str, actor: str = "system") -> Dict[str, Any]:
        if tenant_status not in {"active", "disabled"}:
            raise ValueError("tenant_status 必须是 active 或 disabled")

        with self._lock, self._connect() as conn:
            row = self._get_tenant_row(conn, tenant_key)
            if row is None:
                raise ValueError(f"租户不存在: {tenant_key}")
            tenant_id = int(row["id"])

            conn.execute("UPDATE tenants SET tenant_status = ?, updated_at = ? WHERE id = ?", (tenant_status, self._now(), tenant_id))
            self._log_audit(conn, tenant_id, action="tenant.status.update", actor=actor, detail={"tenant_status": tenant_status})

        return self.get_tenant_config(tenant_key)

    def list_due_download_schedules(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if now is None:
            now_utc = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now_utc = now.replace(tzinfo=FALLBACK_SCHEDULE_TZ).astimezone(timezone.utc)
        else:
            now_utc = now.astimezone(timezone.utc)

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.id, s.schedule_name, s.mode, s.downloader_id, s.keywords_json, s.run_time, s.timezone, s.enabled, s.last_run_date,
                    t.tenant_key, t.tenant_name, t.tenant_status
                FROM tenant_download_schedules s
                JOIN tenants t ON t.id = s.tenant_id
                WHERE s.enabled = 1
                ORDER BY s.id ASC
                """
            ).fetchall()

        due_items: List[Dict[str, Any]] = []
        for row in rows:
            if str(row["tenant_status"]) != "active":
                continue
            schedule_time = str(row["run_time"] or "").strip()
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule_time):
                continue

            tz_name = str(row["timezone"] or DEFAULT_SCHEDULE_TZ).strip() or DEFAULT_SCHEDULE_TZ
            try:
                tzinfo = ZoneInfo(tz_name)
            except Exception:
                tzinfo = FALLBACK_SCHEDULE_TZ

            local_now = now_utc.astimezone(tzinfo)
            current_hm = local_now.strftime("%H:%M")
            run_date = local_now.strftime("%Y-%m-%d")

            if current_hm < schedule_time:
                continue
            if str(row["last_run_date"] or "") == run_date:
                continue

            keywords: List[str] = []
            try:
                parsed_keywords = json.loads(row["keywords_json"] or "[]")
                if isinstance(parsed_keywords, list):
                    keywords = [str(v).strip() for v in parsed_keywords if str(v).strip()]
            except Exception:
                keywords = []

            due_items.append(
                {
                    "id": int(row["id"]),
                    "tenant_key": str(row["tenant_key"]),
                    "tenant_name": str(row["tenant_name"]),
                    "schedule_name": str(row["schedule_name"]),
                    "mode": str(row["mode"]),
                    "downloader_id": str(row["downloader_id"] or ""),
                    "keywords": keywords,
                    "run_time": schedule_time,
                    "timezone": str(row["timezone"] or DEFAULT_SCHEDULE_TZ),
                    "run_date": run_date,
                }
            )
        return due_items

    def claim_download_schedule_run(self, schedule_id: int, run_date: str) -> bool:
        normalized_date = str(run_date or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_date):
            raise ValueError("run_date 格式无效")

        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE tenant_download_schedules
                SET last_run_date = ?, updated_at = ?
                WHERE id = ? AND (last_run_date IS NULL OR last_run_date <> ?)
                """,
                (normalized_date, self._now(), int(schedule_id), normalized_date),
            )
            return int(cur.rowcount or 0) > 0

    def delete_tenant(self, tenant_key: str, hard_delete: bool = False, actor: str = "system") -> Dict[str, Any]:
        normalized_key = (tenant_key or "").strip().lower()
        if normalized_key == "default":
            raise ValueError("默认租户不允许删除")

        with self._lock, self._connect() as conn:
            row = self._get_tenant_row(conn, normalized_key)
            if row is None:
                raise ValueError(f"租户不存在: {normalized_key}")
            tenant_id = int(row["id"])

            if hard_delete:
                self._log_audit(conn, tenant_id, action="tenant.delete.hard", actor=actor, detail={"tenant_key": normalized_key})
                conn.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))
                return {"tenant_key": normalized_key, "deleted": True, "hard_delete": True}

            conn.execute("UPDATE tenants SET tenant_status = 'disabled', updated_at = ? WHERE id = ?", (self._now(), tenant_id))
            self._log_audit(conn, tenant_id, action="tenant.delete.soft", actor=actor, detail={"tenant_key": normalized_key})
            return {"tenant_key": normalized_key, "deleted": True, "hard_delete": False}
    def tenant_auth_required(self, tenant_key: str) -> bool:
        with self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                raise ValueError(f"租户不存在: {tenant_key}")
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM tenant_api_keys WHERE tenant_id = ? AND key_status = 'active'",
                (tenant_id,),
            ).fetchone()
            return int(row["c"] if row else 0) > 0

    def verify_api_key(self, tenant_key: str, api_key: str, touch: bool = True) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = self._get_tenant_row(conn, tenant_key)
            if row is None:
                raise ValueError(f"租户不存在: {tenant_key}")
            tenant_id = int(row["id"])

            active_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM tenant_api_keys WHERE tenant_id = ? AND key_status = 'active'",
                    (tenant_id,),
                ).fetchone()["c"]
            )
            if active_count == 0:
                return {
                    "tenant_key": tenant_key,
                    "tenant_id": tenant_id,
                    "role": "admin",
                    "key_id": 0,
                    "key_name": "open-access",
                    "auth_mode": "open",
                }

            hashed = self._hash_api_key(api_key)
            if not hashed:
                return None

            key_row = conn.execute(
                "SELECT id, key_name, role FROM tenant_api_keys WHERE tenant_id = ? AND key_hash = ? AND key_status = 'active' LIMIT 1",
                (tenant_id, hashed),
            ).fetchone()
            if not key_row:
                return None

            if touch:
                conn.execute(
                    "UPDATE tenant_api_keys SET last_used_at = ?, updated_at = ? WHERE id = ?",
                    (self._now(), self._now(), int(key_row["id"])),
                )

            return {
                "tenant_key": tenant_key,
                "tenant_id": tenant_id,
                "role": str(key_row["role"]),
                "key_id": int(key_row["id"]),
                "key_name": str(key_row["key_name"]),
                "auth_mode": "api-key",
            }

    def create_api_key(self, tenant_key: str, key_name: str, role: str = "admin", actor: str = "system") -> Dict[str, Any]:
        role = (role or "admin").strip().lower()
        if role not in {"admin", "viewer"}:
            raise ValueError("role 仅支持 admin 或 viewer")
        key_name = (key_name or "").strip() or f"{role}-key"

        raw_api_key = f"mtk_{secrets.token_urlsafe(24)}"
        key_hash = self._hash_api_key(raw_api_key)

        with self._lock, self._connect() as conn:
            row = self._get_tenant_row(conn, tenant_key)
            if row is None:
                raise ValueError(f"租户不存在: {tenant_key}")
            tenant_id = int(row["id"])
            now = self._now()

            cur = conn.execute(
                "INSERT INTO tenant_api_keys(tenant_id, key_name, key_hash, role, key_status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (tenant_id, key_name[:80], key_hash, role, now, now),
            )
            key_id = int(cur.lastrowid)
            self._log_audit(
                conn,
                tenant_id,
                action="tenant.api_key.create",
                actor=actor,
                detail={"key_id": key_id, "key_name": key_name[:80], "role": role},
            )
            return {
                "id": key_id,
                "key_name": key_name[:80],
                "role": role,
                "key_status": "active",
                "created_at": now,
                "api_key": raw_api_key,
            }

    def list_api_keys(self, tenant_key: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                raise ValueError(f"租户不存在: {tenant_key}")
            rows = conn.execute(
                "SELECT id, key_name, role, key_status, created_at, updated_at, last_used_at FROM tenant_api_keys WHERE tenant_id = ? ORDER BY id DESC",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke_api_key(self, tenant_key: str, key_id: int, actor: str = "system") -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                raise ValueError(f"租户不存在: {tenant_key}")
            row = conn.execute(
                "SELECT id, key_name, role FROM tenant_api_keys WHERE tenant_id = ? AND id = ?",
                (tenant_id, int(key_id)),
            ).fetchone()
            if row is None:
                raise ValueError(f"API Key 不存在: {key_id}")

            conn.execute(
                "UPDATE tenant_api_keys SET key_status = 'revoked', updated_at = ? WHERE tenant_id = ? AND id = ?",
                (self._now(), tenant_id, int(key_id)),
            )
            self._log_audit(
                conn,
                tenant_id,
                action="tenant.api_key.revoke",
                actor=actor,
                detail={"key_id": int(key_id), "key_name": row["key_name"], "role": row["role"]},
            )
            return {"id": int(key_id), "key_name": row["key_name"], "role": row["role"], "key_status": "revoked"}

    def save_history(self, tenant_key: str, task_id: str, payload: Dict[str, Any]):
        with self._lock, self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                raise ValueError(f"租户不存在: {tenant_key}")
            conn.execute(
                "INSERT INTO download_history(tenant_id, task_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (tenant_id, task_id, json.dumps(payload, ensure_ascii=False), self._now()),
            )

    def list_history(self, tenant_key: str, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                raise ValueError(f"租户不存在: {tenant_key}")
            safe_limit = max(1, min(int(limit), 200))
            safe_offset = max(0, int(offset))
            rows = conn.execute(
                "SELECT payload_json FROM download_history WHERE tenant_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (tenant_id, safe_limit, safe_offset),
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            try:
                result.append(json.loads(row["payload_json"]))
            except Exception:
                continue
        return result

    def count_history(self, tenant_key: str) -> int:
        with self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                return 0
            row = conn.execute("SELECT COUNT(*) AS c FROM download_history WHERE tenant_id = ?", (tenant_id,)).fetchone()
            return int(row["c"]) if row else 0

    def is_downloaded(self, tenant_key: str, title: str, enclosure_url: str) -> bool:
        with self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                return False
            row = conn.execute(
                "SELECT id FROM downloaded_items WHERE tenant_id = ? AND (title = ? OR enclosure_url = ?) LIMIT 1",
                (tenant_id, title, enclosure_url),
            ).fetchone()
            return row is not None

    def remember_downloaded(self, tenant_key: str, title: str, enclosure_url: str):
        with self._lock, self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                return
            conn.execute(
                "INSERT OR IGNORE INTO downloaded_items(tenant_id, title, enclosure_url, created_at) VALUES (?, ?, ?, ?)",
                (tenant_id, title, enclosure_url, self._now()),
            )
    def list_audit_logs(self, tenant_key: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                raise ValueError(f"租户不存在: {tenant_key}")
            rows = conn.execute(
                "SELECT action, actor, detail_json, created_at FROM tenant_audit_logs WHERE tenant_id = ? ORDER BY id DESC LIMIT ?",
                (tenant_id, max(1, min(int(limit), 200))),
            ).fetchall()

        result: List[Dict[str, Any]] = []
        for row in rows:
            detail = {}
            try:
                detail = json.loads(row["detail_json"])
            except Exception:
                detail = {}
            result.append({"action": row["action"], "actor": row["actor"], "detail": detail, "created_at": row["created_at"]})
        return result

    def _job_done(self, conn: sqlite3.Connection, job_key: str) -> bool:
        row = conn.execute("SELECT job_key FROM migration_jobs WHERE job_key = ?", (job_key,)).fetchone()
        return row is not None

    def migrate_legacy_history(self, tenant_key: str, history_file: Path, actor: str = "system") -> Dict[str, Any]:
        history_path = Path(history_file)
        if not history_path.exists():
            return {"job_key": "", "total": 0, "imported": 0, "skipped": 0, "message": "history.json 不存在"}

        try:
            payload = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"读取历史文件失败: {exc}")

        if not isinstance(payload, list):
            raise ValueError("history.json 格式错误，必须是数组")

        job_key = f"legacy-history::{tenant_key}::{history_path.resolve()}::{history_path.stat().st_mtime_ns}"

        with self._lock, self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                raise ValueError(f"租户不存在: {tenant_key}")

            if self._job_done(conn, job_key):
                return {"job_key": job_key, "total": len(payload), "imported": 0, "skipped": len(payload), "message": "该版本历史已迁移"}

            existing_rows = conn.execute("SELECT payload_json FROM download_history WHERE tenant_id = ?", (tenant_id,)).fetchall()
            existing_hashes = {hashlib.sha1(str(row["payload_json"]).encode("utf-8", errors="ignore")).hexdigest() for row in existing_rows}

            imported = 0
            skipped = 0
            for idx, item in enumerate(payload, start=1):
                if not isinstance(item, dict):
                    skipped += 1
                    continue

                raw_json = json.dumps(item, ensure_ascii=False, sort_keys=True)
                digest = hashlib.sha1(raw_json.encode("utf-8")).hexdigest()
                if digest in existing_hashes:
                    skipped += 1
                    continue

                task_id = str(item.get("task_id") or item.get("id") or f"legacy_{idx}")
                created_at = str(item.get("time") or self._now())
                conn.execute(
                    "INSERT INTO download_history(tenant_id, task_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                    (tenant_id, task_id, raw_json, created_at),
                )
                existing_hashes.add(digest)
                imported += 1

                for title in item.get("added_torrents") or []:
                    safe_title = str(title).strip()
                    if not safe_title:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO downloaded_items(tenant_id, title, enclosure_url, created_at) VALUES (?, ?, ?, ?)",
                        (tenant_id, safe_title, f"legacy://{safe_title}", self._now()),
                    )

            conn.execute(
                "INSERT INTO migration_jobs(job_key, meta_json, created_at) VALUES (?, ?, ?)",
                (
                    job_key,
                    json.dumps({"tenant_key": tenant_key, "file": str(history_path.resolve()), "total": len(payload)}, ensure_ascii=False),
                    self._now(),
                ),
            )

            self._log_audit(
                conn,
                tenant_id,
                action="history.migrate.legacy",
                actor=actor,
                detail={"total": len(payload), "imported": imported, "skipped": skipped, "file": str(history_path.resolve())},
            )

            return {"job_key": job_key, "total": len(payload), "imported": imported, "skipped": skipped, "message": "迁移完成"}

