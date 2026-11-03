"""AI 제공사별 글 생성. 설정 페이지의 'AI 제공사'로 교체한다.

새 제공사를 추가하려면 generate() 를 가진 클래스를 만들고 PROVIDERS 에 등록하면 된다.
"""
from __future__ import annotations

import re

TIMEOUT_SECONDS = 300  # AI 응답 최대 대기. 넘으면 실패 처리 (봇이 멈춰 다음 실행을 막지 않게)


class LLMError(RuntimeError):
    pass


class AnthropicLLM:
    default_model = "claude-opus-5-5"

    def __init__(self, api_key: str, model: str = ""):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=1)
        self.model = model or self.default_model

    def generate(self, system: str, prompt: str) -> str:
        # 서버측 fallback: 안전 분류기가 거절하면 다른 모델로 자동 재시도
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise LLMError("Claude가 요청을 거절했습니다 (주제를 바꿔보세요)")
        return "".join(b.text for b in response.content if b.type == "text")


class GeminiLLM:
    """모델명을 비워두면 사용 가능한 Flash 모델을 최신순으로 시도한다.
    '사용량 폭주(503)·한도 초과(429)'면 잠시 기다렸다 재시도하고, 그래도 안 되면 다음 모델로 넘어간다."""

    RETRY_WAITS = (15, 45)  # 같은 모델 재시도 전 대기(초) - 일시적 폭주(503)·시간초과(504)일 때만
    BUSY_CODES = (500, 503, 504)
    QUOTA_CODE = 429  # 하루 무료 한도 초과 → 기다려도 소용없으니 바로 다음 모델

    def __init__(self, api_key: str, model: str = ""):
        from google import genai
        from google.genai import types

        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_SECONDS * 1000))
        self.models = [model] if model else self._flash_models()
        self.model = self.models[0]

    def _flash_models(self) -> list[str]:
        """사용 가능한 Flash 모델 (최신 → 이전, 일반 → lite 순)."""
        skip = ("image", "tts", "audio", "live", "embedding", "exp", "preview", "native")
        names = []
        for m in self.client.models.list():
            name = (m.name or "").removeprefix("models/")
            actions = getattr(m, "supported_actions", None) or []
            if "flash" in name and "generateContent" in actions and not any(s in name for s in skip):
                names.append(name)
        if not names:
            raise LLMError("사용 가능한 Gemini Flash 모델을 찾지 못했습니다. 설정에서 모델 이름을 직접 입력하세요.")
        version = lambda n: [int(x) for x in re.findall(r"\d+", n)]  # noqa: E731
        return sorted(names, key=lambda n: ("lite" in n, [-v for v in version(n)]))

    @staticmethod
    def _thinking(model: str):
        """생각(추론) 단계를 줄여 응답 시간 단축 → 서버 시간초과(504) 예방. 모델 세대별 설정 방식이 다름."""
        from google.genai import types

        if model.startswith("gemini-2"):
            return types.ThinkingConfig(thinking_budget=512)
        return types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)

    def generate(self, system: str, prompt: str) -> str:
        import time

        from google.genai import errors, types

        last_error: Exception | None = None
        for model in self.models[:3]:
            config = types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                thinking_config=self._thinking(model),
            )
            for wait in (*self.RETRY_WAITS, None):
                try:
                    response = self.client.models.generate_content(model=model, contents=prompt, config=config)
                    if not response.text:
                        raise LLMError("Gemini 응답이 비어 있습니다")
                    self.model = model  # 실제로 사용한 모델 (기록용)
                    return response.text
                except errors.APIError as e:
                    last_error = e
                    if e.code == self.QUOTA_CODE:
                        break  # 이 모델 한도 소진 → 다음 모델
                    if e.code not in self.BUSY_CODES:
                        raise
                    if wait is None:
                        break  # 이 모델 포기 → 다음 모델
                    time.sleep(wait)
        if getattr(last_error, "code", None) == self.QUOTA_CODE:
            raise LLMError("Gemini 무료 하루 한도를 다 썼습니다 (매일 오후 4~5시경 초기화). "
                           "설정에서 유료 키 또는 다른 AI로 바꾸면 계속 쓸 수 있습니다")
        raise LLMError(f"Gemini 서버가 혼잡합니다. 다음 실행에서 다시 시도됩니다 ({last_error})")


class OpenAILLM:
    def __init__(self, api_key: str, model: str = ""):
        from openai import OpenAI

        if not model:
            raise LLMError("OpenAI는 설정 페이지에 모델 이름을 입력해야 합니다")
        self.client = OpenAI(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=1)
        self.model = model

    def generate(self, system: str, prompt: str) -> str:
        response = self.client.responses.create(model=self.model, instructions=system, input=prompt)
        return response.output_text


PROVIDERS = {"anthropic": AnthropicLLM, "gemini": GeminiLLM, "openai": OpenAILLM}


def make_llm(provider: str, api_key: str, model: str = ""):
    if provider not in PROVIDERS:
        raise LLMError(f"알 수 없는 AI 제공사: {provider}")
    if not api_key:
        raise LLMError(f"{provider} API 키가 없습니다. 설정 페이지에 입력하세요.")
    return PROVIDERS[provider](api_key, model)
