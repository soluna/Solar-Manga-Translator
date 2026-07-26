from __future__ import annotations

import asyncio
import io
import inspect
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from engine.translator import TranslatorEngine
from diagnostics_bundle import build_diagnostics_zip
from runtime_paths import AppPaths
from task_manager import TaskManager
from translation_provider import (
    DeterministicTranslationProvider,
    GlossaryRequest,
    GlossaryResult,
    ProviderConfig,
    ProviderValidationResult,
    TranslationProvider,
    TranslationProviderAuthenticationError,
    TranslationProviderConfigurationError,
    TranslationProviderContextLengthError,
    TranslationProviderMalformedResponseError,
    TranslationProviderRateLimitError,
    TranslationProviderRetryableError,
    TranslationRequest,
    TranslationResult,
    UpstreamTranslationProvider,
)


def _test_paths(root: Path) -> AppPaths:
    return AppPaths(
        code_dir=BACKEND_DIR,
        app_data_dir=root / "app-data",
        models_dir=root / "models",
        output_dir=root / "output",
        logs_dir=root / "logs",
        cache_dir=root / "cache",
        config_dir=root / "config",
    )


class _InferenceTransport:
    def __init__(self, result: Any = None, failure: BaseException | None = None) -> None:
        self.result = result
        self.failure = failure
        self.calls: list[dict[str, Any]] = []

    async def translate_texts(self, texts: list[str], **kwargs: Any) -> Any:
        self.calls.append({"texts": texts, **kwargs})
        if self.failure is not None:
            raise self.failure
        return self.result if self.result is not None else tuple(f"译:{text}" for text in texts)


class _Response:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class _RawResponse(_Response):
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body


def _http_error(status: int, body: str) -> HTTPError:
    return HTTPError(
        "https://private.provider.example/v1",
        status,
        "unsafe status",
        {},
        io.BytesIO(body.encode("utf-8")),
    )


class TranslationProviderContractTests(unittest.IsolatedAsyncioTestCase):
    def make_config(self, **overrides: Any) -> ProviderConfig:
        payload = {
            "provider_name": "openai-compatible",
            "translator_name": "custom_openai",
            "target_lang": "CHS",
            "model": "model-a",
            "base_url": "https://provider.example/v1",
            "api_key": "super-secret-token",
        }
        payload.update(overrides)
        return ProviderConfig(**payload)

    def assert_safe_error_chain(self, exc: BaseException) -> None:
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            rendered = f"{current!s} {current!r}"
            self.assertNotIn("super-secret-token", rendered)
            self.assertNotIn("/private/provider/path", rendered)
            current = current.__cause__ or current.__context__
        self.assertIsNone(exc.__cause__)
        self.assertIsNone(exc.__context__)

    def test_protocol_is_shared_by_production_and_deterministic_fake(self) -> None:
        production = UpstreamTranslationProvider(_InferenceTransport())  # type: ignore[arg-type]
        fake = DeterministicTranslationProvider()

        self.assertIsInstance(production, TranslationProvider)
        self.assertIsInstance(fake, TranslationProvider)

    async def test_direct_openai_configuration_rejects_a_missing_model(self) -> None:
        provider = UpstreamTranslationProvider(_InferenceTransport())  # type: ignore[arg-type]
        config = provider.configure({
            "selected_translator": "openai-compatible",
            "openai_base_url": "https://provider.example/v1",
            "api_key": "super-secret-token",
        })

        self.assertEqual(config.model, "")
        with self.assertRaisesRegex(TranslationProviderConfigurationError, "模型名称"):
            await provider.validate(config)

    def test_request_and_result_repr_hide_secrets_prompts_and_provider_paths(self) -> None:
        config = self.make_config()
        translation = TranslationRequest(config=config, texts=("private source text",))
        glossary = GlossaryRequest(
            config=config,
            system_prompt="private system prompt",
            user_prompt="private project OCR",
        )
        translation_result = TranslationResult(texts=("private translated text",))
        glossary_result = GlossaryResult(text="private provider body")
        validation_result = ProviderValidationResult(preview="private validation preview")

        combined = " ".join((
            repr(config),
            repr(translation),
            repr(glossary),
            repr(translation_result),
            repr(glossary_result),
            repr(validation_result),
        ))
        for secret in (
            "super-secret-token",
            "private source text",
            "private system prompt",
            "private project OCR",
            "private translated text",
            "private provider body",
            "private validation preview",
            "https://provider.example/v1",
        ):
            self.assertNotIn(secret, combined)

    async def test_success_normalizes_validate_translate_and_glossary(self) -> None:
        responses = iter([
            _Response({"choices": [{"message": {"content": "  测试  "}}]}),
            _Response({"choices": [{"message": {"content": [{"text": "  []  "}]}}]}),
        ])
        inference = _InferenceTransport(result=("  译文 A  ", "译文 B"))
        provider = UpstreamTranslationProvider(
            inference,  # type: ignore[arg-type]
            url_opener=lambda *_args, **_kwargs: next(responses),
        )
        config = self.make_config()

        validated = await provider.validate(config)
        translated = await provider.translate(
            TranslationRequest(config=config, texts=("原文 A", "原文 B"), device="mps")
        )
        glossary = await provider.extract_glossary(
            GlossaryRequest(config=config, system_prompt="system", user_prompt="project")
        )

        self.assertEqual(validated.preview, "测试")
        self.assertEqual(translated.texts, ("译文 A", "译文 B"))
        self.assertEqual(glossary.text, "[]")
        self.assertEqual(inference.calls[0]["translator_name"], "custom_openai")
        self.assertEqual(inference.calls[0]["environment"]["CUSTOM_OPENAI_API_KEY"], "super-secret-token")

    async def test_http_errors_have_stable_safe_categories(self) -> None:
        unsafe_body = json.dumps({
            "error": {
                "message": "api key super-secret-token at /private/provider/path",
                "code": "context_length_exceeded",
            }
        })
        cases = (
            (401, TranslationProviderAuthenticationError),
            (429, TranslationProviderRateLimitError),
            (400, TranslationProviderContextLengthError),
            (503, TranslationProviderRetryableError),
        )
        for status, expected_error in cases:
            def fail(*_args: Any, **_kwargs: Any) -> Any:
                raise _http_error(status, unsafe_body)

            provider = UpstreamTranslationProvider(
                _InferenceTransport(),  # type: ignore[arg-type]
                url_opener=fail,
            )
            with self.subTest(status=status), self.assertRaises(expected_error) as raised:
                await provider.validate(self.make_config())
            rendered = f"{raised.exception!s} {raised.exception!r}"
            self.assertNotIn("super-secret-token", rendered)
            self.assertNotIn("/private/provider/path", rendered)
            self.assertEqual(raised.exception.category, expected_error.category)
            self.assert_safe_error_chain(raised.exception)

    async def test_transport_failure_is_retryable_and_redacted(self) -> None:
        def fail(*_args: Any, **_kwargs: Any) -> Any:
            raise URLError("super-secret-token /private/provider/path")

        provider = UpstreamTranslationProvider(
            _InferenceTransport(),  # type: ignore[arg-type]
            url_opener=fail,
        )

        with self.assertRaises(TranslationProviderRetryableError) as raised:
            await provider.validate(self.make_config())
        self.assertNotIn("super-secret-token", str(raised.exception))
        self.assertNotIn("/private/provider/path", str(raised.exception))

    async def test_public_provider_errors_detach_unsafe_exception_chains(self) -> None:
        unsafe = RuntimeError("super-secret-token /private/provider/path")
        translate_provider = UpstreamTranslationProvider(
            _InferenceTransport(failure=unsafe),  # type: ignore[arg-type]
        )
        with self.assertRaises(TranslationProviderRetryableError) as translated:
            await translate_provider.translate(
                TranslationRequest(
                    config=self.make_config(provider_name="local", translator_name="offline"),
                    texts=("one",),
                )
            )
        self.assert_safe_error_chain(translated.exception)

        def fail_transport(*_args: Any, **_kwargs: Any) -> Any:
            raise URLError("super-secret-token /private/provider/path")

        http_provider = UpstreamTranslationProvider(
            _InferenceTransport(),  # type: ignore[arg-type]
            url_opener=fail_transport,
        )
        with self.assertRaises(TranslationProviderRetryableError) as validated:
            await http_provider.validate(self.make_config())
        self.assert_safe_error_chain(validated.exception)

        class FailingGeminiClient:
            def generate(self, **_kwargs: Any) -> Any:
                raise RuntimeError("super-secret-token /private/provider/path")

        gemini_provider = UpstreamTranslationProvider(
            _InferenceTransport(),  # type: ignore[arg-type]
            gemini_client_factory=lambda _key: FailingGeminiClient(),
        )
        with self.assertRaises(TranslationProviderRetryableError) as glossary:
            await gemini_provider.extract_glossary(
                GlossaryRequest(
                    config=self.make_config(
                        provider_name="gemini",
                        translator_name="gemini",
                        model="gemini-model",
                    ),
                    system_prompt="system",
                    user_prompt="user",
                )
            )
        self.assert_safe_error_chain(glossary.exception)

    async def test_http_error_transport_and_decode_failures_have_no_raw_context(self) -> None:
        cases = (
            (
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    _http_error(
                        503,
                        "super-secret-token /private/provider/path",
                    )
                ),
                TranslationProviderRetryableError,
            ),
            (
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    URLError("super-secret-token /private/provider/path")
                ),
                TranslationProviderRetryableError,
            ),
            (
                lambda *_args, **_kwargs: _RawResponse(b"\xff\xfe"),
                TranslationProviderMalformedResponseError,
            ),
        )
        for opener, expected in cases:
            provider = UpstreamTranslationProvider(
                _InferenceTransport(),  # type: ignore[arg-type]
                url_opener=opener,
            )
            with self.subTest(expected=expected.__name__), self.assertRaises(expected) as raised:
                await provider.validate(self.make_config())
            self.assert_safe_error_chain(raised.exception)

    async def test_http_200_semantically_empty_response_is_malformed(self) -> None:
        provider = UpstreamTranslationProvider(
            _InferenceTransport(),  # type: ignore[arg-type]
            url_opener=lambda *_args, **_kwargs: _Response({}),
        )
        with self.assertRaises(TranslationProviderMalformedResponseError) as raised:
            await provider.validate(self.make_config())
        self.assert_safe_error_chain(raised.exception)

    async def test_malformed_http_and_translation_responses_are_classified(self) -> None:
        provider = UpstreamTranslationProvider(
            _InferenceTransport(result=("only one",)),  # type: ignore[arg-type]
            url_opener=lambda *_args, **_kwargs: _Response(["not", "an", "object"]),
        )
        with self.assertRaises(TranslationProviderMalformedResponseError):
            await provider.validate(self.make_config())
        with self.assertRaises(TranslationProviderMalformedResponseError):
            await provider.translate(
                TranslationRequest(config=self.make_config(), texts=("one", "two"))
            )

    async def test_non_string_translation_items_are_malformed(self) -> None:
        for invalid in (None, True, 1, object()):
            provider = UpstreamTranslationProvider(
                _InferenceTransport(result=(invalid,)),  # type: ignore[arg-type]
            )
            with self.subTest(invalid=type(invalid).__name__), self.assertRaises(
                TranslationProviderMalformedResponseError
            ) as raised:
                await provider.translate(
                    TranslationRequest(
                        config=self.make_config(provider_name="local", translator_name="offline"),
                        texts=("one",),
                    )
                )
            self.assert_safe_error_chain(raised.exception)

    async def test_manual_provider_config_rejects_invalid_base_url_and_model(self) -> None:
        provider = UpstreamTranslationProvider(_InferenceTransport())  # type: ignore[arg-type]
        cases = (
            self.make_config(base_url="file:///private/provider/path"),
            self.make_config(model=""),
        )
        for config in cases:
            with self.subTest(config=repr(config)), self.assertRaises(
                TranslationProviderConfigurationError
            ) as raised:
                await provider.validate(config)
            self.assert_safe_error_chain(raised.exception)

    async def test_context_length_classification_is_an_explicit_provider_seam(self) -> None:
        provider = UpstreamTranslationProvider(_InferenceTransport())  # type: ignore[arg-type]
        fake = DeterministicTranslationProvider()
        self.assertTrue(
            provider.is_context_length_error(RuntimeError("maximum context length exceeded"))
        )
        self.assertTrue(
            provider.is_context_length_error(
                TranslationProviderContextLengthError("safe context failure")
            )
        )
        self.assertFalse(fake.is_context_length_error(RuntimeError("ordinary failure")))

        translator_source = inspect.getsource(TranslatorEngine._is_glossary_context_length_error)
        for forbidden_marker in ("maximum context", "too many tokens", "prompt is too long"):
            self.assertNotIn(forbidden_marker, translator_source.casefold())

    async def test_cancellation_propagates_without_reclassification(self) -> None:
        provider = UpstreamTranslationProvider(
            _InferenceTransport(failure=asyncio.CancelledError()),  # type: ignore[arg-type]
        )
        with self.assertRaises(asyncio.CancelledError):
            await provider.translate(
                TranslationRequest(
                    config=self.make_config(provider_name="gemini", translator_name="gemini"),
                    texts=("one",),
                )
            )

    async def test_deterministic_fake_exercises_all_protocol_operations(self) -> None:
        provider = DeterministicTranslationProvider(
            translations={"原文": "译文"},
            glossary_text='[{"source":"山田"}]',
            validation_preview="测试",
        )
        config = provider.configure({
            "selected_translator": "openai-compatible",
            "translator": "custom_openai",
            "target_lang": "CHS",
        })

        validation = await provider.validate(config)
        translation = await provider.translate(
            TranslationRequest(config=config, texts=("原文",))
        )
        glossary = await provider.extract_glossary(
            GlossaryRequest(config=config, system_prompt="system", user_prompt="user")
        )

        self.assertEqual(validation.preview, "测试")
        self.assertEqual(translation.texts, ("译文",))
        self.assertIn("山田", glossary.text)
        self.assertEqual(provider.calls, ["validate", "translate", "extract_glossary"])

    async def test_deterministic_fake_never_exposes_an_unclassified_failure(self) -> None:
        for failure in (
            RuntimeError("super-secret-token /private/provider/path"),
            TranslationProviderRetryableError(
                "super-secret-token /private/provider/path"
            ),
        ):
            provider = DeterministicTranslationProvider(failure=failure)
            with self.subTest(failure=type(failure).__name__), self.assertRaises(
                TranslationProviderRetryableError
            ) as raised:
                await provider.validate(provider.configure({}))
            self.assert_safe_error_chain(raised.exception)


class TranslatorProviderIntegrationTests(unittest.TestCase):
    def test_translator_delegates_validation_translation_and_glossary_to_injected_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = DeterministicTranslationProvider(
                translations={"原文": "译文"},
                glossary_text='[{"source":"山田"}]',
            )
            engine = TranslatorEngine(
                root,
                app_paths=_test_paths(root),
                translation_provider=provider,
            )
            config = engine.normalize_user_config({
                "selected_translator": "openai-compatible",
                "translator": "openai-compatible",
                "openai_base_url": "https://provider.example/v1",
                "openai_model": "model",
                "api_key": "super-secret-token",
                "target_lang": "CHS",
                "use_gpu": False,
            })

            validation = asyncio.run(engine.validate_user_config(config))
            translation = asyncio.run(engine._translate_text_batch(["原文"], config, "project-a"))
            glossary = asyncio.run(engine._request_project_glossary_extraction(config, "project OCR"))

            self.assertTrue(validation["ok"])
            self.assertEqual(translation, ["译文"])
            self.assertIn("山田", glossary)
            self.assertEqual(provider.calls, ["validate", "translate", "extract_glossary"])

    def test_translator_uses_provider_context_classification_without_message_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentinel = RuntimeError("opaque-provider-failure")

            class ContextAwareProvider(DeterministicTranslationProvider):
                def is_context_length_error(self, exc: BaseException) -> bool:
                    return exc is sentinel

            provider = ContextAwareProvider()
            engine = TranslatorEngine(
                root,
                app_paths=_test_paths(root),
                translation_provider=provider,
            )
            prompts: list[str] = []

            async def request(_config: dict[str, Any], prompt: str) -> str:
                prompts.append(prompt)
                if len(prompts) == 1:
                    raise sentinel
                return "[]"

            engine._request_project_glossary_extraction = request  # type: ignore[method-assign]
            result = asyncio.run(engine._request_project_glossary_with_context_fallback(
                {"translator": "openai-compatible"},
                "项目上下文" * 5000,
                "CHS",
                retry=False,
                candidates=None,
            ))

            self.assertEqual(result, "[]")
            self.assertEqual(len(prompts), 2)
            self.assertGreater(len(prompts[0]), len(prompts[1]))

    def test_provider_secret_never_reaches_persisted_project_state_or_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "provider-secret-that-must-not-persist"
            engine = TranslatorEngine(
                root,
                app_paths=_test_paths(root),
                translation_provider=DeterministicTranslationProvider(),
            )
            project_id = "provider-secret-boundary"
            source_dir = engine._project_source_dir(project_id)
            translated_dir = engine._project_translated_dir(project_id)
            source_dir.mkdir(parents=True)
            translated_dir.mkdir(parents=True)
            session: dict[str, Any] = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [],
            }
            engine.capture_session_config(session, {
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "openai_base_url": "https://provider.example/v1",
                "openai_model": "model",
                "api_key": secret,
            })
            engine.initialize_project(project_id, session, "Provider secret boundary")
            engine.persist_project_state(
                project_id,
                session,
                snapshot_kind="provider_secret_test",
                snapshot_summary="provider boundary",
            )
            pending_state = engine._serialize_session_state(project_id, session)
            pending_state["workflow_stage"] = "detecting"
            engine.project_workspace.write_pending_artifact_set(
                project_id,
                action="detect",
                resume_fingerprint="provider-secret-boundary-fingerprint",
                base_head=engine.project_workspace.read_project_head(project_id),
                state_document=pending_state,
                files={},
            )

            stored_payloads = list(engine.project_workspace.project_dir(project_id).rglob("*.json"))
            self.assertTrue(stored_payloads)
            self.assertNotIn(
                secret.encode("utf-8"),
                b"\n".join(path.read_bytes() for path in stored_payloads),
            )

    def test_provider_failure_secret_never_reaches_task_events_or_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "provider-secret-in-upstream-body"

            def fail(*_args: Any, **_kwargs: Any) -> Any:
                raise _http_error(401, json.dumps({
                    "error": {"message": f"invalid api key {secret}"}
                }))

            provider = UpstreamTranslationProvider(
                _InferenceTransport(),  # type: ignore[arg-type]
                url_opener=fail,
            )
            config = provider.configure({
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "openai_base_url": "https://provider.example/v1",
                "openai_model": "model",
                "api_key": secret,
            })

            async def run_task() -> dict[str, Any]:
                manager = TaskManager()

                async def runner(_publish: Any) -> dict[str, Any]:
                    await provider.validate(config)
                    return {}

                task_id = manager.start("project-a", "translate", runner)
                return await manager.wait(task_id)

            task_snapshot = asyncio.run(run_task())
            bundle = build_diagnostics_zip(
                diagnostics={"task": task_snapshot},
                runtime={},
                settings={"api_key": secret},
                logs_dir=root,
            )

            serialized_task = json.dumps(task_snapshot, ensure_ascii=False)
            self.assertEqual(task_snapshot["status"], "failed")
            self.assertNotIn(secret, serialized_task)
            self.assertNotIn(secret.encode("utf-8"), bundle)
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                diagnostic_payload = archive.read("diagnostics.json")
            self.assertNotIn(secret.encode("utf-8"), diagnostic_payload)


if __name__ == "__main__":
    unittest.main()
