import os
import sys
import shutil
from pathlib import Path

def _replace_once(content: str, old: str, new: str, description: str) -> tuple[str, bool]:
    if new in content:
        return content, False
    if old not in content:
        raise RuntimeError(f"Could not patch {description}: expected snippet not found.")
    return content.replace(old, new, 1), True

def patch_gemini_translator(target_file: Path) -> bool:
    content = target_file.read_text(encoding='utf-8')
    updated = content
    changed = False

    updated, did_change = _replace_once(
        updated,
        "        RETRY_ATTEMPTS = self._RETRY_ATTEMPTS  \n",
        "        RETRY_ATTEMPTS = self._RETRY_ATTEMPTS  \n        server_error_attempt = 0\n",
        "Gemini server error retry counter",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "            self.logger.debug(f'-- GPT Response --\\n' + response.text)\n\n            return response.text",
        "            response_text = getattr(response, 'text', None)\n            if response_text is None:\n                self.logger.warning('Gemini returned an empty response text; retrying this batch.')\n                raise InvalidServerResponse('Gemini returned an empty response text.')\n\n            self.logger.debug('-- GPT Response --\\n' + response_text)\n\n            return response_text",
        "Gemini plain-text response handling",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "            self.logger.debug(  '-- GPT Response --\\n' + \n                                self.ppJSON(response.text) + \n                                '\\n------------\\n'\n                            )\n\n            return response.text",
        "            response_text = getattr(response, 'text', None)\n            if response_text is None:\n                self.logger.warning('Gemini returned an empty JSON response text; retrying this batch.')\n                raise InvalidServerResponse('Gemini returned an empty JSON response text.')\n\n            self.logger.debug(  '-- GPT Response --\\n' + \n                                self.ppJSON(response_text) + \n                                '\\n------------\\n'\n                            )\n\n            return response_text",
        "Gemini JSON response handling",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "                    if attempt == RETRY_ATTEMPTS - 1:  \n                        raise  \n",
        "                    if attempt == RETRY_ATTEMPTS - 1:  \n                        self.logger.warning('Retry limit reached for the current Gemini batch; falling back to batch splitting.')\n                        break\n",
        "Gemini retry-to-split fallback",
    )
    changed = changed or did_change

    if changed:
        target_file.write_text(updated, encoding='utf-8')

    return changed

def patch_local_mode(target_file: Path) -> bool:
    content = target_file.read_text(encoding='utf-8')
    updated = content
    changed = False

    updated, did_change = _replace_once(
        updated,
        "from ..utils import natural_sort, replace_prefix, get_color_name, rgb2hex, get_logger\n",
        "from ..utils import natural_sort, replace_prefix, get_color_name, rgb2hex, get_logger\nfrom ..utils.rerender_cache import save_rerender_cache\n",
        "local mode rerender cache import",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "            ctx = await self.translate(img, config)\n            result = ctx.result\n",
        "            ctx = await self.translate(img, config)\n            save_rerender_cache(path, ctx)\n            result = ctx.result\n",
        "local mode rerender cache save hook",
    )
    changed = changed or did_change

    if changed:
        target_file.write_text(updated, encoding='utf-8')

    return changed

def patch_mask_refinement():
    # Detect if we're running from start.bat and find the correct path dynamically
    backend_dir = Path(__file__).parent
    translator_dir = backend_dir / "manga-image-translator"
    target_file = translator_dir / "manga_translator" / "mask_refinement" / "text_mask_utils.py"
    patched_file = backend_dir / "patched_text_mask_utils.py"
    target_render_file = translator_dir / "manga_translator" / "rendering" / "__init__.py"
    patched_render_file = backend_dir / "patched_rendering_init.py"
    patched_text_render_file = backend_dir / "patched_text_render.py"
    target_text_render_file = translator_dir / "manga_translator" / "rendering" / "text_render.py"
    target_gemini_file = translator_dir / "manga_translator" / "translators" / "gemini.py"
    target_local_file = translator_dir / "manga_translator" / "mode" / "local.py"
    patched_rerender_cache_file = backend_dir / "patched_rerender_cache.py"
    target_rerender_cache_file = translator_dir / "manga_translator" / "utils" / "rerender_cache.py"

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

    if not target_gemini_file.exists():
        print(f"Error: Could not find {target_gemini_file}")
        return False

    if not target_local_file.exists():
        print(f"Error: Could not find {target_local_file}")
        return False

    if not patched_rerender_cache_file.exists():
        print(f"Error: Could not find {patched_rerender_cache_file}")
        return False

    try:
        if patched_text_render_file.exists():
            shutil.copy2(patched_text_render_file, target_text_render_file)
            print("Successfully replaced text_render.py with the patched semantic wrapper version!")
        else:
            print(f"Warning: Could not find patched_text_render.py at {patched_text_render_file}, skipping text_render patch.")

        shutil.copy2(patched_file, target_file)
        print("Successfully replaced text_mask_utils.py with the patched version!")

        shutil.copy2(patched_render_file, target_render_file)
        print("Successfully replaced rendering/__init__.py with the patched layout version!")

        shutil.copy2(patched_rerender_cache_file, target_rerender_cache_file)
        print("Successfully added rerender cache helper module!")

        patch_gemini_translator(target_gemini_file)
        print("Successfully patched Gemini translator for empty-response handling!")

        patch_local_mode(target_local_file)
        print("Successfully patched local mode for rerender cache generation!")

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
