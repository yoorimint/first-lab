"""AI 제공사별 글 생성. 설정 페이지의 'AI 제공사'로 교체한다.

새 제공사를 추가하려면 generate() 를 가진 클래스를 만들고 PROVIDERS 에 등록하면 된다.
"""
from __future__ import annotations

import re

TIMEOUT_SECONDS = 300  # AI 응답 최대 대기. 넘으면 실패 처리 (봇이 멈춰 다음 실행을 막지 않게)


class LLMError(RuntimeError):
    pass


class LLMBusy(LLMError):
    """일시적 혼잡·한도 초과 → 나중에 다시 시도하면 되는 오류."""


class AnthropicLLM:
    default_model = "claude-opus-5-5"

    MAX_SEARCHES = 3       # 글 1개당 웹 검색 최대 횟수 (검색 1회 $0.01 + 검색 결과 읽는 입력 토큰)
    # 추론(생각) 깊이: 높을수록 품질↑·비용↑ (생각한 토큰도 출력 요금). 글쓰기는 medium, 판정 작업은 low
    EFFORT_WRITE, EFFORT_LIGHT = "medium", "low"
    LIGHT_MODEL = "claude-haiku-5-5"
    FALLBACK_SEARCH_MODEL = "claude-sonnet-5-5"
    PRICE = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.10, 0.50)}
    MAX_CONTINUATIONS = 4  # 검색이 길어져 중간에 멈춘(pause_turn) 경우 이어서 진행할 최대 횟수
    WEB_SEARCH = {
        "type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES,
        "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"},
    }

    def __init__(self, api_key: str, model: str = "", light: bool = False):
        import anthropic

        # 웹 검색을 하면 응답이 길어지므로 대기 시간을 넉넉히
        self.client = anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_SECONDS * 2, max_retries=1)
        # 짧은 판정 작업(안전검사·분류)은 저렴한 Haiku 로 (Sonnet 대비 약 1/20 가격)
        self.model = self.LIGHT_MODEL if light else (model or self.default_model)
        self.light = light
        self.cost_usd = 0.0  # 이 객체로 쓴 누적 비용 추정 (기록용)
        self.search_urls: list[str] = []  # 마지막 generate 의 웹 검색 결과 주소 (순위순, 경쟁도 기록용)

    def generate(self, system: str, prompt: str, web_search: bool = False) -> str:
        """web_search=True: Claude 가 직접 웹 검색해서 최신 정보를 확인한 뒤 작성."""
        import anthropic

        self.search_urls = []
        try:
            return self._generate(system, prompt, web_search)
        except anthropic.BadRequestError:
            if not (web_search and self.model == self.LIGHT_MODEL):
                raise
            # Haiku 에서 웹 검색 도구가 거절되면 Sonnet 으로 한 번 더 (낮은 추론 깊이)
            self.model = self.FALLBACK_SEARCH_MODEL
            return self._generate(system, prompt, web_search)

    def _generate(self, system: str, prompt: str, web_search: bool) -> str:
        messages = [{"role": "user", "content": prompt}]
        tool = dict(self.WEB_SEARCH)
        if self.model.startswith("claude-haiku"):  # Haiku 는 기본 검색 도구 사용
            tool["type"] = "web_search_20250305"
        tools = [tool] if web_search else []
        for _ in range(self.MAX_CONTINUATIONS + 1):
            # 서버측 fallback: 안전 분류기가 거절하면 다른 모델로 자동 재시도
            extra = {"tools": tools} if tools else {}
            if not self.model.startswith("claude-haiku"):  # Haiku 는 서버측 fallback 미지원
                extra.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=messages,
                output_config={"effort": self.EFFORT_LIGHT if self.light else self.EFFORT_WRITE},
                **extra,
            )
            self._add_cost(response.usage)
            self._collect_search_urls(response.content)
            if response.stop_reason != "pause_turn":
                break
            # 서버 검색 루프가 한도에 걸려 멈춤 → 응답을 그대로 붙여 보내면 이어서 진행
            messages = [{"role": "user", "content": prompt}, {"role": "assistant", "content": response.content}]
        if response.stop_reason == "refusal":
            raise LLMError("Claude가 요청을 거절했습니다 (주제를 바꿔보세요)")
        if response.stop_reason == "max_tokens":
            raise LLMError("글이 너무 길어 중간에 잘렸습니다 (설정의 글 분량을 줄여보세요)")
        return "".join(b.text for b in response.content if b.type == "text")

    def _collect_search_urls(self, blocks) -> None:
        """웹 검색 결과 블록의 주소를 순서대로 모은다 (오류 결과는 리스트가 아니라 건너뜀)."""
        for b in blocks:
            results = getattr(b, "content", None) if getattr(b, "type", "") == "web_search_tool_result" else None
            if isinstance(results, list):
                self.search_urls += [u for u in (getattr(r, "url", "") for r in results) if u]

    def _add_cost(self, usage) -> None:
        price_in, price_out = self.PRICE.get(self.model, (2.0, 10.0))
        searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
        cached = (getattr(usage, "cache_read_input_tokens", 0) or 0) * 0.1
        self.cost_usd += ((usage.input_tokens + cached) * price_in + usage.output_tokens * price_out) / 1e6
        self.cost_usd += searches * 0.01


class GeminiLLM:
    """모델명을 비워두면 사용 가능한 Flash 모델을 최신순으로 시도한다.
    '사용량 폭주(503)·한도 초과(429)'면 잠시 기다렸다 재시도하고, 그래도 안 되면 다음 모델로 넘어간다."""

    RETRY_WAITS = (30,)     # 같은 모델 재시도 1회 (실패한 요청도 무료 한도를 소모하므로 최소화)
    MAX_MODELS = 2          # 시도할 모델 수
    BUSY_CODES = (500, 503, 504)
    QUOTA_CODE = 429  # 하루 무료 한도 초과 → 기다려도 소용없으니 바로 다음 모델

    def __init__(self, api_key: str, model: str = "", light: bool = False):
        from google import genai
        from google.genai import types

        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_SECONDS * 1000))
        self.models = [model] if model else self._flash_models()
        if light and not model:  # 짧은 판정 작업: lite 모델 먼저 (혼잡 덜하고 한도가 별도)
            self.models.sort(key=lambda n: "lite" not in n)
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
    def _quota_message(detail: str) -> str:
        """구글 오류 내용에서 어떤 한도인지 구분 (분당/하루). 한도는 API 키가 아니라 '프로젝트' 단위."""
        if "PerMinute" in detail:
            kind = "분당 요청 한도 초과 (잠시 후 풀림)"
        elif "PerDay" in detail:
            kind = "하루 무료 한도 소진 (매일 오후 4~5시경 초기화)"
        else:
            kind = "사용 한도 초과"
        quota = re.search(r"quotaId['\"]?:\s*['\"]([^'\"]+)", detail)
        return f"Gemini {kind}{' [' + quota.group(1) + ']' if quota else ''}. 한도는 키가 아니라 구글 프로젝트 단위입니다"

    @staticmethod
    def _thinking(model: str):
        """생각(추론) 단계를 줄여 응답 시간 단축 → 서버 시간초과(504) 예방. 모델 세대별 설정 방식이 다름."""
        from google.genai import types

        if model.startswith("gemini-2"):
            return types.ThinkingConfig(thinking_budget=512)
        return types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)

    def generate(self, system: str, prompt: str, web_search: bool = False) -> str:
        import time

        from google.genai import errors, types

        last_error: Exception | None = None
        for model in self.models[:self.MAX_MODELS]:
            config = types.GenerateContentConfig(
                system_instruction=system,
                # 구글 검색 연동과 JSON 모드는 함께 못 쓰므로, 검색할 때는 JSON 지정을 뺀다 (지시문으로 JSON 요구)
                response_mime_type=None if web_search else "application/json",
                tools=[types.Tool(google_search=types.GoogleSearch())] if web_search else None,
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
            raise LLMBusy(self._quota_message(str(last_error)))
        raise LLMBusy(f"Gemini 서버가 혼잡합니다 ({getattr(last_error, 'code', '')})")


class OpenAILLM:
    def __init__(self, api_key: str, model: str = ""):
        from openai import OpenAI

        if not model:
            raise LLMError("OpenAI는 설정 페이지에 모델 이름을 입력해야 합니다")
        self.client = OpenAI(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=1)
        self.model = model

    def generate(self, system: str, prompt: str, web_search: bool = False) -> str:
        # OpenAI 는 웹 검색 미연결 (필요 시 추가)
        response = self.client.responses.create(model=self.model, instructions=system, input=prompt)
        return response.output_text


PROVIDERS = {"anthropic": AnthropicLLM, "gemini": GeminiLLM, "openai": OpenAILLM}


def make_llm(provider: str, api_key: str, model: str = "", light: bool = False):
    """light=True: 짧은 판정 작업용 (Gemini 는 lite 모델 우선)."""
    if provider not in PROVIDERS:
        raise LLMError(f"알 수 없는 AI 제공사: {provider}")
    if not api_key:
        raise LLMError(f"{provider} API 키가 없습니다. 설정 페이지에 입력하세요.")
    if provider in ("gemini", "anthropic"):
        return PROVIDERS[provider](api_key, model, light=light)
    return PROVIDERS[provider](api_key, model)
