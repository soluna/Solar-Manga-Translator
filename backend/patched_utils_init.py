from .sort import *
from .bubble import is_ignore
from .generic import *
from .log import *
from .textblock import *
from .threading import *

_INFERENCE_EXPORTS = {
    "InfererModule",
    "InvalidModelMappingException",
    "ModelVerificationException",
    "ModelWrapper",
}

__all__ = sorted({
    *[name for name in globals() if not name.startswith("_")],
    *_INFERENCE_EXPORTS,
})


def __getattr__(name):
    if name not in _INFERENCE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from . import inference as _inference

    value = getattr(_inference, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted({*globals(), *_INFERENCE_EXPORTS})
