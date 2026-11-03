"""AI 제공사별 글 생성. 설정 페이지의 'AI 제공사'로 교체한다.

새 제공사를 추가하려면 generate() 를 가진 클래스를 만들고 PROVIDERS 에 등록하면 된다.
"""
from __future__ import annotations

import re


class LLMError(RuntimeError):
    pass


class AnthropicLLM:
    default_model = "claude-opus-5-5"

    def __init__(self, api_key: str, model: str = ""):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
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
    def __init__(self, api_key: str, model: str = ""):
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = model or self._pick_flash_model()

    def _pick_flash_model(self) -> str:
        """모델명을 비워두면 사용 가능한 최신 Flash 모델을 자동 선택 (무료 요금제 대상)."""
        skip = ("lite", "image", "tts", "audio", "live", "embedding", "exp", "preview", "native")
        names = []
        for m in self.client.models.list():
            name = (m.name or "").removeprefix("models/")
            actions = getattr(m, "supported_actions", None) or []
            if "flash" in name and "generateContent" in actions and not any(s in name for s in skip):
                names.append(name)
        if not names:
            raise LLMError("사용 가능한 Gemini Flash 모델을 찾지 못했습니다. 설정에서 모델 이름을 직접 입력하세요.")
        version = lambda n: [int(x) for x in re.findall(r"\d+", n)]  # noqa: E731
        return max(names, key=version)

    def generate(self, system: str, prompt: str) -> str:
        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json"),
        )
        if not response.text:
            raise LLMError("Gemini 응답이 비어 있습니다")
        return response.text


class OpenAILLM:
    def __init__(self, api_key: str, model: str = ""):
        from openai import OpenAI

        if not model:
            raise LLMError("OpenAI는 설정 페이지에 모델 이름을 입력해야 합니다")
        self.client = OpenAI(api_key=api_key)
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
