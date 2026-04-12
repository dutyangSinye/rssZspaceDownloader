import hashlib
import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional
from werkzeug.security import check_password_hash, generate_password_hash


DEFAULT_TRANSMISSION = {
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
                CREATE INDEX IF NOT EXISTS idx_download_history_tenant ON download_history(tenant_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_downloaded_items_tenant ON downloaded_items(tenant_id);
                CREATE INDEX IF NOT EXISTS idx_tenant_audit_logs_tenant ON tenant_audit_logs(tenant_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_tenant_api_keys_tenant ON tenant_api_keys(tenant_id, key_status, id DESC);
                CREATE INDEX IF NOT EXISTS idx_tenant_users_tenant ON tenant_users(tenant_id, account_status, id DESC);
                """
            )
            self._ensure_column(conn, "tenants", "tenant_status", "TEXT NOT NULL DEFAULT 'active'")
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

    def ensure_default_tenant(self, seed: Optional[Dict[str, Any]] = None):
        seed = seed or {}
        transmission_seed = {**DEFAULT_TRANSMISSION, **(seed.get("transmission") or {})}
        rss_seed = dict(DEFAULT_RSS_MODES)
        rss_seed.update(seed.get("rss_modes") or {})
        with self._lock, self._connect() as conn:
            now = self._now()
            conn.execute(
                "INSERT OR IGNORE INTO tenants(tenant_key, tenant_name, tenant_status, created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
                ("default", "默认租户", now, now),
            )
            tenant_id = self._get_tenant_id(conn, "default")
            if tenant_id is None:
                return
            for key, value in transmission_seed.items():
                self._upsert_config(conn, tenant_id, key, str(value))
            for mode, mode_data in rss_seed.items():
                self._upsert_mode(conn, tenant_id, mode, mode_data)
            conn.execute("UPDATE tenants SET updated_at = ? WHERE id = ?", (self._now(), tenant_id))

    def ensure_default_identities(
        self,
        admin_username: str = "admin",
        admin_password: str = "yangyang83",
        tenant_key: str = "default",
        tenant_username: str = "admin",
        tenant_password: str = "yangyang83",
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

    def register_tenant_with_user(
        self,
        tenant_key: str,
        tenant_name: str,
        username: str,
        password: str,
        copy_from: str = "default",
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
        copy_from: str = "default",
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

            transmission = (template or {}).get("transmission", DEFAULT_TRANSMISSION)
            rss_modes = (template or {}).get("rss_modes", DEFAULT_RSS_MODES)
            for key, value in transmission.items():
                self._upsert_config(conn, new_tenant_id, key, str(value))
            for mode, mode_data in rss_modes.items():
                self._upsert_mode(conn, new_tenant_id, mode, mode_data)

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
            transmission = {
                "host": raw_config.get("host", DEFAULT_TRANSMISSION["host"]),
                "username": raw_config.get("username", DEFAULT_TRANSMISSION["username"]),
                "password": raw_config.get("password", DEFAULT_TRANSMISSION["password"]),
                "request_timeout": int(raw_config.get("request_timeout", DEFAULT_TRANSMISSION["request_timeout"])),
                "max_retries": int(raw_config.get("max_retries", DEFAULT_TRANSMISSION["max_retries"])),
                "retry_delay": int(raw_config.get("retry_delay", DEFAULT_TRANSMISSION["retry_delay"])),
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
            for mode, mode_data in DEFAULT_RSS_MODES.items():
                rss_modes.setdefault(mode, dict(mode_data))

            active_key_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM tenant_api_keys WHERE tenant_id = ? AND key_status = 'active'",
                    (tenant_id,),
                ).fetchone()["c"]
            )

            return {
                "tenant_key": row["tenant_key"],
                "tenant_name": row["tenant_name"],
                "tenant_status": row["tenant_status"],
                "auth_required": active_key_count > 0,
                "active_api_key_count": active_key_count,
                "transmission": transmission,
                "rss_modes": rss_modes,
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

            transmission = payload.get("transmission") or {}
            safe_transmission = {**DEFAULT_TRANSMISSION, **transmission}
            for key, value in safe_transmission.items():
                self._upsert_config(conn, tenant_id, key, str(value))

            rss_modes = payload.get("rss_modes") or {}
            for mode, mode_data in rss_modes.items():
                merged_mode = {**DEFAULT_RSS_MODES.get(mode, {}), **(mode_data or {})}
                if not str(merged_mode.get("download_dir", "")).strip():
                    merged_mode["download_dir"] = "/downloads"
                self._upsert_mode(conn, tenant_id, mode, merged_mode)

            conn.execute("UPDATE tenants SET updated_at = ? WHERE id = ?", (self._now(), tenant_id))
            self._log_audit(
                conn,
                tenant_id,
                action="tenant.config.update",
                actor=actor,
                detail={
                    "updated_name": bool(new_name),
                    "updated_status": payload.get("tenant_status") if payload.get("tenant_status") in {"active", "disabled"} else "",
                    "updated_transmission": bool(transmission),
                    "updated_modes": list(rss_modes.keys()),
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

    def list_history(self, tenant_key: str, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            tenant_id = self._get_tenant_id(conn, tenant_key)
            if tenant_id is None:
                raise ValueError(f"租户不存在: {tenant_key}")
            rows = conn.execute(
                "SELECT payload_json FROM download_history WHERE tenant_id = ? ORDER BY id DESC LIMIT ?",
                (tenant_id, max(1, min(int(limit), 200))),
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
