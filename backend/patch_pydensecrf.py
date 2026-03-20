import os
import sys
import shutil
from pathlib import Path

def patch_mask_refinement():
    # Detect if we're running from start.bat and find the correct path dynamically
    backend_dir = Path(__file__).parent
    target_file = backend_dir / "manga-image-translator" / "manga_translator" / "mask_refinement" / "text_mask_utils.py"
    patched_file = backend_dir / "patched_text_mask_utils.py"

    if not target_file.exists():
        print(f"Error: Could not find {target_file}")
        return False

    if not patched_file.exists():
        print(f"Error: Could not find our patched version at {patched_file}")
        return False

    with open(target_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check if already patched
    if 'import pydensecrf' not in content and 'pydensecrf.utils' not in content:
        print("Already patched! No pydensecrf imports found.")
        return True

    try:
        # Instead of regex, we just overwrite the whole file with our pre-patched version
        shutil.copy2(patched_file, target_file)
        print("Successfully replaced text_mask_utils.py with the patched Windows-compatible version!")
        return True
    except Exception as e:
        print(f"Failed to overwrite file: {e}")
        return False

if __name__ == "__main__":
    patch_mask_refinement()
