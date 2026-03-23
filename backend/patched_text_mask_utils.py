from typing import Tuple, List
import numpy as np
import cv2
import math
import os
from pathlib import Path

from tqdm import tqdm
from shapely.geometry import Polygon

from ..utils import Quadrilateral, image_resize

COLOR_RANGE_SIGMA = 1.5 # how many stddev away is considered the same color
MASK_DEBUG_COUNTER = 0

def save_rgb(fn, img):
    if len(img.shape) == 3 and img.shape[2] == 3:
        cv2.imwrite(fn, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    else:
        cv2.imwrite(fn, img)

def _save_mask_debug_images(
    img: np.ndarray,
    input_mask: np.ndarray,
    core_mask: np.ndarray,
    box_cleanup_mask: np.ndarray,
    residual_mask: np.ndarray,
    final_mask: np.ndarray,
):
    debug_dir = os.getenv("MT_MASK_DEBUG_DIR", "").strip()
    if not debug_dir:
        return

    try:
        Path(debug_dir).mkdir(parents=True, exist_ok=True)
        global MASK_DEBUG_COUNTER
        MASK_DEBUG_COUNTER += 1
        prefix = f"{MASK_DEBUG_COUNTER:04d}"

        save_rgb(str(Path(debug_dir) / f"{prefix}_image.png"), img)
        cv2.imwrite(str(Path(debug_dir) / f"{prefix}_input_mask.png"), input_mask)
        cv2.imwrite(str(Path(debug_dir) / f"{prefix}_core_mask.png"), core_mask)
        cv2.imwrite(str(Path(debug_dir) / f"{prefix}_box_cleanup_mask.png"), box_cleanup_mask)
        cv2.imwrite(str(Path(debug_dir) / f"{prefix}_edge_residual_mask.png"), residual_mask)
        cv2.imwrite(str(Path(debug_dir) / f"{prefix}_final_mask.png"), final_mask)
    except Exception:
        pass

def area_overlap(x1, y1, w1, h1, x2, y2, w2, h2):  # returns None if rectangles don't intersect
    x_overlap = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
    y_overlap = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
    return x_overlap * y_overlap

def dist(x1, y1, x2, y2):
    return math.sqrt((x1 - x2) * (x1 - x2) + (y1 - y2) * (y1 - y2))

def rect_distance(x1, y1, x1b, y1b, x2, y2, x2b, y2b):
    left = x2b < x1
    right = x1b < x2
    bottom = y2b < y1
    top = y1b < y2
    if top and left:
        return dist(x1, y1b, x2b, y2)
    elif left and bottom:
        return dist(x1, y1, x2b, y2b)
    elif bottom and right:
        return dist(x1b, y1, x2, y2b)
    elif right and top:
        return dist(x1b, y1b, x2, y2)
    elif left:
        return x1 - x2b
    elif right:
        return x2 - x1b
    elif bottom:
        return y1 - y2b
    elif top:
        return y2 - y1b
    else:             # rectangles intersect
        return 0

def extend_rect(x, y, w, h, max_x, max_y, extend_size):
    x1 = max(x - extend_size, 0)
    y1 = max(y - extend_size, 0)
    w1 = min(w + extend_size * 2, max_x - x1 - 1)
    h1 = min(h + extend_size * 2, max_y - y1 - 1)
    return x1, y1, w1, h1

def interval_overlap(a1, a2, b1, b2):
    return max(0, min(a2, b2) - max(a1, b1))

def interval_gap(a1, a2, b1, b2):
    return max(0, max(a1, b1) - min(a2, b2))

def clamp_rect(x, y, w, h, max_x, max_y):
    x = max(0, min(int(x), max_x - 1))
    y = max(0, min(int(y), max_y - 1))
    w = max(1, min(int(w), max_x - x))
    h = max(1, min(int(h), max_y - y))
    return x, y, w, h

def _local_overlap_area(comp_x, comp_y, comp_w, comp_h, txt_x, txt_y, txt_w, txt_h):
    return area_overlap(comp_x, comp_y, comp_w, comp_h, txt_x, txt_y, txt_w, txt_h)

def _detect_box_cleanup_mask(img: np.ndarray, textline: Quadrilateral):
    if img.ndim == 3 and img.shape[2] == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    else:
        gray = img

    tx, ty, tw, th = map(int, textline.aabb.xywh)
    if tw <= 0 or th <= 0:
        return None

    font_size = max(float(textline.font_size), 1.0)
    pad_x = max(int(font_size * 1.4), int(tw * 0.5), 10)
    pad_y = max(int(font_size * 1.2), int(th * 0.35), 10)
    roi_x, roi_y, roi_w, roi_h = clamp_rect(
        tx - pad_x,
        ty - pad_y,
        tw + pad_x * 2,
        th + pad_y * 2,
        gray.shape[1],
        gray.shape[0],
    )
    roi = gray[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]
    if roi.size == 0:
        return None

    bright = cv2.inRange(roi, 220, 255)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8))
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))

    local_tx = tx - roi_x
    local_ty = ty - roi_y
    local_cx = local_tx + tw // 2
    local_cy = local_ty + th // 2
    line_area = max(tw * th, 1)
    roi_area = roi_w * roi_h

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(bright, 8, cv2.CV_32S)
    best_label = -1
    best_score = -1.0
    for label in range(1, num_labels):
        x1 = int(stats[label, cv2.CC_STAT_LEFT])
        y1 = int(stats[label, cv2.CC_STAT_TOP])
        w1 = int(stats[label, cv2.CC_STAT_WIDTH])
        h1 = int(stats[label, cv2.CC_STAT_HEIGHT])
        area1 = int(stats[label, cv2.CC_STAT_AREA])
        if area1 < max(24, int(line_area * 1.15)):
            continue
        if area1 > min(int(roi_area * 0.82), int(line_area * 28)):
            continue
        if w1 < max(6, int(tw * 0.75)) or h1 < max(6, int(th * 0.75)):
            continue

        touches_edges = sum([
            x1 <= 0,
            y1 <= 0,
            x1 + w1 >= roi_w - 1,
            y1 + h1 >= roi_h - 1,
        ])
        if touches_edges >= 2:
            continue

        center_inside = (
            0 <= local_cx < roi_w and
            0 <= local_cy < roi_h and
            labels[local_cy, local_cx] == label
        )
        overlap = _local_overlap_area(x1, y1, w1, h1, local_tx, local_ty, tw, th)
        if overlap <= 0 and not center_inside:
            continue

        component_mask = (labels == label)
        component_pixels = roi[component_mask]
        if component_pixels.size == 0:
            continue
        if float(component_pixels.mean()) < 229:
            continue
        if float(component_pixels.std()) > 22:
            continue

        score = overlap + (line_area if center_inside else 0) + area1 * 0.05
        if score > best_score:
            best_label = label
            best_score = score

    if best_label < 0:
        return None

    component_mask = np.zeros_like(gray, dtype=np.uint8)
    component_mask[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w][labels == best_label] = 255
    component_mask = cv2.morphologyEx(component_mask, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8))
    return component_mask

def _apply_box_cleanup(img: np.ndarray, final_mask: np.ndarray, textlines: List[Quadrilateral]):
    enhanced_mask = final_mask.copy()
    added_mask = np.zeros_like(final_mask)
    for textline in textlines:
        box_mask = _detect_box_cleanup_mask(img, textline)
        if box_mask is not None:
            enhanced_mask = cv2.bitwise_or(enhanced_mask, box_mask)
            added_mask = cv2.bitwise_or(added_mask, box_mask)
    return enhanced_mask, added_mask

def _cleanup_edge_residuals(img: np.ndarray, final_mask: np.ndarray):
    if img.ndim == 3 and img.shape[2] == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    else:
        gray = img

    ring = cv2.dilate(final_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    ring = cv2.bitwise_and(ring, cv2.bitwise_not(final_mask))
    dark_pixels = np.zeros_like(final_mask)
    dark_pixels[gray < 210] = 255
    residual = cv2.bitwise_and(dark_pixels, ring)
    residual = cv2.morphologyEx(residual, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(residual, 8, cv2.CV_32S)
    kept = np.zeros_like(final_mask)
    for label in range(1, num_labels):
        x1 = int(stats[label, cv2.CC_STAT_LEFT])
        y1 = int(stats[label, cv2.CC_STAT_TOP])
        w1 = int(stats[label, cv2.CC_STAT_WIDTH])
        h1 = int(stats[label, cv2.CC_STAT_HEIGHT])
        area1 = int(stats[label, cv2.CC_STAT_AREA])
        if area1 < 3 or area1 > 320:
            continue
        if max(w1, h1) > 48:
            continue
        kept[labels == label] = 255

    return cv2.bitwise_or(final_mask, kept), kept

def is_small_adjacent_component(textline: Quadrilateral, cc_rect: Tuple[int, int, int, int], cc_area: int,
                                poly_dist: float, overlap_ratio: float) -> bool:
    x1, y1, w1, h1 = cc_rect
    if cc_area <= 0 or w1 <= 0 or h1 <= 0:
        return False

    tx, ty, tw, th = textline.aabb.xywh
    if tw <= 0 or th <= 0:
        return False

    font_size = max(float(textline.font_size), 1.0)
    long_side = max(w1, h1)
    short_side = min(w1, h1)

    if cc_area > max(textline.area * 0.5, font_size * font_size):
        return False
    if long_side > font_size * 1.2 or short_side > font_size * 0.9:
        return False
    if poly_dist > max(2.0, font_size * 0.9) and overlap_ratio <= 0:
        return False

    x2 = x1 + w1
    y2 = y1 + h1
    tx2 = tx + tw
    ty2 = ty + th

    if textline.direction == 'h':
        parallel_overlap = interval_overlap(x1, x2, tx, tx2)
        perpendicular_gap = interval_gap(y1, y2, ty, ty2)
        required_overlap = max(1.0, min(w1, tw) * 0.12)
    else:
        parallel_overlap = interval_overlap(y1, y2, ty, ty2)
        perpendicular_gap = interval_gap(x1, x2, tx, tx2)
        required_overlap = max(1.0, min(h1, th) * 0.12)

    return parallel_overlap >= required_overlap and perpendicular_gap <= max(2.0, font_size * 0.75)

def complete_mask_fill(text_lines: List[Tuple[int, int, int, int]]):
    for (x, y, w, h) in text_lines:
        final_mask = cv2.rectangle(final_mask, (x, y), (x + w, y + h), (255), -1)
    return final_mask

# ----------------- PYDENSECRF REMOVED FOR WINDOWS COMPATIBILITY -----------------
# Fallback implementation of refine_mask that doesn't use pydensecrf
def refine_mask(rgbimg, rawmask):
    # Simple fallback: return the raw mask with basic morphological operations to smooth it
    if len(rawmask.shape) == 2:
        mask = rawmask.copy()
    else:
        mask = rawmask[:, :, 0].copy()

    # Basic smoothing using OpenCV as fallback for CRF
    kernel = np.ones((3,3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.GaussianBlur(mask, (5, 5), 0)
    _, crf_mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)

    return crf_mask
# --------------------------------------------------------------------------------

def complete_mask(img: np.ndarray, mask: np.ndarray, textlines: List[Quadrilateral], keep_threshold = 1e-2, dilation_offset = 0,kernel_size=3):
    input_mask = mask.copy()
    bboxes = [txtln.aabb.xywh for txtln in textlines]
    polys = [Polygon(txtln.pts) for txtln in textlines]
    for (x, y, w, h) in bboxes:
        cv2.rectangle(mask, (x, y), (x + w, y + h), (0), 1)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)

    M = len(textlines)
    textline_ccs = [np.zeros_like(mask) for _ in range(M)]
    iinfo = np.iinfo(labels.dtype)
    textline_rects = np.full(shape = (M, 4), fill_value = [iinfo.max, iinfo.max, iinfo.min, iinfo.min], dtype = labels.dtype)
    ratio_mat = np.zeros(shape = (num_labels, M), dtype = np.float32)
    dist_mat = np.zeros(shape = (num_labels, M), dtype = np.float32)
    valid = False
    for label in range(1, num_labels):
        # skip area too small
        if stats[label, cv2.CC_STAT_AREA] <= 9:
            continue

        x1 = stats[label, cv2.CC_STAT_LEFT]
        y1 = stats[label, cv2.CC_STAT_TOP]
        w1 = stats[label, cv2.CC_STAT_WIDTH]
        h1 = stats[label, cv2.CC_STAT_HEIGHT]
        area1 = stats[label, cv2.CC_STAT_AREA]
        cc_pts = np.array([[x1, y1], [x1 + w1, y1], [x1 + w1, y1 + h1], [x1, y1 + h1]])
        cc_poly = Polygon(cc_pts)

        for tl_idx in range(M):
            area2 = polys[tl_idx].area
            overlapping_area = polys[tl_idx].intersection(cc_poly).area
            ratio_mat[label, tl_idx] = overlapping_area / max(min(area1, area2), 1e-6)
            dist_mat[label, tl_idx] = polys[tl_idx].distance(cc_poly)

        avg = np.argmax(ratio_mat[label])
        area2 = polys[avg].area
        ruby_candidates = [
            tl_idx for tl_idx in range(M)
            if is_small_adjacent_component(
                textlines[tl_idx],
                (x1, y1, w1, h1),
                area1,
                dist_mat[label, tl_idx],
                ratio_mat[label, tl_idx],
            )
        ]
        if area1 >= area2:
            if avg not in ruby_candidates:
                continue
        if ratio_mat[label, avg] <= keep_threshold:
            if ruby_candidates:
                avg = min(ruby_candidates, key=lambda idx: dist_mat[label, idx])
                area2 = polys[avg].area
            else:
                avg = np.argmin(dist_mat[label])
                area2 = polys[avg].area
                unit = max(min([textlines[avg].font_size, w1, h1]), 10)
                if dist_mat[label, avg] >= 0.5 * unit:
                    continue
        elif ruby_candidates and avg not in ruby_candidates:
            avg = min(ruby_candidates, key=lambda idx: dist_mat[label, idx])
            area2 = polys[avg].area

        if area1 >= area2 and avg not in ruby_candidates:
            continue

        textline_ccs[avg][y1:y1+h1, x1:x1+w1][labels[y1:y1+h1, x1:x1+w1] == label] = 255
        textline_rects[avg, 0] = min(textline_rects[avg, 0], x1)
        textline_rects[avg, 1] = min(textline_rects[avg, 1], y1)
        textline_rects[avg, 2] = max(textline_rects[avg, 2], x1 + w1)
        textline_rects[avg, 3] = max(textline_rects[avg, 3], y1 + h1)
        valid = True

    if not valid:
        return None

    # tblr to xywh
    textline_rects[:, 2] -= textline_rects[:, 0]
    textline_rects[:, 3] -= textline_rects[:, 1]

    final_mask = np.zeros_like(mask)
    img = cv2.bilateralFilter(img, 17, 80, 80)
    for i, cc in enumerate(tqdm(textline_ccs, '[mask]')):
        x1, y1, w1, h1 = textline_rects[i]
        text_size = min(w1, h1, textlines[i].font_size)
        x1, y1, w1, h1 = extend_rect(x1, y1, w1, h1, img.shape[1], img.shape[0], int(text_size * 0.1))
        # TODO: Need to think of better way to determine dilate_size.
        dilate_size = max((int((text_size + dilation_offset) * 0.3) // 2) * 2 + 1, 3)
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_size, dilate_size))
        cc_region = np.ascontiguousarray(cc[y1: y1 + h1, x1: x1 + w1])
        if cc_region.size == 0:
            continue
        img_region = np.ascontiguousarray(img[y1: y1 + h1, x1: x1 + w1])
        cc_region = refine_mask(img_region, cc_region)
        cc[y1: y1 + h1, x1: x1 + w1] = cc_region
        x2, y2, w2, h2 = extend_rect(x1, y1, w1, h1, img.shape[1], img.shape[0], -(-dilate_size // 2))
        cc[y2:y2+h2, x2:x2+w2] = cv2.dilate(cc[y2:y2+h2, x2:x2+w2], kern)
        final_mask[y2:y2+h2, x2:x2+w2] = cv2.bitwise_or(final_mask[y2:y2+h2, x2:x2+w2], cc[y2:y2+h2, x2:x2+w2])
    core_mask = final_mask.copy()
    final_mask, box_cleanup_mask = _apply_box_cleanup(img, final_mask, textlines)
    final_mask, residual_mask = _cleanup_edge_residuals(img, final_mask)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    final_mask = cv2.dilate(final_mask, kern)
    _save_mask_debug_images(img, input_mask, core_mask, box_cleanup_mask, residual_mask, final_mask)
    return final_mask

def unsharp(image):
    gaussian_3 = cv2.GaussianBlur(image, (3, 3), 2.0)
    return cv2.addWeighted(image, 1.5, gaussian_3, -0.5, 0, image)
