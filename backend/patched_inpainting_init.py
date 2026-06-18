from __future__ import annotations

from importlib import import_module
from typing import Any, Optional, TYPE_CHECKING

import numpy as np

from ..config import Inpainter, InpainterConfig

if TYPE_CHECKING:
    from .common import CommonInpainter


_INPAINTER_LOADERS = {
    Inpainter.default: ("inpainting_aot", "AotInpainter"),
    Inpainter.lama_large: ("inpainting_lama_mpe", "LamaLargeInpainter"),
    Inpainter.lama_mpe: ("inpainting_lama_mpe", "LamaMPEInpainter"),
    Inpainter.sd: ("inpainting_sd", "StableDiffusionInpainter"),
    Inpainter.none: ("none", "NoneInpainter"),
    Inpainter.original: ("original", "OriginalInpainter"),
}
INPAINTERS = dict(_INPAINTER_LOADERS)
inpainter_cache: dict[Inpainter, Any] = {}


def _normalize_inpainter_key(key: Inpainter | str) -> Inpainter:
    if isinstance(key, Inpainter):
        return key
    try:
        return Inpainter(str(key))
    except ValueError as exc:
        choices = ",".join(str(item) for item in _INPAINTER_LOADERS)
        raise ValueError(f'Could not find inpainter for: "{key}". Choose from the following: {choices}') from exc


def _load_inpainter_class(key: Inpainter):
    module_name, class_name = _INPAINTER_LOADERS[key]
    try:
        module = import_module(f"{__name__}.{module_name}")
    except ImportError as exc:
        if key == Inpainter.sd and "onnxruntime" in str(exc).lower():
            raise RuntimeError(
                "Stable Diffusion 去字需要 onnxruntime，但当前环境无法加载它。"
                "请改用 lama_large、lama_mpe、default、none 或 original。"
            ) from exc
        raise
    return getattr(module, class_name)


def get_inpainter(key: Inpainter | str, *args, **kwargs) -> "CommonInpainter":
    normalized_key = _normalize_inpainter_key(key)
    if not inpainter_cache.get(normalized_key):
        inpainter = _load_inpainter_class(normalized_key)
        inpainter_cache[normalized_key] = inpainter(*args, **kwargs)
    return inpainter_cache[normalized_key]


async def prepare(inpainter_key: Inpainter | str, device: str = "cpu"):
    from .common import OfflineInpainter

    inpainter = get_inpainter(inpainter_key)
    if isinstance(inpainter, OfflineInpainter):
        await inpainter.download()
        await inpainter.load(device)


async def dispatch(
    inpainter_key: Inpainter | str,
    image: np.ndarray,
    mask: np.ndarray,
    config: Optional[InpainterConfig],
    inpainting_size: int = 1024,
    device: str = "cpu",
    verbose: bool = False,
) -> np.ndarray:
    from .common import OfflineInpainter

    inpainter = get_inpainter(inpainter_key)
    if isinstance(inpainter, OfflineInpainter):
        await inpainter.load(device)
    config = config or InpainterConfig()
    return await inpainter.inpaint(image, mask, config, inpainting_size, verbose)


async def unload(inpainter_key: Inpainter | str):
    inpainter_cache.pop(_normalize_inpainter_key(inpainter_key), None)
