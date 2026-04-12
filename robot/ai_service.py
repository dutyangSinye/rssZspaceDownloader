# AI service
import logging

import requests
from openai import OpenAI

from config.settings import Settings

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    """Raised when no usable AI backend is available or a request fails."""


class AIService:
    """Dual backend AI service: OpenAI first, Ollama fallback."""

    def __init__(self):
        self.max_output_tokens = Settings.AI_MAX_TOKENS
        self.temperature = Settings.AI_TEMPERATURE
        self.reasoning_effort = Settings.OPENAI_REASONING_EFFORT

        self.backend = None
        self.client = None
        self.base_url = ""
        self.model = ""

        if Settings.OPENAI_API_KEY:
            client_kwargs = {"api_key": Settings.OPENAI_API_KEY}
            if Settings.OPENAI_BASE_URL:
                client_kwargs["base_url"] = Settings.OPENAI_BASE_URL.rstrip("/")
            self.client = OpenAI(**client_kwargs)
            self.model = Settings.OPENAI_MODEL or "gpt-5.4"
            self.backend = "openai"
            return

        if Settings.OLLAMA_URL:
            self.base_url = Settings.OLLAMA_URL.rstrip("/")
            self.model = Settings.OLLAMA_MODEL or "qwen2.5:7b"
            self.backend = "ollama"
            return

        raise AIServiceError("未配置可用 AI 后端，请配置 OPENAI_API_KEY 或 OLLAMA_URL")

    def backend_label(self) -> str:
        if self.backend == "openai":
            return f"OpenAI / {self.model}"
        if self.backend == "ollama":
            return f"Ollama / {self.model}"
        return "Unavailable"

    def chat(self, prompt: str, system_prompt: str = "你是一个专业的助手", max_tokens: int = None) -> str:
        if self.backend == "openai":
            return self._chat_openai(prompt, system_prompt, max_tokens)
        if self.backend == "ollama":
            return self._chat_ollama(prompt, system_prompt, max_tokens)
        raise AIServiceError("AI 后端不可用")

    def _chat_openai(self, prompt: str, system_prompt: str, max_tokens: int = None) -> str:
        try:
            logger.info("调用 OpenAI: %s", self.model)
            response = self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_output_tokens=max_tokens or self.max_output_tokens,
                temperature=self.temperature,
                reasoning={"effort": self.reasoning_effort},
            )
            content = (response.output_text or "").strip()
            if not content:
                raise AIServiceError("OpenAI 返回内容为空")
            return content
        except AIServiceError:
            raise
        except Exception as e:
            logger.error("OpenAI 调用失败: %s", e)
            raise AIServiceError(f"OpenAI 调用失败: {e}") from e

    def _chat_ollama(self, prompt: str, system_prompt: str, max_tokens: int = None) -> str:
        try:
            logger.info("调用 Ollama: %s", self.model)
            resp = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=(
                    {"Authorization": f"Bearer {Settings.OLLAMA_API_KEY}"}
                    if Settings.OLLAMA_API_KEY
                    else None
                ),
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "max_tokens": max_tokens or self.max_output_tokens,
                    "temperature": self.temperature,
                },
                timeout=600,
            )
            if resp.status_code >= 400:
                body = (resp.text or "").strip()
                if len(body) > 300:
                    body = body[:300] + "..."
                raise AIServiceError(
                    f"Ollama 服务返回 HTTP {resp.status_code}" + (f": {body}" if body else "")
                )
            result = resp.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if not content:
                raise AIServiceError("Ollama 返回内容为空")
            return content
        except AIServiceError:
            raise
        except Exception as e:
            logger.error("Ollama 调用失败: %s", e)
            raise AIServiceError(f"Ollama 调用失败: {e}") from e

    def analyze_news(self, news_list: list, topic_label: str = "行业资讯") -> str:
        if not news_list:
            return "未采集到任何资讯"

        news_text = "\n\n".join(
            [f"- {n.get('title', '')} ({n.get('source', '')})\n  {n.get('summary', '')}" for n in news_list[:30]]
        )

        prompt = f"""你是一个专业的{topic_label}分析师。请分析以下资讯，总结趋势和重要事件：

{news_text}

请分析：
1. 今日行业重要动态（3-5条）
2. 新产品、新技术发布
3. 市场趋势分析
4. 重大事件点评
5. 行业发展建议

要求：专业、客观、有深度，尽量基于资讯内容，不编造数据。"""

        return self.chat(prompt, f"你是一名资深{topic_label}分析师，擅长归纳与分析资讯。")

    def write_article(
        self,
        news_list: list,
        analysis: str,
        topic_label: str = "行业资讯",
        topic_tags: list | None = None,
    ) -> str:
        if not news_list:
            return "无可用资讯"

        top_news = news_list[:8]
        news_details = "\n\n".join(
            [f"### {n.get('title', '')}\n{n.get('summary', '')}\n来源: {n.get('source', '')}" for n in top_news]
        )

        tags = topic_tags or ["资讯速览", "行业观察", "趋势分析", "热点追踪"]
        tag_text = " ".join(f"#{tag}" for tag in tags)

        prompt = f"""你是一名资深科技自媒体编辑，擅长撰写今日头条风格的中文文章。
请根据以下资讯和分析，写一篇可发布的文章：

【行业动态】
{news_details}

【分析】
{analysis}

要求：
1. 提供 1 个正式标题和 3 个备选标题。
2. 正文结构清晰，适合普通读者阅读。
3. 字数控制在 2000 到 3000 字。
4. 结尾加入互动引导。
5. 必须为中文。
6. 在需要配图的位置使用 [图片描述:xxx] 标记，至少 5 处。
7. 不编造无法从资讯中推出的事实。
8. 内容主题要围绕“{topic_label}”展开。

输出格式：
---
标题：[正式标题]
备选标题：
1. [备选1]
2. [备选2]
3. [备选3]

正文：[文章正文，适当位置插入[图片描述:xxx]标记]

话题标签：{tag_text}
---"""

        return self.chat(prompt, "你是一名资深科技自媒体编辑，擅长写适合中文平台发布的深度文章。")

    def evaluate_article(self, article: str) -> str:
        if not article:
            return "文章为空"

        prompt = f"""请对以下文章进行评估：
{article[:2000]}

请从以下维度评分（1-10分）：
1. 标题吸引力
2. 内容深度
3. 逻辑结构
4. 语言表达
5. 互动性

输出格式：
总分：X/10
各项评分：
- 标题吸引力：X/10
- 内容深度：X/10
- 逻辑结构：X/10
- 语言表达：X/10
- 互动性：X/10
改进建议：[具体建议]"""

        return self.chat(prompt, "你是一名专业的文章质量评估专家。", max_tokens=1000)

    def optimize_article(self, article: str, feedback: str) -> str:
        prompt = f"""请根据以下评估反馈，优化这篇文章：
【原文】
{article}

【评估反馈】
{feedback}

请保留原有风格，重点修正明显问题，并保留图片占位标记。"""

        return self.chat(prompt, "你是一名经验丰富的中文编辑，擅长优化文章结构和表达。")
