import os
import re
import cv2
import numpy as np
from typing import List
from tqdm import tqdm

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover - optional runtime dependency
    OpenCC = None

# from .ballon_extractor import extract_ballon_region
from . import text_render
from .text_render_eng import render_textblock_list_eng
from .text_render_pillow_eng import render_textblock_list_eng as render_textblock_list_eng_pillow
from ..utils import (
    BASE_PATH,
    TextBlock,
    color_difference,
    get_logger,
)

logger = get_logger('render')
_ACTIVE_FONT_KEY = None
_TRADITIONAL_TEXT_CONVERTER = None
_TERMINAL_PERIOD_CHARS = {"。", "．", ".", "｡", "﹒"}
_TERMINAL_PUNCTUATION_CHARS = _TERMINAL_PERIOD_CHARS | {"！", "!", "﹗", "？", "?", "…", "‥", "～", "~"}
_TRAILING_CLOSERS = "』」】》〉）)]}>'\"”’"


def parse_font_paths(path: str, default: List[str] = None) -> List[str]:
    if path:
        parsed = path.split(',')
        parsed = list(filter(lambda p: os.path.isfile(p), parsed))
    else:
        parsed = default or []
    return parsed


def fg_bg_compare(fg, bg):
    fg_avg = np.mean(fg)
    if color_difference(fg, bg) < 30:
        bg = (255, 255, 255) if fg_avg <= 127 else (0, 0, 0)
    return fg, bg


def count_text_length(text: str) -> float:
    """Calculate text length, treating っッぁぃぅぇぉ as 0.5 characters"""
    half_width_chars = 'っッぁぃぅぇぉ'
    length = 0.0
    for char in text.strip():
        if char in half_width_chars:
            length += 0.5
        else:
            length += 1.0
    return length


def _normalize_direction(direction: str) -> str:
    if direction in ('horizontal', 'h', 'hr'):
        return 'hr' if direction.endswith('r') else 'h'
    if direction in ('vertical', 'v', 'vr'):
        return 'vr' if direction.endswith('r') else 'v'
    return 'auto'


def _get_traditional_text_converter():
    global _TRADITIONAL_TEXT_CONVERTER
    if _TRADITIONAL_TEXT_CONVERTER is None:
        if OpenCC is None:
            _TRADITIONAL_TEXT_CONVERTER = False
        else:
            _TRADITIONAL_TEXT_CONVERTER = OpenCC('s2t')
    return _TRADITIONAL_TEXT_CONVERTER


def _split_trailing_closers(text: str) -> tuple[str, str]:
    if not text:
        return "", ""

    core = text
    closers = []
    while core and core[-1] in _TRAILING_CLOSERS:
        closers.append(core[-1])
        core = core[:-1]
    return core, "".join(reversed(closers))


def _has_terminal_punctuation(text: str) -> bool:
    core, _ = _split_trailing_closers(str(text or "").rstrip())
    return bool(core and core[-1] in _TERMINAL_PUNCTUATION_CHARS)


def _get_region_source_text(region: TextBlock) -> str:
    for field_name in ("text_raw", "text", "source_text", "original_text", "manual_source_text"):
        value = str(getattr(region, field_name, "") or "").strip()
        if value:
            return value

    texts = getattr(region, "texts", None)
    if isinstance(texts, list):
        joined = "".join(str(item or "") for item in texts).strip()
        if joined:
            return joined

    return ""


def _strip_unwanted_terminal_period(region: TextBlock, text: str) -> str:
    normalized_text = str(text or "").rstrip()
    if not normalized_text:
        return normalized_text

    source_text = _get_region_source_text(region)
    if not source_text or _has_terminal_punctuation(source_text):
        return normalized_text

    core, closers = _split_trailing_closers(normalized_text)
    if not core or core[-1] not in _TERMINAL_PERIOD_CHARS:
        return normalized_text

    return (core[:-1] + closers).rstrip()


def _convert_text_for_rendering(region: TextBlock, text: str) -> str:
    if not text:
        return text

    target_lang = str(getattr(region, 'target_lang', '') or '').upper()
    if target_lang not in {'CHS', 'CHT'}:
        return text

    converter = _get_traditional_text_converter()
    if not converter:
        return _strip_unwanted_terminal_period(region, text)

    try:
        converted_text = converter.convert(text)
    except Exception:
        converted_text = text
    return _strip_unwanted_terminal_period(region, converted_text)


def _normalize_font_key(font_path: str) -> str:
    if not font_path:
        return ''
    return os.path.normcase(os.path.abspath(font_path))


def _clear_font_sensitive_caches() -> None:
    get_char_glyph = getattr(text_render, 'get_char_glyph', None)
    if hasattr(get_char_glyph, 'cache_clear'):
        get_char_glyph.cache_clear()

    get_char_offset_x = getattr(text_render, 'get_char_offset_x', None)
    if hasattr(get_char_offset_x, 'cache_clear'):
        get_char_offset_x.cache_clear()


def _activate_font_path(font_path: str) -> str:
    global _ACTIVE_FONT_KEY

    selected_font = font_path if font_path and os.path.isfile(font_path) else ''
    font_key = _normalize_font_key(selected_font)
    if _ACTIVE_FONT_KEY != font_key:
        text_render.set_font(selected_font)
        _clear_font_sensitive_caches()
        _ACTIVE_FONT_KEY = font_key
    return selected_font


def _resolve_region_font_path(region: TextBlock, default_font_path: str) -> str:
    region_font_path = str(getattr(region, 'font_family', '') or '').strip()
    if region_font_path and os.path.isfile(region_font_path):
        return region_font_path
    return default_font_path if default_font_path and os.path.isfile(default_font_path) else ''


def _activate_region_font(region: TextBlock, default_font_path: str) -> str:
    return _activate_font_path(_resolve_region_font_path(region, default_font_path))


def _get_candidate_text(region: TextBlock, direction: str) -> str:
    original_direction = getattr(region, '_direction', 'auto')
    try:
        region._direction = direction
        text = _convert_text_for_rendering(region, region.get_translation_for_rendering())
    finally:
        region._direction = original_direction

    if not direction.startswith('h'):
        return text

    normalized_text = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    if '\n' not in normalized_text:
        return normalized_text

    manual_override = str(getattr(region, 'translation_override', '') or '').strip()
    if manual_override:
        return normalized_text

    target_lang = str(getattr(region, 'target_lang', '') or '').upper()
    joiner = '' if target_lang in {'CHS', 'CHT', 'JPN', 'JA', 'JP', 'ZH'} else ' '
    collapsed_text = re.sub(r'[ \t]*\n+[ \t]*', joiner, normalized_text)
    if joiner:
        collapsed_text = re.sub(r' {2,}', ' ', collapsed_text)
    return collapsed_text.strip()


def _get_candidate_directions(region: TextBlock) -> List[str]:
    forced_direction = _normalize_direction(getattr(region, '_direction', 'auto'))
    if forced_direction != 'auto':
        return [forced_direction]

    inferred_direction = _normalize_direction(region.direction)
    if inferred_direction != 'auto':
        return [inferred_direction]
    return ['h' if region.horizontal else 'v']


def _render_candidate_box(region: TextBlock, direction: str, font_size: int, box_width: int, box_height: int,
                          hyphenate: bool, line_spacing: int, default_font_path: str):
    text = _get_candidate_text(region, direction)
    if not text:
        return None

    _activate_region_font(region, default_font_path)

    fg = (0, 0, 0)
    # Always reserve border room during layout measurement so the final render
    # stays inside the chosen box even when an outline is enabled.
    bg = (255, 255, 255)

    if direction.startswith('h'):
        return text_render.put_text_horizontal(
            font_size,
            text,
            box_width,
            box_height,
            region.alignment,
            direction.endswith('r'),
            fg,
            bg,
            region.target_lang,
            hyphenate,
            line_spacing,
            getattr(region, 'letter_spacing', 1.0),
        )

    return text_render.put_text_vertical(
        font_size,
        text,
        box_height,
        region.alignment,
        fg,
        bg,
        line_spacing,
        getattr(region, 'letter_spacing', 1.0),
    )


def _layout_metrics(candidate_box: np.ndarray, box_width: int, box_height: int):
    if candidate_box is None or candidate_box.size == 0:
        return False, float('inf'), 0.0

    rendered_height, rendered_width = candidate_box.shape[:2]
    width_ratio = rendered_width / max(box_width, 1)
    height_ratio = rendered_height / max(box_height, 1)
    fits = rendered_width <= box_width and rendered_height <= box_height
    overflow = max(width_ratio, height_ratio)
    fill = min(width_ratio, 1.0) + min(height_ratio, 1.0)
    return fits, overflow, fill


def _direction_priority(region: TextBlock, direction: str) -> int:
    return 1 if direction.startswith('h') == region.horizontal else 0


def _alignment_offset(alignment: str, available_space: int) -> int:
    if available_space <= 0:
        return 0
    if alignment == 'center':
        return available_space // 2
    if alignment == 'right':
        return available_space
    return 0


def _compose_render_canvas(
    temp_box: np.ndarray,
    target_width: int,
    target_height: int,
    alignment: str,
    render_horizontally: bool,
) -> np.ndarray:
    canvas_width = max(int(target_width), 1)
    canvas_height = max(int(target_height), 1)
    canvas = np.zeros((canvas_height, canvas_width, 4), dtype=np.uint8)
    if temp_box is None or temp_box.size == 0:
        return canvas

    box_height, box_width = temp_box.shape[:2]
    canvas_width = max(canvas_width, int(box_width))
    canvas_height = max(canvas_height, int(box_height))
    canvas = np.zeros((canvas_height, canvas_width, 4), dtype=np.uint8)

    if render_horizontally:
        # Horizontal review mode anchors text to the left edge of the detected
        # region and vertically centers it. If the rendered text is larger than
        # the detected box we keep the same anchor and allow the canvas to grow.
        offset_x = 0
        offset_y = max((canvas_height - box_height) // 2, 0)
    else:
        # Vertical review mode anchors text to the top edge of the detected
        # region and horizontally centers it. Wider/taller content can extend
        # beyond the original box through the larger canvas.
        offset_x = max((canvas_width - box_width) // 2, 0)
        offset_y = 0

    canvas[offset_y:offset_y + box_height, offset_x:offset_x + box_width] = temp_box
    return canvas


def _expand_destination_quad(
    dst_points: np.ndarray,
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    render_horizontally: bool,
) -> np.ndarray:
    expanded = np.array(dst_points, dtype=np.float32, copy=True)
    restore_singleton_axis = False
    if expanded.shape == (1, 4, 2):
        expanded = expanded[0]
        restore_singleton_axis = True
    if expanded.shape != (4, 2):
        return np.array(dst_points, dtype=np.float32, copy=True)

    width_scale = max(float(source_width) / max(float(target_width), 1.0), 1.0)
    height_scale = max(float(source_height) / max(float(target_height), 1.0), 1.0)
    if np.isclose(width_scale, 1.0) and np.isclose(height_scale, 1.0):
        if restore_singleton_axis:
            return expanded[np.newaxis, ...]
        return expanded

    top_vec = expanded[1] - expanded[0]
    bottom_vec = expanded[2] - expanded[3]
    left_vec = expanded[3] - expanded[0]
    right_vec = expanded[2] - expanded[1]

    if width_scale > 1.0:
        extra_top = top_vec * (width_scale - 1.0)
        extra_bottom = bottom_vec * (width_scale - 1.0)
        if render_horizontally:
            expanded[1] += extra_top
            expanded[2] += extra_bottom
        else:
            half_top = extra_top * 0.5
            half_bottom = extra_bottom * 0.5
            expanded[0] -= half_top
            expanded[1] += half_top
            expanded[3] -= half_bottom
            expanded[2] += half_bottom

    if height_scale > 1.0:
        extra_left = left_vec * (height_scale - 1.0)
        extra_right = right_vec * (height_scale - 1.0)
        if render_horizontally:
            half_left = extra_left * 0.5
            half_right = extra_right * 0.5
            expanded[0] -= half_left
            expanded[3] += half_left
            expanded[1] -= half_right
            expanded[2] += half_right
        else:
            expanded[3] += extra_left
            expanded[2] += extra_right

    if restore_singleton_axis:
        return expanded[np.newaxis, ...]
    return expanded


def _select_region_layout(region: TextBlock, target_font_size: int, font_size_minimum: int, font_size_fixed: int,
                          box_width: int, box_height: int, hyphenate: bool, line_spacing: int,
                          default_font_path: str):
    candidate_directions = _get_candidate_directions(region)
    search_sizes = [max(int(target_font_size), max(int(font_size_minimum), 1))]

    best_fit = None
    best_fallback = None

    for direction in candidate_directions:
        for font_size in search_sizes:
            candidate_box = _render_candidate_box(
                region,
                direction,
                font_size,
                box_width,
                box_height,
                hyphenate,
                line_spacing,
                default_font_path,
            )
            fits, overflow, fill = _layout_metrics(candidate_box, box_width, box_height)
            direction_priority = _direction_priority(region, direction)

            if best_fallback is None or overflow < best_fallback[0] or (
                np.isclose(overflow, best_fallback[0]) and (
                    font_size > best_fallback[1] or
                    (font_size == best_fallback[1] and (fill > best_fallback[2] or direction_priority > best_fallback[3]))
                )
            ):
                best_fallback = (overflow, font_size, fill, direction_priority, direction)

            if not fits:
                continue

            if best_fit is None or font_size > best_fit[0] or (
                font_size == best_fit[0] and (fill > best_fit[1] or direction_priority > best_fit[2])
            ):
                best_fit = (font_size, fill, direction_priority, direction)
            break

    if best_fit is not None:
        return best_fit[3], best_fit[0]
    if best_fallback is not None:
        return best_fallback[4], best_fallback[1]
    return _normalize_direction(region.direction), target_font_size


def resize_regions_to_font_size(img: np.ndarray, text_regions: List['TextBlock'], font_size_fixed: int, font_size_offset: int,
                                font_size_minimum: int, hyphenate: bool = True, line_spacing: int = None,
                                default_font_path: str = ''):
    """
    Find a render direction and font size that keep translated text inside the
    original detected region.

    Args:
        img: Input image
        text_regions: List of text regions to process
        font_size_fixed: Fixed font size (overrides other font parameters)
        font_size_offset: Font size offset
        font_size_minimum: Minimum font size (-1 for auto-calculation)

    Returns:
        List of destination polygons for rendering
    """
    if font_size_minimum == -1:
        font_size_minimum = round((img.shape[0] + img.shape[1]) / 200)
    font_size_minimum = max(1, font_size_minimum)

    dst_points_list = []
    for region in text_regions:
        original_region_font_size = region.font_size
        if original_region_font_size <= 0:
            original_region_font_size = font_size_minimum

        if font_size_fixed is not None:
            target_font_size = font_size_fixed
        else:
            preserve_requested_size = bool(
                getattr(region, 'font_size_override_active', False)
                or getattr(region, 'direction_override_active', False)
            )
            effective_font_size_offset = 0 if preserve_requested_size else font_size_offset
            target_font_size = original_region_font_size + effective_font_size_offset
        target_font_size = max(target_font_size, font_size_minimum, 1)

        box_width = max(int(round(region.unrotated_size[0])), 1)
        box_height = max(int(round(region.unrotated_size[1])), 1)

        selected_direction, selected_font_size = _select_region_layout(
            region,
            target_font_size,
            font_size_minimum,
            font_size_fixed,
            box_width,
            box_height,
            hyphenate,
            line_spacing,
            default_font_path,
        )

        region._direction = selected_direction
        region.font_size = int(selected_font_size)
        dst_points_list.append(region.min_rect.copy())

    return dst_points_list


async def dispatch(
    img: np.ndarray,
    text_regions: List[TextBlock],
    font_path: str = '',
    font_size_fixed: int = None,
    font_size_offset: int = 0,
    font_size_minimum: int = 0,
    hyphenate: bool = True,
    render_mask: np.ndarray = None,
    line_spacing: int = None,
    disable_font_border: bool = False
    ) -> np.ndarray:

    _activate_font_path(font_path)
    text_regions = list(filter(lambda region: region.translation, text_regions))

    dst_points_list = resize_regions_to_font_size(
        img,
        text_regions,
        font_size_fixed,
        font_size_offset,
        font_size_minimum,
        hyphenate,
        line_spacing,
        font_path,
    )

    for region, dst_points in tqdm(zip(text_regions, dst_points_list), '[render]', total=len(text_regions)):
        if render_mask is not None:
            cv2.fillConvexPoly(render_mask, dst_points.astype(np.int32), 1)
        img = render(img, region, dst_points, hyphenate, line_spacing, disable_font_border, font_path)
    return img


def render(
    img,
    region: TextBlock,
    dst_points,
    hyphenate,
    line_spacing,
    disable_font_border,
    default_font_path
):
    _activate_region_font(region, default_font_path)
    fg, bg = region.get_font_colors()
    fg, bg = fg_bg_compare(fg, bg)

    if disable_font_border:
        bg = None

    middle_pts = (dst_points[:, [1, 2, 3, 0]] + dst_points) / 2
    norm_h = np.linalg.norm(middle_pts[:, 1] - middle_pts[:, 3], axis=1)
    norm_v = np.linalg.norm(middle_pts[:, 2] - middle_pts[:, 0], axis=1)
    r_orig = np.mean(norm_h / norm_v)

    render_direction = _normalize_direction(
        getattr(region, '_direction', 'auto') if getattr(region, '_direction', 'auto') != 'auto' else region.direction
    )
    render_horizontally = render_direction.startswith('h') if render_direction != 'auto' else region.horizontal
    reversed_direction = render_direction.endswith('r')
    render_text = _convert_text_for_rendering(region, region.get_translation_for_rendering())

    if render_horizontally:
        temp_box = text_render.put_text_horizontal(
            region.font_size,
            render_text,
            round(norm_h[0]),
            round(norm_v[0]),
            region.alignment,
            reversed_direction,
            fg,
            bg,
            region.target_lang,
            hyphenate,
            line_spacing,
            getattr(region, 'letter_spacing', 1.0),
        )
    else:
        temp_box = text_render.put_text_vertical(
            region.font_size,
            render_text,
            round(norm_v[0]),
            region.alignment,
            fg,
            bg,
            line_spacing,
            getattr(region, 'letter_spacing', 1.0),
        )
    target_width = max(int(round(norm_h[0])), 1)
    target_height = max(int(round(norm_v[0])), 1)
    box = _compose_render_canvas(
        temp_box,
        target_width,
        target_height,
        region.alignment,
        render_horizontally,
    )
    expanded_dst_points = _expand_destination_quad(
        np.array(dst_points, dtype=np.float32),
        int(box.shape[1]),
        int(box.shape[0]),
        target_width,
        target_height,
        render_horizontally,
    )
    src_points = np.array([[0, 0], [box.shape[1], 0], [box.shape[1], box.shape[0]], [0, box.shape[0]]]).astype(np.float32)

    M, _ = cv2.findHomography(src_points, expanded_dst_points, cv2.RANSAC, 5.0)
    rgba_region = cv2.warpPerspective(box, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    x, y, w, h = cv2.boundingRect(expanded_dst_points.astype(np.int32))
    x0 = max(int(x), 0)
    y0 = max(int(y), 0)
    x1 = min(int(x + w), img.shape[1])
    y1 = min(int(y + h), img.shape[0])
    if x1 <= x0 or y1 <= y0:
        return img
    canvas_region = rgba_region[y0:y1, x0:x1, :3]
    mask_region = rgba_region[y0:y1, x0:x1, 3:4].astype(np.float32) / 255.0
    img[y0:y1, x0:x1] = np.clip(
        (
            img[y0:y1, x0:x1].astype(np.float32) * (1 - mask_region)
            + canvas_region.astype(np.float32) * mask_region
        ),
        0,
        255,
    ).astype(np.uint8)
    return img


async def dispatch_eng_render(img_canvas: np.ndarray, original_img: np.ndarray, text_regions: List[TextBlock], font_path: str = '', line_spacing: int = 0, disable_font_border: bool = False) -> np.ndarray:
    if len(text_regions) == 0:
        return img_canvas

    if not font_path:
        font_path = os.path.join(BASE_PATH, 'fonts/comic shanns 2.ttf')
    _activate_font_path(font_path)

    return render_textblock_list_eng(img_canvas, text_regions, line_spacing=line_spacing, size_tol=1.2, original_img=original_img, downscale_constraint=0.8, disable_font_border=disable_font_border)


async def dispatch_eng_render_pillow(img_canvas: np.ndarray, original_img: np.ndarray, text_regions: List[TextBlock], font_path: str = '', line_spacing: int = 0, disable_font_border: bool = False) -> np.ndarray:
    if len(text_regions) == 0:
        return img_canvas

    if not font_path:
        font_path = os.path.join(BASE_PATH, 'fonts/NotoSansMonoCJK-VF.ttf.ttc')
    _activate_font_path(font_path)

    return render_textblock_list_eng_pillow(font_path, img_canvas, text_regions, original_img=original_img, downscale_constraint=0.95)
