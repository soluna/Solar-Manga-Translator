import os
import sys
from pathlib import Path

def patch_mask_refinement():
    # Detect if we're running from start.bat and find the correct path dynamically
    backend_dir = Path(__file__).parent
    target_file = backend_dir / "manga-image-translator" / "manga_translator" / "mask_refinement" / "text_mask_utils.py"

    if not target_file.exists():
        print(f"Error: Could not find {target_file}")
        return False

    with open(target_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check if already patched
    if 'import pydensecrf' not in content and 'pydensecrf.utils' not in content:
        print("Already patched! No pydensecrf imports found.")
        return True

    # Simple text replacement instead of regex to be safer
    old_imports = """from pydensecrf.utils import compute_unary, unary_from_softmax
import pydensecrf.densecrf as dcrf"""

    if old_imports in content:
        print("Found pydensecrf imports, removing them...")
        content = content.replace(old_imports, "# Removed pydensecrf imports for Windows compatibility")

    # Find the refine_mask function and replace it
    import re

    # We use regex to find and replace the whole refine_mask function since it might vary slightly
    pattern = r"def refine_mask\(rgbimg, rawmask\):.*?return crf_mask"

    fallback_code = """def refine_mask(rgbimg, rawmask):
    import cv2
    import numpy as np

    # Fallback implementation of refine_mask that doesn't use pydensecrf
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

    return crf_mask"""

    new_content = re.sub(pattern, fallback_code, content, flags=re.DOTALL)

    if new_content != content:
        with open(target_file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print("Successfully patched text_mask_utils.py to bypass pydensecrf dependency!")
        return True
    else:
        print("Could not find refine_mask function to patch. Proceeding anyway.")
        return False

if __name__ == "__main__":
    patch_mask_refinement()
