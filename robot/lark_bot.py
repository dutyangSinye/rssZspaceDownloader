# -*- coding: utf-8 -*-
import logging
import json
import requests

logger = logging.getLogger("lark_bot")

class LarkBot:
    def __init__(self, app_id: str, app_secret: str, receive_type: str = "chat_id"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.receive_type = receive_type
        self._token = None
        self._token_expires = 0
        logger.info(f"飞书机器人初始化完成，AppID: {self.app_id}, ReceiveType: {self.receive_type}")

    def _get_token(self):
        """获取 tenant_access_token"""
        import time
        if self._token and time.time() < self._token_expires:
            return self._token
        
        try:
            url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
            data = {
                "app_id": self.app_id,
                "app_secret": self.app_secret
            }
            resp = requests.post(url, json=data, timeout=10)
            result = resp.json()
            if result.get("code") == 0:
                self._token = result["tenant_access_token"]
                self._token_expires = time.time() + result.get("expire", 7200) - 300
                logger.info("飞书 token 获取成功")
                return self._token
            else:
                logger.error(f"获取飞书token失败: {result.get('msg')}")
                return None
        except Exception as e:
            logger.error(f"获取飞书token异常: {e}")
            return None

    def send_to_chat(self, receive_id: str, text: str):
        """发送文本消息到指定用户或群"""
        token = self._get_token()
        if not token:
            logger.error("无法获取 token，消息发送失败")
            return False
        
        try:
            url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={self.receive_type}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8"
            }
            data = {
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text})
            }
            resp = requests.post(url, headers=headers, json=data, timeout=10)
            result = resp.json()
            if result.get("code") == 0:
                logger.info(f"飞书消息发送成功: {text[:50]}")
                return True
            else:
                logger.error(f"飞书发送失败: {result.get('msg')}")
                return False
        except Exception as e:
            logger.error(f"发送飞书消息异常: {e}")
            return False

    def start_listening(self):
        logger.info("飞书机器人已就绪（API 模式）")

    def run_in_background(self):
        logger.info("飞书机器人已在后台启动。")
