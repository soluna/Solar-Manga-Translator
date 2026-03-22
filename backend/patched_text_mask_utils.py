from typing import Tuple, List
import numpy as np
import cv2
import math

from tqdm import tqdm
from shapely.geometry import Polygon

from ..utils import Quadrilateral, image_resize

COLOR_RANGE_SIGMA = 1.5 # how many stddev away is considered the same color

def save_rgb(fn, img):
    if len(img.shape) == 3 and img.shape[2] == 3:
        cv2.imwrite(fn, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    else:
        cv2.imwrite(fn, img)

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
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    return cv2.dilate(final_mask, kern)

def unsharp(image):
    gaussian_3 = cv2.GaussianBlur(image, (3, 3), 2.0)
    return cv2.addWeighted(image, 1.5, gaussian_3, -0.5, 0, image)
