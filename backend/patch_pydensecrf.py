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
        "import re\n",
        "import os\nimport re\n",
        "Gemini project glossary environment import",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "NRML='\\033[0m' # Revert to Normal formatting\n",
        "NRML='\\033[0m' # Revert to Normal formatting\n\n"
        "def _project_glossary_instruction() -> str:\n"
        "    glossary_text = str(os.getenv(\"MT_PROJECT_GLOSSARY_TEXT\", \"\") or \"\").strip()\n"
        "    return f\"\\n\\n{glossary_text}\" if glossary_text else \"\"\n",
        "Gemini project glossary instruction helper",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "        sysTemplate=self.chat_system_template.format(to_lang=to_lang)\n",
        "        sysTemplate=self.chat_system_template.format(to_lang=to_lang) + _project_glossary_instruction()\n",
        "Gemini cached system glossary injection",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "            config_kwargs['system_instruction'] = self.chat_system_template.format(to_lang=to_lang)\n",
        "            config_kwargs['system_instruction'] = self.chat_system_template.format(to_lang=to_lang) + _project_glossary_instruction()\n",
        "Gemini system glossary injection",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "        sysTemplate=self.translator.chat_system_template.format(to_lang=to_lang)\n",
        "        sysTemplate=self.translator.chat_system_template.format(to_lang=to_lang) + _project_glossary_instruction()\n",
        "Gemini JSON cached system glossary injection",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "            config_kwargs['system_instruction'] = self.translator.chat_system_template.format(to_lang=to_lang)\n",
        "            config_kwargs['system_instruction'] = self.translator.chat_system_template.format(to_lang=to_lang) + _project_glossary_instruction()\n",
        "Gemini JSON system glossary injection",
    )
    changed = changed or did_change

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

def patch_chatgpt_translator(target_file: Path) -> bool:
    content = target_file.read_text(encoding='utf-8')
    updated = content
    changed = False

    updated, did_change = _replace_once(
        updated,
        "from .. import manga_translator\n",
        "\ndef _get_manga_translator_runtime():\n    from importlib import import_module\n    return import_module(\"manga_translator.manga_translator\")\n",
        "ChatGPT translator lazy manga_translator runtime import",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "        if hasattr(manga_translator, '_global_console') and manga_translator._global_console:\n            self.console = manga_translator._global_console\n        else:\n            self.console = Console()  \n",
        "        manga_translator_runtime = _get_manga_translator_runtime()\n        global_console = getattr(manga_translator_runtime, '_global_console', None)\n        if global_console:\n            self.console = global_console\n        else:\n            self.console = Console()  \n",
        "ChatGPT translator lazy global console lookup",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "        if hasattr(manga_translator, '_log_console') and manga_translator._log_console:\n            # 直接输出纯文本，不使用边框\n            manga_translator._log_console.print(f\"=== {title} ===\")\n            manga_translator._log_console.print(fixed_text)\n            manga_translator._log_console.print(\"=\" * (len(title) + 8))\n",
        "        manga_translator_runtime = _get_manga_translator_runtime()\n        log_console = getattr(manga_translator_runtime, '_log_console', None)\n        if log_console:\n            # 直接输出纯文本，不使用边框\n            log_console.print(f\"=== {title} ===\")\n            log_console.print(fixed_text)\n            log_console.print(\"=\" * (len(title) + 8))\n",
        "ChatGPT translator lazy log console lookup",
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

def patch_custom_openai_translator(target_file: Path, patched_file: Path) -> bool:
    if not patched_file.exists():
        raise RuntimeError(f"Could not find patched custom openai translator at {patched_file}")
    shutil.copy2(patched_file, target_file)
    return True

def patch_sakura_translator(target_file: Path) -> bool:
    content = target_file.read_text(encoding='utf-8')
    updated, changed = _replace_once(
        content,
        '        self.client.api_key = "sk-114514"\n',
        '        self.client.api_key = openai.api_key or "empty"\n',
        "Sakura translator dummy API key placeholder",
    )
    if changed:
        target_file.write_text(updated, encoding='utf-8')
    return changed

def patch_text_render(target_file: Path) -> bool:
    content = target_file.read_text(encoding='utf-8')
    updated = content
    changed = False

    updated, did_change = _replace_once(
        updated,
        "from ..utils import BASE_PATH, is_punctuation\n",
        "from ..utils.generic import BASE_PATH\nfrom ..utils.generic2 import is_punctuation\n",
        "text_render lightweight renderer imports",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "def put_text_vertical(font_size: int, text: str, h: int, alignment: str, fg: Tuple[int, int, int], bg: Optional[Tuple[int, int, int]], line_spacing: int):",
        "def put_text_vertical(font_size: int, text: str, h: int, alignment: str, fg: Tuple[int, int, int], bg: Optional[Tuple[int, int, int]], line_spacing: int, letter_spacing: float = 1.0):",
        "text_render vertical signature",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "    spacing_x = int(font_size * (line_spacing or 0.2))\n",
        "    spacing_x = int(font_size * (line_spacing or 0.2))\n    letter_spacing = max(float(letter_spacing or 1.0), 0.85)\n",
        "text_render vertical letter spacing init",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "    if not text :\n        return\n    bg_size = int(max(font_size * 0.07, 1)) if bg is not None else 0\n",
        "    if not text :\n        return\n    font_size = max(int(font_size or 0), 1)\n    h = max(int(h or 0), font_size)\n    bg_size = int(max(font_size * 0.07, 1)) if bg is not None else 0\n",
        "text_render vertical dimension guards",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "    if not text :\n        return\n    font_size = max(int(font_size or 0), 1)\n    width = max(int(width or 0), font_size)\n    height = max(int(height or 0), font_size)\n    bg_size = int(max(font_size * 0.07, 1)) if bg is not None else 0\n",
        "    if not text :\n        return\n    font_size = max(int(font_size or 0), 1)\n    h = max(int(h or 0), font_size)\n    bg_size = int(max(font_size * 0.07, 1)) if bg is not None else 0\n",
        "text_render vertical wrong width/height guard cleanup",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "    num_char_y = h // font_size\n",
        "    num_char_y = max(h // font_size, 1)\n",
        "text_render vertical num_char_y guard",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "    canvas_y = font_size * num_char_y + (font_size + bg_size) * 2\n",
        "    canvas_y = int(font_size * num_char_y * max(letter_spacing, 1.0)) + (font_size + bg_size) * 2\n",
        "text_render vertical canvas height",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "            pen_line[1] += offset_y\n",
        "            pen_line[1] += max(1, int(round(offset_y * letter_spacing)))\n",
        "text_render vertical glyph advance",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "        if alignment == 'center':\n            pen_line[1] += (max(line_height_list) - line_height) // 2\n        elif alignment == 'right':\n            pen_line[1] += max(line_height_list) - line_height\n\n",
        "        # Vertical columns stay top-aligned; horizontal placement is handled by the outer canvas.\n",
        "text_render vertical column top alignment",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "    max_width = max(max_width, 2 * font_size)\n",
        "    font_size = max(int(font_size or 0), 1)\n    max_height = max(int(max_height or 0), font_size)\n    max_width = max(max_width, 2 * font_size)\n",
        "text_render horizontal calc guards",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "def put_text_horizontal(font_size: int, text: str, width: int, height: int, alignment: str,\n                        reversed_direction: bool, fg: Tuple[int, int, int], bg: Tuple[int, int, int],\n                        lang: str = 'en_US', hyphenate: bool = True, line_spacing: int = 0):",
        "def put_text_horizontal(font_size: int, text: str, width: int, height: int, alignment: str,\n                        reversed_direction: bool, fg: Tuple[int, int, int], bg: Tuple[int, int, int],\n                        lang: str = 'en_US', hyphenate: bool = True, line_spacing: int = 0, letter_spacing: float = 1.0):",
        "text_render horizontal signature",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "    if not text :\n        return\n    bg_size = int(max(font_size * 0.07, 1)) if bg is not None else 0\n",
        "    if not text :\n        return\n    font_size = max(int(font_size or 0), 1)\n    width = max(int(width or 0), font_size)\n    height = max(int(height or 0), font_size)\n    bg_size = int(max(font_size * 0.07, 1)) if bg is not None else 0\n",
        "text_render horizontal dimension guards",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "    if not text :\n        return\n    font_size = max(int(font_size or 0), 1)\n    h = max(int(h or 0), font_size)\n    bg_size = int(max(font_size * 0.07, 1)) if bg is not None else 0\n",
        "    if not text :\n        return\n    font_size = max(int(font_size or 0), 1)\n    width = max(int(width or 0), font_size)\n    height = max(int(height or 0), font_size)\n    bg_size = int(max(font_size * 0.07, 1)) if bg is not None else 0\n",
        "text_render horizontal wrong h guard cleanup",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "    spacing_y = int(font_size * (line_spacing or 0.01))\n",
        "    spacing_y = int(font_size * (line_spacing or 0.01))\n    letter_spacing = max(float(letter_spacing or 1.0), 0.85)\n",
        "text_render horizontal letter spacing init",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "    canvas_w = max(line_width_list) + (font_size + bg_size) * 2\n",
        "    canvas_w = int(max(line_width_list) * max(letter_spacing, 1.0)) + (font_size + bg_size) * 2\n",
        "text_render horizontal canvas width",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "                offset_x = glyph.metrics.horiAdvance >> 6\n                pen_line[0] -= offset_x\n",
        "                offset_x = glyph.metrics.horiAdvance >> 6\n                pen_line[0] -= max(1, int(round(offset_x * letter_spacing)))\n",
        "text_render horizontal reverse advance",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "                pen_line[0] += offset_x\n",
        "                pen_line[0] += max(1, int(round(offset_x * letter_spacing)))\n",
        "text_render horizontal glyph advance",
    )
    changed = changed or did_change

    if changed:
        target_file.write_text(updated, encoding='utf-8')

    return changed

def patch_text_render_eng(target_file: Path) -> bool:
    content = target_file.read_text(encoding='utf-8')
    updated = content
    changed = False

    updated, did_change = _replace_once(
        updated,
        "from ..utils import TextBlock, rect_distance\n",
        "from ..utils.generic2 import rect_distance\nfrom ..utils.textblock import TextBlock\n",
        "text_render_eng lightweight renderer imports",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "        font_size = int(font_size)\n",
        "        font_size = max(int(font_size or 0), 1)\n",
        "text_render_eng font size guard",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "        line_height = int(font_size * 0.8)\n",
        "        line_height = max(int(font_size * 0.8), 1)\n",
        "text_render_eng line height guard",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "        lines_available = abs(xyxy[3] - xyxy[1]) // line_height + 1\n",
        "        lines_available = max(1, abs(xyxy[3] - xyxy[1]) // max(line_height, 1) + 1)\n",
        "text_render_eng lines available guard",
    )
    changed = changed or did_change

    if changed:
        target_file.write_text(updated, encoding='utf-8')

    return changed

def patch_text_render_pillow_eng(target_file: Path) -> bool:
    content = target_file.read_text(encoding='utf-8')
    updated = content
    changed = False

    updated, did_change = _replace_once(
        updated,
        "from ..utils import TextBlock\n",
        "from ..utils.textblock import TextBlock\n",
        "text_render_pillow_eng lightweight renderer imports",
    )
    changed = changed or did_change

    updated, did_change = _replace_once(
        updated,
        "        line_height = font.getmetrics()[0] - font.getmetrics()[1]\n",
        "        line_height = max(font.getmetrics()[0] - font.getmetrics()[1], 1)\n",
        "text_render_pillow_eng line height guard",
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
    target_package_init_file = translator_dir / "manga_translator" / "__init__.py"
    patched_package_init_file = backend_dir / "patched_manga_translator_init.py"
    target_utils_init_file = translator_dir / "manga_translator" / "utils" / "__init__.py"
    patched_utils_init_file = backend_dir / "patched_utils_init.py"
    target_inpainting_init_file = translator_dir / "manga_translator" / "inpainting" / "__init__.py"
    patched_inpainting_init_file = backend_dir / "patched_inpainting_init.py"
    target_render_file = translator_dir / "manga_translator" / "rendering" / "__init__.py"
    patched_render_file = backend_dir / "patched_rendering_init.py"
    patched_text_render_file = backend_dir / "patched_text_render.py"
    target_text_render_file = translator_dir / "manga_translator" / "rendering" / "text_render.py"
    target_text_render_eng_file = translator_dir / "manga_translator" / "rendering" / "text_render_eng.py"
    target_text_render_pillow_eng_file = translator_dir / "manga_translator" / "rendering" / "text_render_pillow_eng.py"
    target_gemini_file = translator_dir / "manga_translator" / "translators" / "gemini.py"
    target_chatgpt_file = translator_dir / "manga_translator" / "translators" / "chatgpt.py"
    target_custom_openai_file = translator_dir / "manga_translator" / "translators" / "custom_openai.py"
    target_sakura_file = translator_dir / "manga_translator" / "translators" / "sakura.py"
    target_local_file = translator_dir / "manga_translator" / "mode" / "local.py"
    patched_rerender_cache_file = backend_dir / "patched_rerender_cache.py"
    target_rerender_cache_file = translator_dir / "manga_translator" / "utils" / "rerender_cache.py"
    patched_custom_openai_file = backend_dir / "patched_custom_openai.py"

    if not target_file.exists():
        print(f"Error: Could not find {target_file}")
        return False

    if not patched_file.exists():
        print(f"Error: Could not find our patched version at {patched_file}")
        return False

    if not target_package_init_file.exists():
        print(f"Error: Could not find {target_package_init_file}")
        return False

    if not patched_package_init_file.exists():
        print(f"Error: Could not find our patched package init at {patched_package_init_file}")
        return False

    if not target_utils_init_file.exists():
        print(f"Error: Could not find {target_utils_init_file}")
        return False

    if not patched_utils_init_file.exists():
        print(f"Error: Could not find our patched utils init at {patched_utils_init_file}")
        return False

    if not target_inpainting_init_file.exists():
        print(f"Error: Could not find {target_inpainting_init_file}")
        return False

    if not patched_inpainting_init_file.exists():
        print(f"Error: Could not find our patched inpainting init at {patched_inpainting_init_file}")
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

    if not target_chatgpt_file.exists():
        print(f"Error: Could not find {target_chatgpt_file}")
        return False

    if not target_text_render_eng_file.exists():
        print(f"Error: Could not find {target_text_render_eng_file}")
        return False

    if not target_text_render_pillow_eng_file.exists():
        print(f"Error: Could not find {target_text_render_pillow_eng_file}")
        return False

    if not target_local_file.exists():
        print(f"Error: Could not find {target_local_file}")
        return False

    if not patched_rerender_cache_file.exists():
        print(f"Error: Could not find {patched_rerender_cache_file}")
        return False

    if not target_custom_openai_file.exists():
        print(f"Error: Could not find {target_custom_openai_file}")
        return False

    if not patched_custom_openai_file.exists():
        print(f"Error: Could not find {patched_custom_openai_file}")
        return False

    if not target_sakura_file.exists():
        print(f"Error: Could not find {target_sakura_file}")
        return False

    try:
        if patched_text_render_file.exists():
            shutil.copy2(patched_text_render_file, target_text_render_file)
            print("Successfully replaced text_render.py with the patched semantic wrapper version!")
        else:
            patch_text_render(target_text_render_file)
            print("Successfully patched text_render.py for configurable letter spacing!")

        shutil.copy2(patched_package_init_file, target_package_init_file)
        print("Successfully replaced manga_translator/__init__.py with the lazy package init!")

        shutil.copy2(patched_utils_init_file, target_utils_init_file)
        print("Successfully replaced utils/__init__.py with the lazy inference init!")

        shutil.copy2(patched_inpainting_init_file, target_inpainting_init_file)
        print("Successfully replaced inpainting/__init__.py with the lazy inpainter init!")

        patch_text_render_eng(target_text_render_eng_file)
        print("Successfully patched text_render_eng.py with rendering guards!")

        patch_text_render_pillow_eng(target_text_render_pillow_eng_file)
        print("Successfully patched text_render_pillow_eng.py with rendering guards!")

        shutil.copy2(patched_file, target_file)
        print("Successfully replaced text_mask_utils.py with the patched version!")

        shutil.copy2(patched_render_file, target_render_file)
        print("Successfully replaced rendering/__init__.py with the patched layout version!")

        shutil.copy2(patched_rerender_cache_file, target_rerender_cache_file)
        print("Successfully added rerender cache helper module!")

        patch_gemini_translator(target_gemini_file)
        print("Successfully patched Gemini translator for empty-response handling!")

        patch_chatgpt_translator(target_chatgpt_file)
        print("Successfully patched ChatGPT translator for lazy runtime lookup!")

        patch_local_mode(target_local_file)
        print("Successfully patched local mode for rerender cache generation!")

        patch_custom_openai_translator(target_custom_openai_file, patched_custom_openai_file)
        print("Successfully replaced custom_openai.py with the patched Responses-compatible version!")

        patch_sakura_translator(target_sakura_file)
        print("Successfully patched Sakura translator dummy API key placeholder!")

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
