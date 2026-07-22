from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, runtime_checkable
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit

from http_requests import build_json_post_request
from inference_backend import InferenceBackend


DOUBAO_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DOUBAO_DEFAULT_MODEL = "doubao-seed-translation-250915"
DOUBAO_GLOSSARY_FALLBACK_MODEL = "doubao-seed-2-0-pro-260215"
GEMINI_DEFAULT_MODEL = "gemini-3.1-pro-preview"

# Temporary compatibility object for pre-A4.2 tests which patch the old module
# transport symbol. All HTTP behavior and classification live in this module.
HTTP_CLIENT_COMPAT = urllib_request


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider_name: str
    translator_name: str
    target_lang: str
    model: str = field(default="", repr=False)
    base_url: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False, compare=False)
    use_gpu: bool = False


@dataclass(frozen=True, slots=True)
class TranslationRequest:
    config: ProviderConfig = field(repr=False)
    texts: tuple[str, ...] = field(repr=False)
    device: str = "cpu"


@dataclass(frozen=True, slots=True)
class TranslationResult:
    texts: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class GlossaryRequest:
    config: ProviderConfig = field(repr=False)
    system_prompt: str = field(repr=False)
    user_prompt: str = field(repr=False)
    max_tokens: int = 3200
    timeout_seconds: int = 120


@dataclass(frozen=True, slots=True)
class GlossaryResult:
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProviderValidationResult:
    preview: str = field(default="", repr=False)


def normalize_provider_config(raw_config: Mapping[str, Any]) -> ProviderConfig:
    requested_translator = str(
        raw_config.get("translator")
        or raw_config.get("selected_translator")
        or "gemini"
    ).strip() or "gemini"
    provider_name = requested_translator
    if requested_translator == "custom_openai":
        selected = str(raw_config.get("selected_translator") or "").strip()
        provider_name = (
            selected
            if selected in {"doubao-ark", "openai-compatible"}
            else "doubao-ark"
        )
    elif (
        requested_translator == "gemini"
        and str(raw_config.get("selected_translator") or "").strip() == "sugoi"
    ):
        # Preserve the UI selection when an already-normalized Chinese Sugoi
        # config is configured again; only its upstream dispatcher is Gemini.
        provider_name = "sugoi"
    target_lang = str(
        raw_config.get("target_lang") or "CHS"
    ).strip().upper() or "CHS"
    translator_name = provider_name
    if provider_name in {"doubao-ark", "openai-compatible"}:
        translator_name = "custom_openai"
    elif provider_name == "sugoi" and target_lang in {"CHS", "CHT"}:
        translator_name = "gemini"
    raw_model = (
        raw_config.get("openai_model")
        if provider_name == "openai-compatible"
        else raw_config.get("translator_model_custom")
        or raw_config.get("translator_model")
    )
    model = str(raw_model or "").strip()
    if provider_name == "doubao-ark" and not model:
        model = DOUBAO_DEFAULT_MODEL
    if provider_name == "gemini" and not model:
        model = GEMINI_DEFAULT_MODEL
    base_url = str(raw_config.get("openai_base_url") or "").strip()
    if provider_name == "doubao-ark":
        base_url = DOUBAO_ARK_BASE_URL
    return ProviderConfig(
        provider_name=provider_name,
        translator_name=translator_name,
        target_lang=target_lang,
        model=model,
        base_url=base_url,
        api_key=str(raw_config.get("api_key") or "").strip(),
        use_gpu=bool(raw_config.get("use_gpu", True)),
    )


class TranslationProviderError(RuntimeError):
    category = "provider"


class TranslationProviderConfigurationError(TranslationProviderError):
    category = "configuration"


class TranslationProviderAuthenticationError(TranslationProviderError):
    category = "authentication"


class TranslationProviderRateLimitError(TranslationProviderError):
    category = "rate_limit"


class TranslationProviderContextLengthError(TranslationProviderError):
    category = "context_length"


class TranslationProviderRetryableError(TranslationProviderError):
    category = "retryable"


class TranslationProviderMalformedResponseError(TranslationProviderError):
    category = "malformed_response"


class TranslationProviderUpstreamError(TranslationProviderError):
    category = "upstream_failure"


def canonical_provider_error(exc: TranslationProviderError) -> TranslationProviderError:
    if isinstance(exc, TranslationProviderConfigurationError):
        return TranslationProviderConfigurationError(
            "翻译服务配置无效，请检查 API Base URL、API Key 和模型名称。"
        )
    if isinstance(exc, TranslationProviderAuthenticationError):
        return TranslationProviderAuthenticationError(
            "翻译服务鉴权失败，请检查 API Key。"
        )
    if isinstance(exc, TranslationProviderRateLimitError):
        return TranslationProviderRateLimitError(
            "翻译服务请求过于频繁，请稍后重试。"
        )
    if isinstance(exc, TranslationProviderContextLengthError):
        return TranslationProviderContextLengthError(
            "翻译请求内容超过模型上下文长度。"
        )
    if isinstance(exc, TranslationProviderRetryableError):
        return TranslationProviderRetryableError(
            "翻译服务暂时无法完成请求，请稍后重试。"
        )
    if isinstance(exc, TranslationProviderMalformedResponseError):
        return TranslationProviderMalformedResponseError(
            "翻译服务返回了无法识别的响应，请重试。"
        )
    if isinstance(exc, TranslationProviderUpstreamError):
        return TranslationProviderUpstreamError(
            "翻译服务拒绝了请求，请检查配置后重试。"
        )
    return TranslationProviderError("翻译服务请求失败，请重试。")


@runtime_checkable
class TranslationProvider(Protocol):
    def configure(self, raw_config: Mapping[str, Any]) -> ProviderConfig: ...

    def runtime_environment(self, config: ProviderConfig) -> Mapping[str, str]: ...

    def is_context_length_error(self, exc: BaseException) -> bool: ...

    async def validate(self, config: ProviderConfig) -> ProviderValidationResult: ...

    async def translate(self, request: TranslationRequest) -> TranslationResult: ...

    async def extract_glossary(self, request: GlossaryRequest) -> GlossaryResult: ...


class UpstreamTranslationProvider:
    """Owns provider configuration, transport normalization and safe errors."""

    def __init__(
        self,
        inference_backend: InferenceBackend,
        *,
        compatibility_hooks: Any | None = None,
        url_opener: Callable[..., Any] | None = None,
        gemini_client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._inference_backend = inference_backend
        self._compatibility_hooks = compatibility_hooks
        self._url_opener = url_opener
        self._gemini_client_factory = gemini_client_factory

    def configure(self, raw_config: Mapping[str, Any]) -> ProviderConfig:
        return normalize_provider_config(raw_config)

    def runtime_environment(self, config: ProviderConfig) -> Mapping[str, str]:
        env: dict[str, str] = {"GEMINI_MODEL": GEMINI_DEFAULT_MODEL}
        if config.provider_name == "gemini" and config.api_key:
            env["GEMINI_API_KEY"] = config.api_key
        elif config.provider_name in {"doubao-ark", "openai-compatible"}:
            if config.base_url:
                env["CUSTOM_OPENAI_API_BASE"] = config.base_url
            if config.model:
                env["CUSTOM_OPENAI_MODEL"] = config.model
            env["CUSTOM_OPENAI_MODEL_CONF"] = ""
            env["CUSTOM_OPENAI_USE_RESPONSES"] = (
                "1"
                if config.provider_name == "doubao-ark"
                and config.model.startswith("doubao-seed-translation")
                else "0"
            )
            if config.api_key:
                env["CUSTOM_OPENAI_API_KEY"] = config.api_key
        return env

    def is_context_length_error(self, exc: BaseException) -> bool:
        return isinstance(exc, TranslationProviderContextLengthError) or (
            self._body_indicates_context_limit(str(exc or ""))
        )

    async def validate(self, config: ProviderConfig) -> ProviderValidationResult:
        self._require_supported_remote(config)
        self._require_api_key(config)
        failure: TranslationProviderError | None = None
        try:
            if config.provider_name == "openai-compatible":
                self._require_chat_configuration(config)
                preview = await asyncio.to_thread(
                    self._call_compat,
                    "_request_chat_completions_validation_sync",
                    self.request_chat_completions_validation_sync,
                    provider_label="OpenAI Compatible",
                    base_url=config.base_url,
                    model=config.model,
                    api_key=config.api_key,
                )
            elif config.provider_name == "doubao-ark":
                if config.model.startswith("doubao-seed-translation"):
                    preview = await asyncio.to_thread(
                        self._call_compat,
                        "_request_responses_validation_sync",
                        self.request_responses_validation_sync,
                        provider_label="Doubao Ark",
                        base_url=config.base_url,
                        model=config.model,
                        api_key=config.api_key,
                        target_lang=config.target_lang,
                    )
                else:
                    preview = await asyncio.to_thread(
                        self._call_compat,
                        "_request_chat_completions_validation_sync",
                        self.request_chat_completions_validation_sync,
                        provider_label="Doubao Ark",
                        base_url=config.base_url,
                        model=config.model,
                        api_key=config.api_key,
                    )
            else:
                result = await self.translate(
                    TranslationRequest(config=config, texts=("テスト",), device="cpu")
                )
                preview = result.texts[0]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = self._detached_error(config.provider_name, exc)
        if failure is not None:
            raise failure
        if not isinstance(preview, str):
            raise TranslationProviderMalformedResponseError(
                "翻译服务返回了无法识别的校验结果。"
            )
        return ProviderValidationResult(preview=preview.strip())

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        texts = tuple(str(item or "").strip() for item in request.texts)
        if request.config.translator_name == "none":
            return TranslationResult(texts=tuple("" for _ in texts))
        if request.config.provider_name in {"gemini", "doubao-ark", "openai-compatible"}:
            self._require_api_key(request.config)
        failure: TranslationProviderError | None = None
        try:
            translated = await self._inference_backend.translate_texts(
                list(texts),
                translator_name=request.config.translator_name,
                target_lang=request.config.target_lang,
                device=request.device,
                environment=self.runtime_environment(request.config),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = self._detached_error(request.config.provider_name, exc)
        if failure is not None:
            raise failure
        if not isinstance(translated, (tuple, list)) or len(translated) != len(texts):
            raise TranslationProviderMalformedResponseError(
                "翻译服务返回的结果数量异常，请重试。"
            )
        normalized: list[str] = []
        for item in translated:
            if not isinstance(item, str):
                raise TranslationProviderMalformedResponseError(
                    "翻译服务返回了无法识别的内容，请重试。"
                )
            normalized.append(item.strip())
        return TranslationResult(texts=tuple(normalized))

    async def extract_glossary(self, request: GlossaryRequest) -> GlossaryResult:
        config = request.config
        if config.provider_name not in {"gemini", "doubao-ark", "openai-compatible"}:
            return GlossaryResult(text="")
        self._require_api_key(config)
        failure: TranslationProviderError | None = None
        try:
            if config.provider_name in {"doubao-ark", "openai-compatible"}:
                self._require_chat_configuration(config)
                model = config.model
                if config.provider_name == "doubao-ark" and model.startswith(
                    "doubao-seed-translation"
                ):
                    model = DOUBAO_GLOSSARY_FALLBACK_MODEL
                text = await asyncio.to_thread(
                    self._call_compat,
                    "_request_chat_completions_text_sync",
                    self.request_chat_completions_text_sync,
                    provider_label=(
                        "Doubao Ark"
                        if config.provider_name == "doubao-ark"
                        else "OpenAI Compatible"
                    ),
                    base_url=config.base_url,
                    model=model,
                    api_key=config.api_key,
                    system_prompt=request.system_prompt,
                    user_prompt=request.user_prompt,
                    max_tokens=request.max_tokens,
                    timeout_seconds=request.timeout_seconds,
                )
            else:
                text = await asyncio.to_thread(
                    self._call_compat,
                    "_request_gemini_text_sync",
                    self.request_gemini_text_sync,
                    model=config.model or GEMINI_DEFAULT_MODEL,
                    api_key=config.api_key,
                    system_prompt=request.system_prompt,
                    user_prompt=request.user_prompt,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = self._detached_error(config.provider_name, exc)
        if failure is not None:
            raise failure
        if not isinstance(text, str) or not text.strip():
            raise TranslationProviderMalformedResponseError(
                "翻译服务没有返回可读取的文本，请重试。"
            )
        return GlossaryResult(text=text.strip())

    def request_chat_completions_validation_sync(
        self, *, provider_label: str, base_url: str, model: str, api_key: str
    ) -> str:
        provider_label = self._safe_provider_label(provider_label)
        return self.request_chat_completions_text_sync(
            provider_label=provider_label,
            base_url=base_url,
            model=model,
            api_key=api_key,
            system_prompt="You are a translation connectivity test. Return only the translated text.",
            user_prompt="Translate this Japanese text to Chinese: テスト",
            max_tokens=64,
            timeout_seconds=30,
        )

    def request_chat_completions_text_sync(
        self,
        *,
        provider_label: str,
        base_url: str,
        model: str,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1600,
        timeout_seconds: int = 30,
    ) -> str:
        provider_label = self._safe_provider_label(provider_label)
        config = ProviderConfig(
            provider_name="openai-compatible",
            translator_name="custom_openai",
            target_lang="CHS",
            model=model,
            base_url=base_url,
            api_key=api_key,
        )
        self._require_api_key(config, provider_label=provider_label)
        self._require_chat_configuration(config, provider_label=provider_label)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }
        response = self._post_json(
            provider_label=provider_label,
            url=self._chat_completions_url(base_url),
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        return self._extract_chat_completions_text(response)

    def request_responses_validation_sync(
        self,
        *,
        provider_label: str,
        base_url: str,
        model: str,
        api_key: str,
        target_lang: Any,
    ) -> str:
        provider_label = self._safe_provider_label(provider_label)
        payload = {
            "model": model,
            "input": [{
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": "テスト",
                    "translation_options": {
                        "target_language": self._language_code(target_lang) or "zh"
                    },
                }],
            }],
        }
        response = self._post_json(
            provider_label=provider_label,
            url=self._responses_url(base_url),
            api_key=api_key,
            payload=payload,
            timeout_seconds=30,
        )
        return self._extract_responses_text(response)

    def request_gemini_text_sync(
        self,
        *,
        model: str,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if not api_key:
            raise TranslationProviderAuthenticationError("缺少 Gemini API Key。")
        failure: TranslationProviderError | None = None
        try:
            if self._gemini_client_factory is not None:
                client = self._gemini_client_factory(api_key)
                response = client.generate(
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            else:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=model or GEMINI_DEFAULT_MODEL,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0,
                    ),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = self._detached_error("gemini", exc)
        if failure is not None:
            raise failure
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
        raise TranslationProviderMalformedResponseError(
            "Gemini 没有返回可读取的文本，请重试。"
        )

    def post_json_direct(
        self,
        *,
        provider_label: str,
        url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        provider_label = self._safe_provider_label(provider_label)
        preparation_failure: TranslationProviderError | None = None
        try:
            timeout = max(5, int(timeout_seconds or 30))
            request = build_json_post_request(url, api_key=api_key, payload=payload)
        except Exception:
            preparation_failure = TranslationProviderConfigurationError(
                f"{provider_label} 请求配置无效。"
            )
        if preparation_failure is not None:
            raise preparation_failure

        opener = self._url_opener or HTTP_CLIENT_COMPAT.urlopen
        body = ""
        response_failure: TranslationProviderError | None = None
        try:
            with opener(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            response_failure = self._http_error(provider_label, exc.code, body)
        except (urllib_error.URLError, TimeoutError, OSError):
            response_failure = TranslationProviderRetryableError(
                f"{provider_label} 暂时无法连接，请稍后重试。"
            )
        except UnicodeError:
            response_failure = TranslationProviderMalformedResponseError(
                f"{provider_label} 返回了无法解码的响应。"
            )
        except Exception:
            response_failure = TranslationProviderMalformedResponseError(
                f"{provider_label} 返回格式异常。"
            )
        if response_failure is not None:
            raise response_failure

        parse_failure: TranslationProviderError | None = None
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, UnicodeError, TypeError):
            parse_failure = TranslationProviderMalformedResponseError(
                f"{provider_label} 返回了无法解析的响应。"
            )
        if parse_failure is not None:
            raise parse_failure
        if not isinstance(parsed, dict):
            raise TranslationProviderMalformedResponseError(
                f"{provider_label} 返回格式异常。"
            )
        return parsed

    def _post_json(self, **kwargs: Any) -> dict[str, Any]:
        return self._call_compat(
            "_post_validation_json",
            self.post_json_direct,
            **kwargs,
        )

    def _call_compat(self, method_name: str, fallback: Callable[..., Any], **kwargs: Any) -> Any:
        method = getattr(self._compatibility_hooks, method_name, None)
        if callable(method):
            return method(**kwargs)
        return fallback(**kwargs)

    @staticmethod
    def _require_supported_remote(config: ProviderConfig) -> None:
        if config.provider_name not in {"gemini", "doubao-ark", "openai-compatible"}:
            raise TranslationProviderConfigurationError(
                "当前只支持校验 Gemini / Doubao / OpenAI Compatible，暂不支持当前引擎。"
            )

    @staticmethod
    def _require_api_key(
        config: ProviderConfig, *, provider_label: str | None = None
    ) -> None:
        if not config.api_key:
            label = f"{provider_label} " if provider_label else ""
            raise TranslationProviderAuthenticationError(f"缺少 {label}API Key。")

    @staticmethod
    def _require_chat_configuration(
        config: ProviderConfig, *, provider_label: str | None = None
    ) -> None:
        label = provider_label or (
            "Doubao Ark" if config.provider_name == "doubao-ark" else "OpenAI Compatible"
        )
        parsed = urlsplit(config.base_url)
        if not config.base_url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise TranslationProviderConfigurationError(f"缺少或无效的 {label} API Base URL。")
        if not config.model:
            raise TranslationProviderConfigurationError(f"缺少 {label} 模型名称。")

    @staticmethod
    def _chat_completions_url(base_url: str) -> str:
        normalized = base_url.strip().rstrip("/")
        return normalized if normalized.endswith("/chat/completions") else f"{normalized}/chat/completions"

    @staticmethod
    def _responses_url(base_url: str) -> str:
        normalized = base_url.strip().rstrip("/")
        return normalized if normalized.endswith("/responses") else f"{normalized}/responses"

    @staticmethod
    def _content_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, list):
            return ""
        pieces: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                pieces.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, dict):
                    text = text.get("value")
                if isinstance(text, str) and text.strip():
                    pieces.append(text.strip())
        return "\n".join(pieces).strip()

    def _extract_chat_completions_text(
        self, payload: Mapping[str, Any], *, allow_empty: bool = False
    ) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    text = self._content_text(message.get("content"))
                    if text:
                        return text
                    for field_name in ("reasoning_content", "reasoning"):
                        text = self._content_text(message.get(field_name))
                        if text:
                            return text
                text = first.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        if allow_empty:
            return ""
        raise TranslationProviderMalformedResponseError(
            "翻译服务没有返回可读取的文本，请重试。"
        )

    @staticmethod
    def _extract_responses_text(
        payload: Mapping[str, Any], *, allow_empty: bool = False
    ) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        pieces: list[str] = []
        output = payload.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or not isinstance(item.get("content"), list):
                    continue
                for content_item in item["content"]:
                    if isinstance(content_item, dict):
                        text = content_item.get("text")
                        if isinstance(text, str) and text.strip():
                            pieces.append(text.strip())
        if pieces:
            return "\n".join(pieces)
        if allow_empty:
            return ""
        raise TranslationProviderMalformedResponseError(
            "翻译服务没有返回可读取的文本，请重试。"
        )

    @staticmethod
    def _language_code(raw_value: Any) -> str | None:
        return {
            "CHS": "zh",
            "CHT": "zh-Hant",
            "JPN": "ja",
            "ENG": "en",
            "KOR": "ko",
        }.get(str(raw_value or "").strip().upper())

    @staticmethod
    def _body_indicates_context_limit(body: str) -> bool:
        normalized = str(body or "").casefold()
        return any(marker in normalized for marker in (
            "context_length",
            "context length",
            "maximum context",
            "max context",
            "too many tokens",
            "prompt is too long",
            "上下文长度",
            "请求内容过长",
        ))

    def _http_error(
        self, provider_label: str, status_code: int, body: str
    ) -> TranslationProviderError:
        if status_code in {401, 403}:
            return TranslationProviderAuthenticationError(
                f"{provider_label} 鉴权失败，请检查 API Key。"
            )
        if status_code == 429:
            return TranslationProviderRateLimitError(
                f"{provider_label} 请求过于频繁，请稍后重试。"
            )
        if status_code in {400, 413, 422} and self._body_indicates_context_limit(body):
            return TranslationProviderContextLengthError(
                f"{provider_label} 请求内容超过模型上下文长度。"
            )
        if status_code in {408, 425} or status_code >= 500:
            return TranslationProviderRetryableError(
                f"{provider_label} 服务暂时不可用，请稍后重试。"
            )
        return TranslationProviderUpstreamError(
            f"{provider_label} 拒绝了请求（HTTP {status_code}）。"
        )

    def _safe_transport_error(
        self, provider_name: str, exc: BaseException
    ) -> TranslationProviderError:
        if isinstance(exc, TranslationProviderError):
            return exc
        message = str(exc or "").casefold()
        label = {
            "gemini": "Gemini",
            "doubao-ark": "Doubao Ark",
            "openai-compatible": "OpenAI Compatible",
        }.get(provider_name, "翻译服务")
        if self._body_indicates_context_limit(message):
            return TranslationProviderContextLengthError(
                f"{label} 请求内容超过模型上下文长度。"
            )
        if any(marker in message for marker in ("unauthorized", "forbidden", "invalid api key")):
            return TranslationProviderAuthenticationError(
                f"{label} 鉴权失败，请检查 API Key。"
            )
        if "rate limit" in message or "resource exhausted" in message:
            return TranslationProviderRateLimitError(
                f"{label} 请求过于频繁，请稍后重试。"
            )
        return TranslationProviderRetryableError(
            f"{label} 暂时无法完成请求，请稍后重试。"
        )

    def _detached_error(
        self, provider_name: str, exc: BaseException
    ) -> TranslationProviderError:
        safe = self._safe_transport_error(provider_name, exc)
        return canonical_provider_error(safe)

    @staticmethod
    def _safe_provider_label(raw_label: Any) -> str:
        label = str(raw_label or "").strip()
        if label in {"Gemini", "Doubao Ark", "OpenAI Compatible"}:
            return label
        return "翻译服务"


class DeterministicTranslationProvider:
    """A deterministic adapter for coordinator and provider contract tests."""

    def __init__(
        self,
        *,
        translations: Mapping[str, str] | None = None,
        glossary_text: str = "[]",
        validation_preview: str = "测试",
        failure: BaseException | None = None,
    ) -> None:
        self._translations = dict(translations or {})
        self._glossary_text = glossary_text
        self._validation_preview = validation_preview
        self._failure = failure
        self.calls: list[str] = []

    def configure(self, raw_config: Mapping[str, Any]) -> ProviderConfig:
        return normalize_provider_config(raw_config)

    def runtime_environment(self, config: ProviderConfig) -> Mapping[str, str]:
        return {}

    def is_context_length_error(self, exc: BaseException) -> bool:
        return isinstance(exc, TranslationProviderContextLengthError)

    async def validate(self, config: ProviderConfig) -> ProviderValidationResult:
        self.calls.append("validate")
        self._raise_failure()
        return ProviderValidationResult(preview=self._validation_preview)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self.calls.append("translate")
        self._raise_failure()
        return TranslationResult(
            texts=tuple(self._translations.get(text, text) for text in request.texts)
        )

    async def extract_glossary(self, request: GlossaryRequest) -> GlossaryResult:
        self.calls.append("extract_glossary")
        self._raise_failure()
        return GlossaryResult(text=self._glossary_text)

    def _raise_failure(self) -> None:
        if self._failure is not None:
            if isinstance(self._failure, asyncio.CancelledError):
                raise self._failure
            if isinstance(self._failure, TranslationProviderError):
                raise canonical_provider_error(self._failure)
            raise TranslationProviderRetryableError(
                "翻译服务暂时无法完成请求，请稍后重试。"
            ) from None
