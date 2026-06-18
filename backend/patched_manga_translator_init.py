import logging

import colorama
from dotenv import load_dotenv

colorama.init(autoreset=True)
load_dotenv()

logger = logging.getLogger("manga_translator")

_TRANSLATOR_EXPORTS = {
    "MangaTranslator",
    "TranslationInterrupt",
    "apply_dictionary",
    "load_dictionary",
    "set_main_logger",
}
_CONFIG_EXPORTS = {
    "Colorizer",
    "Config",
    "Detector",
    "Inpainter",
    "Renderer",
    "Translator",
}
_UTIL_EXPORTS = {
    "Context",
}

__all__ = sorted({*_TRANSLATOR_EXPORTS, *_CONFIG_EXPORTS, *_UTIL_EXPORTS, "logger"})


def __getattr__(name):
    if name in _CONFIG_EXPORTS:
        from . import config as _config

        value = getattr(_config, name)
        globals()[name] = value
        return value

    if name in _UTIL_EXPORTS:
        from .utils.generic import Context

        globals()["Context"] = Context
        return Context

    if name in _TRANSLATOR_EXPORTS:
        from . import manga_translator as _manga_translator

        value = getattr(_manga_translator, name)
        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted({*globals(), *__all__})
