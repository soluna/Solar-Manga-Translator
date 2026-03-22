import os
import cv2
import numpy as np
from typing import List
from tqdm import tqdm

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


def _get_candidate_text(region: TextBlock, direction: str) -> str:
    original_direction = getattr(region, '_direction', 'auto')
    try:
        region._direction = direction
        return region.get_translation_for_rendering()
    finally:
        region._direction = original_direction


def _get_candidate_directions(region: TextBlock) -> List[str]:
    forced_direction = _normalize_direction(getattr(region, '_direction', 'auto'))
    if forced_direction != 'auto':
        return [forced_direction]

    inferred_direction = _normalize_direction(region.direction)
    if inferred_direction != 'auto':
        return [inferred_direction]
    return ['h' if region.horizontal else 'v']


def _render_candidate_box(region: TextBlock, direction: str, font_size: int, box_width: int, box_height: int,
                          hyphenate: bool, line_spacing: int):
    text = _get_candidate_text(region, direction)
    if not text:
        return None

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


def _select_region_layout(region: TextBlock, target_font_size: int, font_size_minimum: int, font_size_fixed: int,
                          box_width: int, box_height: int, hyphenate: bool, line_spacing: int):
    candidate_directions = _get_candidate_directions(region)
    search_sizes = [target_font_size] if font_size_fixed is not None else list(range(target_font_size, font_size_minimum - 1, -1))

    best_fit = None
    best_fallback = None

    for direction in candidate_directions:
        for font_size in search_sizes:
            candidate_box = _render_candidate_box(region, direction, font_size, box_width, box_height, hyphenate, line_spacing)
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
                                font_size_minimum: int, hyphenate: bool = True, line_spacing: int = None):
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
            target_font_size = original_region_font_size + font_size_offset
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

    text_render.set_font(font_path)
    text_regions = list(filter(lambda region: region.translation, text_regions))

    dst_points_list = resize_regions_to_font_size(
        img,
        text_regions,
        font_size_fixed,
        font_size_offset,
        font_size_minimum,
        hyphenate,
        line_spacing,
    )

    for region, dst_points in tqdm(zip(text_regions, dst_points_list), '[render]', total=len(text_regions)):
        if render_mask is not None:
            cv2.fillConvexPoly(render_mask, dst_points.astype(np.int32), 1)
        img = render(img, region, dst_points, hyphenate, line_spacing, disable_font_border)
    return img


def render(
    img,
    region: TextBlock,
    dst_points,
    hyphenate,
    line_spacing,
    disable_font_border
):
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

    if render_horizontally:
        temp_box = text_render.put_text_horizontal(
            region.font_size,
            region.get_translation_for_rendering(),
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
            region.get_translation_for_rendering(),
            round(norm_v[0]),
            region.alignment,
            fg,
            bg,
            line_spacing,
            getattr(region, 'letter_spacing', 1.0),
        )
    h, w, _ = temp_box.shape
    r_temp = w / h

    box = None
    if region.horizontal:
        if r_temp > r_orig:
            h_ext = int((w / r_orig - h) // 2) if r_orig > 0 else 0
            if h_ext >= 0:
                box = np.zeros((h + h_ext * 2, w, 4), dtype=np.uint8)
                box[h_ext:h_ext+h, 0:w] = temp_box
            else:
                box = temp_box.copy()
        else:
            w_ext = int((h * r_orig - w) // 2)
            if w_ext >= 0:
                box = np.zeros((h, w + w_ext * 2, 4), dtype=np.uint8)
                box[0:h, 0:w] = temp_box
            else:
                box = temp_box.copy()
    else:
        if r_temp > r_orig:
            h_ext = int(w / (2 * r_orig) - h / 2) if r_orig > 0 else 0
            if h_ext >= 0:
                box = np.zeros((h + h_ext * 2, w, 4), dtype=np.uint8)
                box[0:h, 0:w] = temp_box
            else:
                box = temp_box.copy()
        else:
            w_ext = int((h * r_orig - w) / 2)
            if w_ext >= 0:
                box = np.zeros((h, w + w_ext * 2, 4), dtype=np.uint8)
                box[0:h, w_ext:w_ext+w] = temp_box
            else:
                box = temp_box.copy()

    src_points = np.array([[0, 0], [box.shape[1], 0], [box.shape[1], box.shape[0]], [0, box.shape[0]]]).astype(np.float32)

    M, _ = cv2.findHomography(src_points, dst_points, cv2.RANSAC, 5.0)
    rgba_region = cv2.warpPerspective(box, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    x, y, w, h = cv2.boundingRect(dst_points.astype(np.int32))
    canvas_region = rgba_region[y:y+h, x:x+w, :3]
    mask_region = rgba_region[y:y+h, x:x+w, 3:4].astype(np.float32) / 255.0
    img[y:y+h, x:x+w] = np.clip((img[y:y+h, x:x+w].astype(np.float32) * (1 - mask_region) + canvas_region.astype(np.float32) * mask_region), 0, 255).astype(np.uint8)
    return img


async def dispatch_eng_render(img_canvas: np.ndarray, original_img: np.ndarray, text_regions: List[TextBlock], font_path: str = '', line_spacing: int = 0, disable_font_border: bool = False) -> np.ndarray:
    if len(text_regions) == 0:
        return img_canvas

    if not font_path:
        font_path = os.path.join(BASE_PATH, 'fonts/comic shanns 2.ttf')
    text_render.set_font(font_path)

    return render_textblock_list_eng(img_canvas, text_regions, line_spacing=line_spacing, size_tol=1.2, original_img=original_img, downscale_constraint=0.8, disable_font_border=disable_font_border)


async def dispatch_eng_render_pillow(img_canvas: np.ndarray, original_img: np.ndarray, text_regions: List[TextBlock], font_path: str = '', line_spacing: int = 0, disable_font_border: bool = False) -> np.ndarray:
    if len(text_regions) == 0:
        return img_canvas

    if not font_path:
        font_path = os.path.join(BASE_PATH, 'fonts/NotoSansMonoCJK-VF.ttf.ttc')
    text_render.set_font(font_path)

    return render_textblock_list_eng_pillow(font_path, img_canvas, text_regions, original_img=original_img, downscale_constraint=0.95)
