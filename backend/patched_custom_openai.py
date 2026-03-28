import asyncio
import json
import os
import re
import time
from typing import List
from urllib import error as urllib_error
from urllib import request as urllib_request

from ..config import TranslatorConfig
from .config_gpt import ConfigGPT

try:
    import openai
except ImportError:
    openai = None

try:
    from volcenginesdkarkruntime import Ark
except ImportError:
    Ark = None

from .common import CommonTranslator, VALID_LANGUAGES
from .keys import CUSTOM_OPENAI_API_KEY, CUSTOM_OPENAI_API_BASE, CUSTOM_OPENAI_MODEL, CUSTOM_OPENAI_MODEL_CONF


class ResponsesHTTPError(Exception):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"Responses API request failed: HTTP {status_code} {body}")
        self.status_code = status_code
        self.body = body


class CustomOpenAiTranslator(ConfigGPT, CommonTranslator):
    _INVALID_REPEAT_COUNT = 2
    _MAX_REQUESTS_PER_MINUTE = 40
    _TIMEOUT = 40
    _RETRY_ATTEMPTS = 3
    _TIMEOUT_RETRY_ATTEMPTS = 3
    _RATELIMIT_RETRY_ATTEMPTS = 3
    _MAX_SPLIT_ATTEMPTS = 5
    _MAX_TOKENS = 4096
    _RETURN_PROMPT = False
    _INCLUDE_TEMPLATE = False

    def __init__(self, model=None, api_base=None, api_key=None, check_openai_key=False):
        config_key = "ollama"
        if CUSTOM_OPENAI_MODEL_CONF:
            config_key += f".{CUSTOM_OPENAI_MODEL_CONF}"

        ConfigGPT.__init__(self, config_key=config_key)
        self.model = model
        CommonTranslator.__init__(self)
        self.api_key = api_key or CUSTOM_OPENAI_API_KEY or "ollama"
        self.client = openai.AsyncOpenAI(api_key=self.api_key)
        self.client.base_url = api_base or CUSTOM_OPENAI_API_BASE
        self.ark_client = None
        self.token_count = 0
        self.token_count_last = 0
        self.use_responses_api = os.getenv("CUSTOM_OPENAI_USE_RESPONSES", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        configured_model = (self.model or CUSTOM_OPENAI_MODEL or "").strip().lower()
        if configured_model.startswith("doubao-seed-translation"):
            self.use_responses_api = True
        if Ark is not None and configured_model.startswith("doubao-seed"):
            self.ark_client = Ark(
                base_url=str(self.client.base_url).rstrip("/"),
                api_key=self.api_key,
            )

    def parse_args(self, args: TranslatorConfig):
        self.config = args.chatgpt_config

    def extract_capture_groups(self, text, regex=r"(.*)"):
        pattern = re.compile(regex, re.DOTALL)
        matches = pattern.findall(text)
        extracted_text = "\n".join(
            "\n".join(m) if isinstance(m, tuple) else m for m in matches
        )
        return extracted_text.strip() if extracted_text else None

    def _assemble_prompts(self, from_lang: str, to_lang: str, queries: List[str]):
        prompt = ""

        if self._INCLUDE_TEMPLATE:
            prompt += self.prompt_template.format(to_lang=to_lang)

        if self._RETURN_PROMPT:
            prompt += "\nOriginal:"

        i_offset = 0
        for i, query in enumerate(queries):
            prompt += f"\n<|{i + 1 - i_offset}|>{query}"

            if self._MAX_TOKENS * 2 and len("".join(queries[i + 1 :])) > self._MAX_TOKENS:
                if self._RETURN_PROMPT:
                    prompt += "\n<|1|>"
                yield prompt.lstrip(), i + 1 - i_offset
                prompt = self.prompt_template.format(to_lang=to_lang)
                i_offset = i + 1

        if self._RETURN_PROMPT:
            prompt += "\n<|1|>"

        yield prompt.lstrip(), len(queries) - i_offset

    def _format_prompt_log(self, to_lang: str, prompt: str) -> str:
        if to_lang in self.chat_sample:
            return "\n".join(
                [
                    "System:",
                    self.chat_system_template.format(to_lang=to_lang),
                    "User:",
                    self.chat_sample[to_lang][0],
                    "Assistant:",
                    self.chat_sample[to_lang][1],
                    "User:",
                    prompt,
                ]
            )
        return "\n".join(
            [
                "System:",
                self.chat_system_template.format(to_lang=to_lang),
                "User:",
                prompt,
            ]
        )

    def _format_request_log(self, model_name: str, from_lang: str, to_lang: str, prompt: str) -> str:
        if self._is_translation_responses_model(model_name):
            translation_options = {
                "target_language": self._map_translation_language_code(to_lang) or "zh-Hant",
            }
            source_language = self._map_translation_language_code(from_lang)
            if source_language:
                translation_options["source_language"] = source_language
            return (
                "Translation model payload:\n"
                + json.dumps(
                    {
                        "text": prompt,
                        "translation_options": translation_options,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return self._format_prompt_log(to_lang, prompt)

    def _normalize_response_markers(self, text: str) -> str:
        return re.sub(r"<\|?(\d+)\|?>", lambda match: f"<|{match.group(1)}|>", text or "")

    def _clean_translation_chunk(self, text: str) -> str:
        text = self._normalize_response_markers(text)
        text = re.sub(r"^\s*<\|\d+\|>\s*", "", text)
        return text.strip()

    def _parse_translation_response(self, response: str, query_size: int):
        captured = self.extract_capture_groups(response, rf"{self.rgx_capture}")
        response = captured if isinstance(captured, str) and captured.strip() else response
        response = self._normalize_response_markers(response).strip()
        if not response:
            return None

        if query_size == 1:
            single = self._clean_translation_chunk(response)
            return [single] if single else None

        matches = list(re.finditer(r"(?s)<\|(\d+)\|>(.*?)(?=<\|\d+\|>|$)", response))
        if matches:
            mapped: dict[int, str] = {}
            for match in matches:
                idx = int(match.group(1))
                text = self._clean_translation_chunk(match.group(2))
                if 1 <= idx <= query_size and text and idx not in mapped:
                    mapped[idx] = text
            if len(mapped) == query_size:
                return [mapped[i] for i in range(1, query_size + 1)]

        line_candidates = [self._clean_translation_chunk(line) for line in response.splitlines()]
        line_candidates = [line for line in line_candidates if line]
        if len(line_candidates) == query_size:
            return line_candidates

        return None

    async def _request_with_retries(self, from_lang: str, to_lang: str, prompt: str) -> str:
        ratelimit_attempt = 0
        server_error_attempt = 0
        timeout_attempt = 0
        while True:
            request_task = asyncio.create_task(self._request_translation(from_lang, to_lang, prompt))
            started = time.time()
            while not request_task.done():
                await asyncio.sleep(0.1)
                if time.time() - started > self._TIMEOUT + (timeout_attempt * self._TIMEOUT / 2):
                    if timeout_attempt >= self._TIMEOUT_RETRY_ATTEMPTS:
                        raise Exception("translator server did not respond quickly enough.")
                    timeout_attempt += 1
                    self.logger.warning(f"Restarting request due to timeout. Attempt: {timeout_attempt}")
                    request_task.cancel()
                    request_task = asyncio.create_task(self._request_translation(from_lang, to_lang, prompt))
                    started = time.time()
            try:
                return await request_task
            except Exception as exc:
                if openai is not None and isinstance(exc, openai.RateLimitError):
                    ratelimit_attempt += 1
                    if ratelimit_attempt >= self._RATELIMIT_RETRY_ATTEMPTS:
                        raise
                    self.logger.warning(
                        f"Restarting request due to rate limiting by the upstream model service. Attempt: {ratelimit_attempt}"
                    )
                    await asyncio.sleep(2)
                    continue
                if openai is not None and isinstance(exc, openai.APIError):
                    server_error_attempt += 1
                    if server_error_attempt >= self._RETRY_ATTEMPTS:
                        self.logger.error(
                            "The upstream model service encountered a server error. Use a different translator or try again later."
                        )
                        raise
                    self.logger.warning(f"Restarting request due to a server error. Attempt: {server_error_attempt}")
                    await asyncio.sleep(1)
                    continue
                raise

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str]) -> List[str]:
        translations = [""] * len(queries)
        self.logger.debug(f"Temperature: {self.temperature}, TopP: {self.top_p}")

        async def translate_batch(batch_queries: List[str], batch_indices: List[int], split_level: int = 0) -> bool:
            assembled = list(self._assemble_prompts(from_lang, to_lang, batch_queries))
            if len(assembled) != 1:
                self.logger.warning(
                    f"Unexpected multi-prompt batch assembly for {len(batch_queries)} queries; splitting before request."
                )
                if len(batch_queries) <= 1:
                    return False
                mid = max(1, len(batch_queries) // 2)
                results = await asyncio.gather(
                    translate_batch(batch_queries[:mid], batch_indices[:mid], split_level + 1),
                    translate_batch(batch_queries[mid:], batch_indices[mid:], split_level + 1),
                )
                return all(results)

            prompt, query_size = assembled[0]
            split_prefix = " (split)" if split_level > 0 else ""
            model_name = self.model or CUSTOM_OPENAI_MODEL
            self.logger.debug(
                f"-- GPT Prompt{split_prefix} --\n" + self._format_request_log(model_name, from_lang, to_lang, prompt)
            )

            last_response = ""
            for attempt in range(self._RETRY_ATTEMPTS):
                last_response = await self._request_with_retries(from_lang, to_lang, prompt)
                parsed = self._parse_translation_response(last_response, query_size)
                if parsed and len(parsed) == query_size and all(text.strip() for text in parsed):
                    for idx, text in zip(batch_indices, parsed):
                        translations[idx] = text.strip()
                    self.logger.info(f"Batch translated: {len([t for t in translations if t])}/{len(queries)} completed.")
                    self.logger.debug(f"Completed translations: {[t if t else queries[i] for i, t in enumerate(translations)]}")
                    return True

                remaining_attempts = self._RETRY_ATTEMPTS - attempt - 1
                if remaining_attempts > 0:
                    self.logger.warning(
                        f"Could not map response back to {query_size} text regions. Retrying {remaining_attempts} more time(s) before splitting."
                    )

            if query_size == 1:
                fallback_text = self._clean_translation_chunk(last_response)
                if fallback_text:
                    translations[batch_indices[0]] = fallback_text
                    self.logger.info(f"Batch translated: {len([t for t in translations if t])}/{len(queries)} completed.")
                    return True

            if split_level < self._MAX_SPLIT_ATTEMPTS and len(batch_queries) > 1:
                self.logger.warning(
                    f"Response segmentation mismatch for {len(batch_queries)} queries. Splitting the batch to protect text-region mapping."
                )
                mid = max(1, len(batch_queries) // 2)
                results = await asyncio.gather(
                    translate_batch(batch_queries[:mid], batch_indices[:mid], split_level + 1),
                    translate_batch(batch_queries[mid:], batch_indices[mid:], split_level + 1),
                )
                return all(results)

            self.logger.error("Unable to map translated segments back to text regions for the current batch.")
            if last_response:
                self.logger.error(f"Last raw response for failed batch:\n{last_response}")
            for idx in batch_indices:
                if not translations[idx]:
                    translations[idx] = queries[idx]
            return False

        idx_offset = 0
        for _, batch_size in self._assemble_prompts(from_lang, to_lang, queries):
            batch_queries = queries[idx_offset : idx_offset + batch_size]
            batch_indices = list(range(idx_offset, idx_offset + batch_size))
            await translate_batch(batch_queries, batch_indices)
            idx_offset += batch_size

        for t in translations:
            if "I'm sorry, but I can't assist with that request" in t:
                raise Exception("translations contain error text")
        self.logger.debug(translations)
        if self.token_count_last:
            self.logger.info(f"Used {self.token_count_last} tokens (Total: {self.token_count})")

        return translations

    def _build_messages(self, to_lang: str, prompt: str):
        messages = [{"role": "system", "content": self.chat_system_template.format(to_lang=to_lang)}]
        lang_chat_samples = self.get_chat_sample(to_lang)
        if lang_chat_samples:
            messages.append({"role": "user", "content": lang_chat_samples[0]})
            messages.append({"role": "assistant", "content": lang_chat_samples[1]})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _extract_responses_text(self, response) -> str:
        if isinstance(response, dict):
            output_text = response.get("output_text")
            if isinstance(output_text, str) and output_text.strip():
                return output_text

            pieces: list[str] = []
            for item in response.get("output", []) or []:
                for content in item.get("content", []) or []:
                    text = content.get("text")
                    if isinstance(text, str) and text.strip():
                        pieces.append(text)

            merged = "\n".join(piece.strip() for piece in pieces if piece and piece.strip()).strip()
            if merged:
                return merged

            raise RuntimeError("Responses API did not return any text output.")

        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        pieces: list[str] = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if isinstance(text, str) and text.strip():
                    pieces.append(text)

        merged = "\n".join(piece.strip() for piece in pieces if piece and piece.strip()).strip()
        if merged:
            return merged

        raise RuntimeError("Responses API did not return any text output.")

    def _extract_total_tokens(self, usage) -> int:
        if isinstance(usage, dict):
            total_tokens = usage.get("total_tokens")
            if isinstance(total_tokens, int):
                return total_tokens
            input_tokens = usage.get("input_tokens", 0) or 0
            output_tokens = usage.get("output_tokens", 0) or 0
            if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                return input_tokens + output_tokens
            return 0

        total_tokens = getattr(usage, "total_tokens", None)
        if isinstance(total_tokens, int):
            return total_tokens

        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            return input_tokens + output_tokens

        return 0

    def _responses_endpoint_url(self) -> str:
        base_url = str(self.client.base_url).rstrip("/")
        if base_url.endswith("/responses"):
            return base_url
        return f"{base_url}/responses"

    def _is_translation_responses_model(self, model_name: str) -> bool:
        return str(model_name or "").strip().lower().startswith("doubao-seed-translation")

    def _responses_prompt_input(self, model_name: str, to_lang: str, prompt: str):
        return [
            {
                "type": "message",
                "role": message["role"],
                "content": [
                    {
                        "type": "input_text",
                        "text": str(message["content"]),
                    }
                ],
            }
            for message in self._build_messages(to_lang, prompt)
        ]

    def _map_translation_language_code(self, lang: str) -> str | None:
        normalized = str(lang or "").strip().upper()
        mapping = {
            "AUTO": None,
            "JPN": "ja",
            "JA": "ja",
            "ENG": "en",
            "EN": "en",
            "CHS": "zh",
            "ZH": "zh",
            "CHT": "zh-Hant",
            "KOR": "ko",
            "KO": "ko",
            "FRA": "fr",
            "FR": "fr",
            "DEU": "de",
            "DE": "de",
            "ESP": "es",
            "ES": "es",
            "ITA": "it",
            "IT": "it",
            "RUS": "ru",
            "RU": "ru",
            "THA": "th",
            "TH": "th",
            "VIE": "vi",
            "VI": "vi",
            "PTB": "pt",
            "PT": "pt",
        }
        return mapping.get(normalized)

    def _translation_responses_prompt_input(self, from_lang: str, to_lang: str, prompt: str):
        translation_options = {
            "target_language": self._map_translation_language_code(to_lang) or "zh-Hant",
        }
        source_language = self._map_translation_language_code(from_lang)
        if source_language:
            translation_options["source_language"] = source_language
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                        "translation_options": translation_options,
                    }
                ],
            }
        ]

    async def _call_responses_api(self, model_name: str, prompt_input):
        if self.ark_client is not None and str(model_name or "").strip().lower().startswith("doubao-seed"):
            self.logger.debug("Using official Ark SDK for Responses API.")
            return await asyncio.to_thread(
                self.ark_client.responses.create,
                model=model_name,
                input=prompt_input,
            )
        if hasattr(self.client, "responses"):
            return await self.client.responses.create(
                model=model_name,
                input=prompt_input,
            )

        self.logger.debug("OpenAI SDK does not expose responses API; falling back to direct HTTP request.")
        return await asyncio.to_thread(
            self._request_responses_via_http_sync,
            model_name,
            prompt_input,
        )

    def _request_responses_via_http_sync(self, model_name: str, prompt_input):
        payload = {
            "model": model_name,
            "input": prompt_input,
        }
        request = urllib_request.Request(
            self._responses_endpoint_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self._TIMEOUT) as response:
                body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise ResponsesHTTPError(exc.code, body) from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"Responses API request failed: {exc}") from exc

        return json.loads(body)

    async def _request_translation(self, from_lang: str, to_lang: str, prompt: str) -> str:
        messages = self._build_messages(to_lang, prompt)
        model_name = self.model or CUSTOM_OPENAI_MODEL
        force_responses_api = self._is_translation_responses_model(model_name)

        if self.use_responses_api or force_responses_api:
            self.logger.debug(f"Using Responses API for model: {model_name}")
            if force_responses_api:
                prompt_input = self._translation_responses_prompt_input(from_lang, to_lang, prompt)
                self.logger.debug(
                    "Using translation-model Responses payload with translation_options:\n"
                    + json.dumps(prompt_input, ensure_ascii=False, indent=2)
                )
                response = await self._call_responses_api(model_name, prompt_input)
            else:
                prompt_input = self._responses_prompt_input(model_name, to_lang, prompt)
                response = await self._call_responses_api(model_name, prompt_input)
            response_text = self._extract_responses_text(response)
            self.logger.debug("\n-- GPT Response (raw) --")
            self.logger.debug(response_text)
            self.logger.debug("------------------------\n")

            usage_total = self._extract_total_tokens(
                response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
            )
            self.token_count += usage_total
            self.token_count_last = usage_total
            return response_text

        response = await self.client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=self._MAX_TOKENS // 2,
            temperature=self.temperature,
            top_p=self.top_p,
        )

        self.logger.debug("\n-- GPT Response (raw) --")
        self.logger.debug(response.choices[0].message.content)
        self.logger.debug("------------------------\n")

        self.token_count += response.usage.total_tokens
        self.token_count_last = response.usage.total_tokens

        return response.choices[0].message.content
