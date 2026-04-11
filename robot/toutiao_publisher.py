# 头条发布模块
import logging
import time

from config.settings import Settings

logger = logging.getLogger(__name__)


class ToutiaoPublisher:
    """头条号自动发布器"""

    def __init__(self, status_callback=None, log_callback=None):
        self.status_callback = status_callback
        self.log_callback = log_callback

    def _log(self, msg: str):
        logger.info(msg)
        if self.log_callback:
            self.log_callback(msg)

    def _set_status(self, status: str):
        if self.status_callback:
            self.status_callback(status)

    def publish(self, title: str, content: str, auto_publish: bool = True) -> dict:
        if not title or not content:
            return {"success": False, "message": "标题和内容不能为空"}

        try:
            from playwright.sync_api import sync_playwright

            self._set_status("publishing")
            self._log("启动浏览器...")
            user_data_dir = str(Settings.DATA_DIR / "browser_data")

            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=Settings.TOUTIAO_HEADLESS,
                    viewport={"width": 1280, "height": 720},
                )
                page = browser.pages[0] if browser.pages else browser.new_page()

                self._log("检查登录状态...")
                page.goto("https://mp.toutiao.com", timeout=30000)
                page.wait_for_load_state("domcontentloaded")
                time.sleep(3)

                is_logged_in = "login" not in page.url.lower()
                if not is_logged_in:
                    self._log("未登录，请在 60 秒内扫码登录...")
                    for _ in range(60):
                        try:
                            if "login" not in page.url.lower():
                                is_logged_in = True
                                self._log("登录成功")
                                break
                        except Exception:
                            pass
                        time.sleep(1)

                if not is_logged_in:
                    return {"success": False, "message": "登录超时，请重试"}

                self._log("打开发布页...")
                page.goto("https://mp.toutiao.com/profile_v4/graphic/publish", timeout=30000)
                page.wait_for_load_state("domcontentloaded")
                time.sleep(5)
                self._close_popups(page)

                self._log(f"填写标题: {title[:30]}...")
                if not self._fill_title(page, title):
                    return {"success": False, "message": "标题填写失败"}

                self._log("填写正文...")
                if not self._fill_content(page, content):
                    return {"success": False, "message": "正文填写失败"}

                if not auto_publish:
                    self._log("发布页已打开，请手动确认发布")
                    return {"success": True, "message": "发布页已打开，请手动确认"}

                self._log("点击发布按钮...")
                result = self._click_publish(page)
                if Settings.TOUTIAO_KEEP_OPEN_AFTER_PUBLISH:
                    if result.get("success"):
                        self._log("发布后保留页面以便人工复核")
                        result["message"] = f"{result.get('message', '发布流程完成')}（页面已保留）"
                    else:
                        self._log("发布结果未确认，页面已保留便于人工核对")
                        result["message"] = f"{result.get('message', '发布结果未确认')}（页面已保留，请手动核查）"
                else:
                    browser.close()
                return result

        except Exception as e:
            logger.error("发布失败: %s", e)
            self._log(f"发布失败: {e}")
            return {"success": False, "message": f"发布失败: {e}"}

    def _close_popups(self, page):
        try:
            close_selectors = [".draft-tip-close-icon", ".byte-drawer-close", ".byte-drawer-mask"]
            for selector in close_selectors:
                try:
                    elements = page.locator(selector)
                    for i in range(min(elements.count(), 3)):
                        try:
                            if elements.nth(i).is_visible(timeout=1000):
                                elements.nth(i).click(timeout=2000)
                                time.sleep(0.5)
                        except Exception:
                            pass
                except Exception:
                    pass
            for _ in range(3):
                page.keyboard.press("Escape")
                time.sleep(0.3)
        except Exception:
            pass

    def _fill_title(self, page, title: str) -> bool:
        try:
            ta = page.locator('textarea[placeholder*="标题"]').first
            if ta.is_visible(timeout=5000):
                ta.click()
                ta.fill(title)
                time.sleep(1)
                return len(ta.input_value()) >= min(len(title), 4)
        except Exception:
            pass
        return False

    def _fill_content(self, page, content: str) -> bool:
        try:
            editor = page.locator("div.ProseMirror").first
            if editor.is_visible(timeout=5000):
                editor.click()
                time.sleep(0.5)
                for para in [p.strip() for p in content.split("\n\n") if p.strip()][:30]:
                    page.keyboard.type(para[:500], delay=5)
                    page.keyboard.press("Enter")
                    page.keyboard.press("Enter")
                    time.sleep(0.2)
                return True
        except Exception:
            pass
        return False

    def _click_publish(self, page) -> dict:
        publish_selectors = [
            'button:has-text("发布")',
            "button.btn-publish",
            'button[type="submit"]',
            'button:has-text("发表")',
            ".publish-btn",
        ]

        clicked = False
        for selector in publish_selectors:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            return {"success": False, "message": "未找到发布按钮"}

        time.sleep(2)

        try:
            confirm_btn = page.locator(
                'button:has-text("确认发布"), button:has-text("确定"), button:has-text("确认")'
            ).first
            if confirm_btn.is_visible(timeout=3000):
                confirm_btn.click()
                time.sleep(2)
        except Exception:
            pass

        success_texts = ["发布成功", "已发布", "发布完成"]
        for success_text in success_texts:
            try:
                if page.locator(f'text="{success_text}"').first.is_visible(timeout=5000):
                    self._log("发布成功")
                    return {"success": True, "message": "发布成功"}
            except Exception:
                pass

        current_url = page.url.lower()
        if any(token in current_url for token in ["manage", "article", "content"]):
            self._log("页面已跳转到内容管理区域，判定为发布成功")
            return {"success": True, "message": "发布成功"}

        self._log("未能确认发布结果，按失败处理")
        return {"success": False, "message": "未能确认发布是否成功，请手动检查"}
