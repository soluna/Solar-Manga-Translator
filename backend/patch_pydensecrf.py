import os
import sys
import shutil
from pathlib import Path

def patch_mask_refinement():
    # Detect if we're running from start.bat and find the correct path dynamically
    backend_dir = Path(__file__).parent
    translator_dir = backend_dir / "manga-image-translator"
    target_file = translator_dir / "manga_translator" / "mask_refinement" / "text_mask_utils.py"
    patched_file = backend_dir / "patched_text_mask_utils.py"
    target_render_file = translator_dir / "manga_translator" / "rendering" / "__init__.py"
    patched_render_file = backend_dir / "patched_rendering_init.py"

    if not target_file.exists():
        print(f"Error: Could not find {target_file}")
        return False

    if not patched_file.exists():
        print(f"Error: Could not find our patched version at {patched_file}")
        return False

    if not target_render_file.exists():
        print(f"Error: Could not find {target_render_file}")
        return False

    if not patched_render_file.exists():
        print(f"Error: Could not find our patched rendering version at {patched_render_file}")
        return False

    try:
        shutil.copy2(patched_file, target_file)
        print("Successfully replaced text_mask_utils.py with the patched version!")

        shutil.copy2(patched_render_file, target_render_file)
        print("Successfully replaced rendering/__init__.py with the patched layout version!")

        # We also need to patch translators/keys.py to default to Gemini 3.1 Pro Preview
        keys_file = translator_dir / "manga_translator" / "translators" / "keys.py"
        if keys_file.exists():
            with open(keys_file, 'r', encoding='utf-8') as f:
                keys_content = f.read()

            import re
            # Change the default model (using regex to catch any current default)
            keys_content = re.sub(r"GEMINI_MODEL\s*=\s*os\.getenv\('GEMINI_MODEL',\s*'[^']+'\)", "GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.1-pro-preview')", keys_content)

            with open(keys_file, 'w', encoding='utf-8') as f:
                f.write(keys_content)
            print("Successfully patched Gemini default model to gemini-3.1-pro-preview!")

        return True
    except Exception as e:
        print(f"Failed to overwrite file: {e}")
        return False

if __name__ == "__main__":
    patch_mask_refinement()
