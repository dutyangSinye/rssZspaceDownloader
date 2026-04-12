# -*- coding: utf-8 -*-
"""M-TEAM Downloader with separated user/admin consoles."""

import re
import time
from threading import Lock, Thread
from typing import Dict, List, Optional
from uuid import uuid4

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from config.logging_config import setup_logging
from config.settings import Settings
from services.multi_tenant_download_service import MultiTenantDownloadService
from services.tenant_store import TenantStore
from services.transmission_client import TransmissionClient

logger = setup_logging("app")

app = Flask(
    __name__,
    template_folder=str(Settings.BASE_DIR / "web" / "templates"),
    static_folder=str(Settings.BASE_DIR / "web" / "static"),
)
app.config["JSON_AS_ASCII"] = False
app.secret_key = Settings.SECRET_KEY


def parse_keywords(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [v for v in re.split(r"[\s,，]+", text) if v]


def current_actor() -> str:
    if session.get("login_type") == "admin":
        return f"admin:{session.get('username', '')}"[:64]
    if session.get("login_type") == "tenant_user":
        return f"tenant:{session.get('tenant_key', '')}/{session.get('username', '')}"[:64]
    actor = (request.headers.get("X-Actor") or "").strip()
    if actor:
        return actor[:64]
    remote_ip = (request.remote_addr or "web-ui").strip()
    return remote_ip[:64]


class TaskRegistry:
    def __init__(self):
        self._lock = Lock()
        self._progress: Dict[str, Dict] = {}
        self._tenant_running: Dict[str, str] = {}
        self._task_tenant: Dict[str, str] = {}

    def start(self, tenant_key: str, task_id: str):
        with self._lock:
            self._tenant_running[tenant_key] = task_id
            self._task_tenant[task_id] = tenant_key

    def finish(self, tenant_key: str, task_id: str):
        with self._lock:
            if self._tenant_running.get(tenant_key) == task_id:
                self._tenant_running.pop(tenant_key, None)
            self._task_tenant.pop(task_id, None)

    def is_running(self, tenant_key: str) -> bool:
        with self._lock:
            return tenant_key in self._tenant_running

    def set_progress(self, task_id: str, payload: Dict):
        with self._lock:
            self._progress[task_id] = payload

    def get_progress(self, task_id: str, tenant_key: Optional[str] = None) -> Optional[Dict]:
        with self._lock:
            owner = self._task_tenant.get(task_id)
            if tenant_key and owner and owner != tenant_key:
                return None
            return self._progress.get(task_id)


store = TenantStore(Settings.DB_PATH)
store.ensure_default_tenant(Settings.default_tenant_seed())
store.ensure_default_identities(
    admin_username=Settings.DEFAULT_ADMIN_USERNAME,
    admin_password=Settings.DEFAULT_ADMIN_PASSWORD,
    tenant_key=Settings.DEFAULT_TENANT_KEY,
    tenant_username=Settings.DEFAULT_TENANT_USERNAME,
    tenant_password=Settings.DEFAULT_TENANT_PASSWORD,
)
download_service = MultiTenantDownloadService(store)
task_registry = TaskRegistry()


def validate_tenant_key(tenant_key: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", tenant_key or ""))


def normalize_tenant_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-_")
    return normalized[:64]


def auto_generate_tenant_key(seed: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "", str(seed or "").strip().lower())[:12]
    base = base or "tenant"
    for _ in range(10):
        key = f"{base}_{uuid4().hex[:6]}"
        if validate_tenant_key(key):
            return key
    return f"tenant_{uuid4().hex[:8]}"


def require_admin_json():
    if session.get("login_type") != "admin":
        return jsonify({"success": False, "message": "请先登录管理员账号"}), 401
    return None


def require_user_json():
    if session.get("login_type") != "tenant_user":
        return jsonify({"success": False, "message": "请先登录租户账号"}), 401
    tenant_key = (session.get("tenant_key") or "").strip().lower()
    if not tenant_key:
        session.clear()
        return jsonify({"success": False, "message": "登录状态失效，请重新登录"}), 401
    return None


def active_tenant_or_error(tenant_key: str) -> Dict:
    config = store.get_tenant_config(tenant_key)
    if config.get("tenant_status") != "active":
        raise ValueError(f"租户 {tenant_key} 已停用")
    return config


def current_user_tenant_key() -> str:
    return (session.get("tenant_key") or "").strip().lower()


@app.route("/")
def root():
    return redirect(url_for("user_login_page"))


@app.route("/user/login")
def user_login_page():
    return render_template("user_login.html")


@app.route("/admin/login")
def admin_login_page():
    return render_template("admin_login.html")


@app.route("/user/dashboard")
def user_dashboard_page():
    if session.get("login_type") != "tenant_user":
        return redirect(url_for("user_login_page"))
    return render_template("user_dashboard.html")


@app.route("/admin/dashboard")
def admin_dashboard_page():
    if session.get("login_type") != "admin":
        return redirect(url_for("admin_login_page"))
    return render_template("admin_dashboard.html")


@app.route("/api/user/register", methods=["POST"])
def api_user_register():
    data = request.get_json(silent=True) or {}
    raw_tenant_key = normalize_tenant_key(data.get("tenant_key") or "")
    tenant_name = (data.get("tenant_name") or "").strip()
    username = (data.get("username") or "").strip()
    password = str(data.get("password") or "")
    # Self-register tenants should start from clean configuration.
    # Admin-side creation can still explicitly pass copy_from=default.
    copy_from = (data.get("copy_from") or "").strip().lower()

    tenant_key = raw_tenant_key or auto_generate_tenant_key(tenant_name or username)
    if not tenant_name:
        tenant_name = tenant_key

    if not validate_tenant_key(tenant_key):
        return jsonify({"success": False, "message": "tenant_key 格式无效，仅支持小写字母/数字/_/-，长度2-64"})
    if not username:
        return jsonify({"success": False, "message": "用户名不能为空"})
    if len(password) < 6:
        return jsonify({"success": False, "message": "密码至少 6 位"})

    last_error = "注册失败"
    for _ in range(10):
        try:
            tenant = store.register_tenant_with_user(
                tenant_key=tenant_key,
                tenant_name=tenant_name,
                username=username,
                password=password,
                copy_from=copy_from,
                actor=f"self-register:{username}",
            )
            return jsonify({"success": True, "tenant": tenant})
        except Exception as exc:
            message = str(exc)
            last_error = message
            if raw_tenant_key:
                break
            if "UNIQUE constraint failed: tenants.tenant_key" in message:
                tenant_key = auto_generate_tenant_key(tenant_name or username)
                continue
            break
    return jsonify({"success": False, "message": last_error})


@app.route("/api/user/login", methods=["POST"])
def api_user_login():
    data = request.get_json(silent=True) or {}
    tenant_key = normalize_tenant_key(data.get("tenant_key") or "")
    username = (data.get("username") or "").strip()
    password = str(data.get("password") or "")

    if tenant_key and not validate_tenant_key(tenant_key):
        return jsonify({"success": False, "message": "tenant_key 格式无效"}), 400

    try:
        if tenant_key:
            login = store.verify_tenant_login(tenant_key, username, password)
        else:
            # Prefer default tenant first so the documented default account
            # remains predictable even when users have similarly named accounts.
            login = store.verify_tenant_login(Settings.DEFAULT_TENANT_KEY, username, password)
            if not login:
                login = store.verify_tenant_login_auto(username, password)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 409
    if not login:
        return jsonify({"success": False, "message": "账号或密码错误，或租户已停用"}), 401

    session.clear()
    session["login_type"] = "tenant_user"
    session["tenant_key"] = login["tenant_key"]
    session["tenant_name"] = login["tenant_name"]
    session["username"] = login["username"]
    return jsonify({"success": True, "next": "/user/dashboard", "tenant_key": login["tenant_key"], "tenant_name": login["tenant_name"]})


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = str(data.get("password") or "")

    login = store.verify_admin_login(username, password)
    if not login:
        return jsonify({"success": False, "message": "管理员账号或密码错误"}), 401

    session.clear()
    session["login_type"] = "admin"
    session["username"] = login["username"]
    return jsonify({"success": True, "next": "/admin/dashboard"})


@app.route("/api/user/logout", methods=["POST"])
def api_user_logout():
    session.clear()
    return jsonify({"success": True})


@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.clear()
    return jsonify({"success": True})


@app.route("/api/user/me", methods=["GET"])
def api_user_me():
    err = require_user_json()
    if err:
        return err
    return jsonify(
        {
            "success": True,
            "tenant_key": session.get("tenant_key"),
            "tenant_name": session.get("tenant_name"),
            "username": session.get("username"),
        }
    )


@app.route("/api/admin/me", methods=["GET"])
def api_admin_me():
    err = require_admin_json()
    if err:
        return err
    return jsonify({"success": True, "username": session.get("username")})


@app.route("/api/user/config", methods=["GET"])
def api_user_get_config():
    err = require_user_json()
    if err:
        return err
    try:
        config = store.get_tenant_config(current_user_tenant_key())
        return jsonify({"success": True, "config": config})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.route("/api/user/config", methods=["PUT"])
def api_user_update_config():
    err = require_user_json()
    if err:
        return err
    tenant_key = current_user_tenant_key()
    data = request.get_json(silent=True) or {}
    if "tenant_status" in data:
        data.pop("tenant_status", None)
    try:
        config = store.update_tenant_config(tenant_key, data, actor=current_actor())
        return jsonify({"success": True, "config": config})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.route("/api/user/password", methods=["PUT"])
def api_user_change_password():
    err = require_user_json()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    old_password = str(data.get("old_password") or "")
    new_password = str(data.get("new_password") or "")
    confirm_password = str(data.get("confirm_password") or "")

    if not old_password:
        return jsonify({"success": False, "message": "旧密码不能为空"})
    if len(new_password) < 6:
        return jsonify({"success": False, "message": "新密码至少 6 位"})
    if new_password != confirm_password:
        return jsonify({"success": False, "message": "两次输入的新密码不一致"})
    if old_password == new_password:
        return jsonify({"success": False, "message": "新密码不能与旧密码相同"})

    try:
        store.change_tenant_user_password(
            tenant_key=current_user_tenant_key(),
            username=str(session.get("username") or ""),
            old_password=old_password,
            new_password=new_password,
            actor=current_actor(),
        )
        return jsonify({"success": True, "message": "密码修改成功"})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.route("/api/user/status", methods=["GET"])
def api_user_status():
    err = require_user_json()
    if err:
        return err
    tenant_key = current_user_tenant_key()
    try:
        config = store.get_tenant_config(tenant_key)
        tr = config["transmission"]
        transmission_client = TransmissionClient(
            host=tr["host"],
            username=tr["username"],
            password=tr["password"],
            request_timeout=tr["request_timeout"],
            max_retries=tr["max_retries"],
            retry_delay=tr["retry_delay"],
        )
        history_count = store.count_history(tenant_key)
        return jsonify(
            {
                "success": True,
                "tenant_key": tenant_key,
                "tenant_name": config["tenant_name"],
                "tenant_status": config.get("tenant_status", "active"),
                "transmission_connected": transmission_client.test_connection(),
                "transmission_host": tr["host"],
                "running": task_registry.is_running(tenant_key),
                "history_count": history_count,
                "rss_modes": config.get("rss_modes", {}),
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.route("/api/user/transmission/test", methods=["POST"])
def api_user_test_transmission():
    err = require_user_json()
    if err:
        return err

    tenant_key = current_user_tenant_key()
    data = request.get_json(silent=True) or {}
    incoming = data.get("transmission") or {}

    try:
        config = store.get_tenant_config(tenant_key)
        base = config.get("transmission") or {}

        def to_int(value, default_value: int, min_value: int) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = int(default_value)
            return parsed if parsed >= min_value else min_value

        tr = {
            "host": str(incoming.get("host", base.get("host", ""))).strip(),
            "username": str(incoming.get("username", base.get("username", ""))).strip(),
            "password": str(incoming.get("password", base.get("password", ""))),
            "request_timeout": to_int(incoming.get("request_timeout", base.get("request_timeout", 30)), 30, 1),
            "max_retries": to_int(incoming.get("max_retries", base.get("max_retries", 3)), 3, 1),
            "retry_delay": to_int(incoming.get("retry_delay", base.get("retry_delay", 2)), 2, 0),
        }
        if not tr["host"]:
            return jsonify({"success": False, "message": "Transmission Host 不能为空", "transmission_connected": False})

        transmission_client = TransmissionClient(
            host=tr["host"],
            username=tr["username"],
            password=tr["password"],
            request_timeout=tr["request_timeout"],
            max_retries=tr["max_retries"],
            retry_delay=tr["retry_delay"],
        )
        connected = transmission_client.test_connection()
        return jsonify(
            {
                "success": True,
                "tenant_key": tenant_key,
                "transmission_host": tr["host"],
                "transmission_connected": connected,
                "message": "连接成功" if connected else "连接失败，请检查 Host/账号/密码",
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc), "transmission_connected": False})


@app.route("/api/user/history", methods=["GET"])
def api_user_history():
    err = require_user_json()
    if err:
        return err
    tenant_key = current_user_tenant_key()
    limit = request.args.get("limit", 20, type=int)
    try:
        history = store.list_history(tenant_key, limit=limit)
        return jsonify({"success": True, "history": history})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc), "history": []})


@app.route("/api/user/preview", methods=["POST"])
def api_user_preview():
    err = require_user_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    tenant_key = current_user_tenant_key()
    mode = (data.get("mode") or "movie").strip()

    try:
        config = active_tenant_or_error(tenant_key)
        items = download_service.fetch_rss_items(tenant_key, mode)
        mode_cfg = config["rss_modes"][mode]
        return jsonify({
            "success": True,
            "tenant_key": tenant_key,
            "mode": mode,
            "mode_name": mode_cfg.get("mode_name", mode),
            "count": len(items),
            "items": [item.to_dict() for item in items],
        })
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.route("/api/user/download", methods=["POST"])
def api_user_download():
    err = require_user_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    tenant_key = current_user_tenant_key()
    mode = (data.get("mode") or "movie").strip()
    keywords = parse_keywords(data.get("keywords"))

    if task_registry.is_running(tenant_key):
        return jsonify({"success": False, "message": "该租户已有任务执行中"})

    try:
        active_tenant_or_error(tenant_key)
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})

    task_id = f"{tenant_key}_{int(time.time())}_{uuid4().hex[:8]}"
    task_registry.start(tenant_key, task_id)
    task_registry.set_progress(task_id, {"status": "fetching", "message": "正在获取 RSS..."})

    def run_download():
        try:
            result = download_service.execute_download(
                tenant_key=tenant_key,
                mode=mode,
                keywords=keywords,
                task_id=task_id,
                progress_callback=lambda p: task_registry.set_progress(task_id, p),
            )
            task_registry.set_progress(task_id, {"status": "completed", "result": result})
        except Exception as exc:
            logger.exception("下载任务失败")
            task_registry.set_progress(task_id, {"status": "error", "message": str(exc)})
        finally:
            task_registry.finish(tenant_key, task_id)

    Thread(target=run_download, daemon=True).start()
    return jsonify({"success": True, "task_id": task_id, "tenant_key": tenant_key})


@app.route("/api/user/download-one", methods=["POST"])
def api_user_download_one():
    err = require_user_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    tenant_key = current_user_tenant_key()
    mode = (data.get("mode") or "movie").strip()
    url = (data.get("url") or "").strip()
    title = (data.get("title") or "").strip()

    if not url:
        return jsonify({"success": False, "message": "URL 不能为空"})

    try:
        result = download_service.add_single_torrent(tenant_key, mode, url, title=title)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.route("/api/user/progress/<task_id>", methods=["GET"])
def api_user_progress(task_id: str):
    err = require_user_json()
    if err:
        return err
    tenant_key = current_user_tenant_key()
    progress = task_registry.get_progress(task_id, tenant_key=tenant_key)
    if progress is None:
        return jsonify({"success": False, "message": "任务不存在"})
    return jsonify({"success": True, "progress": progress})


@app.route("/api/admin/tenants", methods=["GET"])
def api_admin_tenants():
    err = require_admin_json()
    if err:
        return err
    tenants = store.list_tenants()
    return jsonify({"success": True, "tenants": tenants, "default": "default"})


@app.route("/api/admin/tenants", methods=["POST"])
def api_admin_create_tenant():
    err = require_admin_json()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    tenant_key = (data.get("tenant_key") or "").strip().lower()
    tenant_name = (data.get("tenant_name") or "").strip()
    copy_from = (data.get("copy_from") or "default").strip().lower() or "default"
    user_username = (data.get("user_username") or data.get("owner_username") or "").strip()
    user_password = str(data.get("user_password") or data.get("owner_password") or "")

    if not validate_tenant_key(tenant_key):
        return jsonify({"success": False, "message": "tenant_key 格式无效，仅支持小写字母/数字/_/-，长度2-64"})

    try:
        if user_username and user_password:
            tenant = store.register_tenant_with_user(
                tenant_key=tenant_key,
                tenant_name=tenant_name or tenant_key,
                username=user_username,
                password=user_password,
                copy_from=copy_from,
                actor=current_actor(),
            )
        else:
            tenant = store.create_tenant(tenant_key, tenant_name or tenant_key, copy_from=copy_from, actor=current_actor())
        return jsonify({"success": True, "tenant": tenant})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.route("/api/admin/tenant/<tenant_key>/status", methods=["PATCH"])
def api_admin_update_tenant_status(tenant_key: str):
    err = require_admin_json()
    if err:
        return err

    normalized = tenant_key.strip().lower()
    if not validate_tenant_key(normalized):
        return jsonify({"success": False, "message": "tenant_key 无效"})

    data = request.get_json(silent=True) or {}
    tenant_status = (data.get("tenant_status") or "").strip().lower()

    try:
        config = store.set_tenant_status(normalized, tenant_status, actor=current_actor())
        return jsonify({"success": True, "config": config})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.route("/api/admin/tenant/<tenant_key>", methods=["DELETE"])
def api_admin_delete_tenant(tenant_key: str):
    err = require_admin_json()
    if err:
        return err

    normalized = tenant_key.strip().lower()
    if not validate_tenant_key(normalized):
        return jsonify({"success": False, "message": "tenant_key 无效"})

    hard_delete = str(request.args.get("hard", "false")).lower() in {"1", "true", "yes", "on"}
    try:
        if task_registry.is_running(normalized):
            return jsonify({"success": False, "message": "该租户有运行中任务，暂不能删除"})
        result = store.delete_tenant(normalized, hard_delete=hard_delete, actor=current_actor())
        return jsonify({"success": True, "result": result})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.route("/api/admin/tenant/<tenant_key>/audits", methods=["GET"])
def api_admin_tenant_audits(tenant_key: str):
    err = require_admin_json()
    if err:
        return err

    normalized = tenant_key.strip().lower()
    if not validate_tenant_key(normalized):
        return jsonify({"success": False, "message": "tenant_key 无效", "audits": []})

    limit = request.args.get("limit", 50, type=int)
    try:
        audits = store.list_audit_logs(normalized, limit=limit)
        return jsonify({"success": True, "audits": audits})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc), "audits": []})


@app.route("/api/admin/migrations/legacy-history", methods=["POST"])
def api_admin_migrate_legacy_history():
    err = require_admin_json()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    tenant_key = (data.get("tenant_key") or "default").strip().lower() or "default"
    history_file = (data.get("history_file") or str(Settings.DATA_DIR / "history.json")).strip()

    if not validate_tenant_key(tenant_key):
        return jsonify({"success": False, "message": "tenant_key 无效"})

    try:
        result = store.migrate_legacy_history(tenant_key, history_file, actor=current_actor())
        return jsonify({"success": True, "result": result})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("M-TEAM Downloader - User/Admin Separated")
    logger.info("用户端: http://localhost:%s/user/login", Settings.PORT)
    logger.info("管理端: http://localhost:%s/admin/login", Settings.PORT)
    logger.info("数据库文件: %s", Settings.DB_PATH)
    logger.info("=" * 50)
    app.run(host=Settings.HOST, port=Settings.PORT, debug=Settings.DEBUG, threaded=True, use_reloader=False)
