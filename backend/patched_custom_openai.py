import asyncio
import os
import re
import time
from typing import List

from ..config import TranslatorConfig
from .config_gpt import ConfigGPT

try:
    import openai
except ImportError:
    openai = None

from .common import CommonTranslator, VALID_LANGUAGES
from .keys import CUSTOM_OPENAI_API_KEY, CUSTOM_OPENAI_API_BASE, CUSTOM_OPENAI_MODEL, CUSTOM_OPENAI_MODEL_CONF


class CustomOpenAiTranslator(ConfigGPT, CommonTranslator):
    _INVALID_REPEAT_COUNT = 2
    _MAX_REQUESTS_PER_MINUTE = 40
    _TIMEOUT = 40
    _RETRY_ATTEMPTS = 3
    _TIMEOUT_RETRY_ATTEMPTS = 3
    _RATELIMIT_RETRY_ATTEMPTS = 3
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
        self.client = openai.AsyncOpenAI(api_key=api_key or CUSTOM_OPENAI_API_KEY or "ollama")
        self.client.base_url = api_base or CUSTOM_OPENAI_API_BASE
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

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str]) -> List[str]:
        translations = []
        self.logger.debug(f"Temperature: {self.temperature}, TopP: {self.top_p}")

        for prompt, query_size in self._assemble_prompts(from_lang, to_lang, queries):
            self.logger.debug("-- GPT Prompt --\n" + self._format_prompt_log(to_lang, prompt))

            ratelimit_attempt = 0
            server_error_attempt = 0
            timeout_attempt = 0
            while True:
                request_task = asyncio.create_task(self._request_translation(to_lang, prompt))
                started = time.time()
                while not request_task.done():
                    await asyncio.sleep(0.1)
                    if time.time() - started > self._TIMEOUT + (timeout_attempt * self._TIMEOUT / 2):
                        if timeout_attempt >= self._TIMEOUT_RETRY_ATTEMPTS:
                            raise Exception("translator server did not respond quickly enough.")
                        timeout_attempt += 1
                        self.logger.warning(f"Restarting request due to timeout. Attempt: {timeout_attempt}")
                        request_task.cancel()
                        request_task = asyncio.create_task(self._request_translation(to_lang, prompt))
                        started = time.time()
                try:
                    response = await request_task
                    break
                except openai.RateLimitError:
                    ratelimit_attempt += 1
                    if ratelimit_attempt >= self._RATELIMIT_RETRY_ATTEMPTS:
                        raise
                    self.logger.warning(
                        f"Restarting request due to rate limiting by the upstream model service. Attempt: {ratelimit_attempt}"
                    )
                    await asyncio.sleep(2)
                except openai.APIError:
                    server_error_attempt += 1
                    if server_error_attempt >= self._RETRY_ATTEMPTS:
                        self.logger.error(
                            "The upstream model service encountered a server error. Use a different translator or try again later."
                        )
                        raise
                    self.logger.warning(f"Restarting request due to a server error. Attempt: {server_error_attempt}")
                    await asyncio.sleep(1)

            response = self.extract_capture_groups(response, rf"{self.rgx_capture}")

            def add_pipe(match):
                number = match.group(1)
                return f"<|{number}|>"

            response = re.sub(r"<\|?(\d+)\|?>", add_pipe, response)

            new_translations = re.split(r"<\|\d+\|>", "pre_1\n" + response)[1:]

            if not new_translations:
                new_translations = [response]

            new_translations = [t.strip() for t in new_translations]

            if not new_translations[0].strip():
                new_translations = new_translations[1:]

            if len(new_translations) <= 1 and query_size > 1:
                new_translations = re.split(r"\n", response)

            if len(new_translations) > query_size:
                new_translations = new_translations[:query_size]
            elif len(new_translations) < query_size:
                new_translations = new_translations + [""] * (query_size - len(new_translations))

            translations.extend([t.strip() for t in new_translations])

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
        total_tokens = getattr(usage, "total_tokens", None)
        if isinstance(total_tokens, int):
            return total_tokens

        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            return input_tokens + output_tokens

        return 0

    async def _request_translation(self, to_lang: str, prompt: str) -> str:
        messages = self._build_messages(to_lang, prompt)
        model_name = self.model or CUSTOM_OPENAI_MODEL
        force_responses_api = str(model_name or "").strip().lower().startswith("doubao-seed-translation")

        if self.use_responses_api or force_responses_api:
            self.logger.debug(f"Using Responses API for model: {model_name}")
            response = await self.client.responses.create(
                model=model_name,
                input=messages,
                max_output_tokens=self._MAX_TOKENS // 2,
                temperature=self.temperature,
                top_p=self.top_p,
            )
            response_text = self._extract_responses_text(response)
            self.logger.debug("\n-- GPT Response (raw) --")
            self.logger.debug(response_text)
            self.logger.debug("------------------------\n")

            usage_total = self._extract_total_tokens(getattr(response, "usage", None))
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
