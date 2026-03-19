import asyncio
import os
import subprocess

class TranslatorEngine:
    def __init__(self):
        # 如果使用库调用的方式，可以在这里初始化模型。
        # 这里我们使用最简单的命令行子进程调用方式，这样可以最大程度兼容隔离的 CUDA 环境。
        pass

    async def translate_image(self, image_path: str, output_path: str, config: dict):
        """
        调用核心引擎翻译单图
        使用 asyncio.create_subprocess_exec 来非阻塞执行 manga_translator
        """
        target_lang = config.get("target_lang", "CHS")
        translator = config.get("translator", "google")

        # 强制添加 --use-cuda 参数调用 GPU
        cmd = [
            "manga_translator",
            "-i", image_path,
            "-o", output_path,
            "--target-lang", target_lang,
            "--translator", translator,
            "--use-cuda"
        ]

        # 为了避免因为某些特殊的报错卡死，我们捕获它的输出
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            raise RuntimeError(f"Translation failed (exit {process.returncode}): {error_msg}")

        return output_path
