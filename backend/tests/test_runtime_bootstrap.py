from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from runtime_bootstrap import (
    NvidiaGpu,
    _installed_runtime_matches,
    build_gpu_diagnostics,
    choose_pytorch_runtime,
    install_pytorch_runtime,
)


class RuntimeBootstrapTests(unittest.TestCase):
    def test_windows_blackwell_gpu_uses_cuda_runtime(self) -> None:
        plan = choose_pytorch_runtime(
            platform_name="win32",
            nvidia_gpus=[
                NvidiaGpu(
                    name="NVIDIA GeForce RTX 5060 Ti",
                    driver_version="580.88",
                    compute_capability="12.0",
                )
            ],
        )

        self.assertEqual(plan.accelerator, "cuda")
        self.assertEqual(plan.index_url, "https://download.pytorch.org/whl/cu130")
        self.assertEqual(
            plan.packages,
            ("torch==2.12.1", "torchvision==0.27.1"),
        )

    def test_windows_without_nvidia_gpu_uses_cpu_runtime(self) -> None:
        plan = choose_pytorch_runtime(platform_name="win32", nvidia_gpus=[])

        self.assertEqual(plan.accelerator, "cpu")
        self.assertEqual(plan.index_url, "https://download.pytorch.org/whl/cpu")

    def test_windows_cuda_install_uses_direct_official_wheels(self) -> None:
        gpu = NvidiaGpu(
            name="NVIDIA GeForce RTX 5060 Ti",
            driver_version="580.88",
            compute_capability="12.0",
        )
        plan = choose_pytorch_runtime(
            platform_name="win32",
            nvidia_gpus=[gpu],
        )

        for python_tag in ("cp310", "cp311"):
            with (
                self.subTest(python_tag=python_tag),
                mock.patch("runtime_bootstrap.detect_nvidia_gpus", return_value=[gpu]),
                mock.patch("runtime_bootstrap._installed_runtime_matches", return_value=False),
                mock.patch("runtime_bootstrap.subprocess.run") as run,
            ):
                install_pytorch_runtime(
                    plan,
                    platform_name="win32",
                    python_tag=python_tag,
                    machine="AMD64",
                )

                command = run.call_args.args[0]
                self.assertIn(
                    "https://download-r2.pytorch.org/whl/cu130/"
                    f"torch-2.12.1%2Bcu130-{python_tag}-{python_tag}-win_amd64.whl",
                    command,
                )
                self.assertIn(
                    "https://download-r2.pytorch.org/whl/cu130/"
                    f"torchvision-0.27.1%2Bcu130-{python_tag}-{python_tag}-win_amd64.whl",
                    command,
                )
                self.assertNotIn("--index-url", command)

    def test_blackwell_plan_rejects_same_torch_version_with_wrong_cuda_build(self) -> None:
        plan = choose_pytorch_runtime(
            platform_name="win32",
            nvidia_gpus=[
                NvidiaGpu(
                    name="NVIDIA GeForce RTX 5060 Ti",
                    driver_version="580.88",
                    compute_capability="12.0",
                )
            ],
        )
        fake_torch = SimpleNamespace(
            __version__="2.12.1+cu126",
            version=SimpleNamespace(cuda="12.6"),
        )
        fake_torchvision = SimpleNamespace(__version__="0.27.1+cu126")

        with mock.patch.dict(
            sys.modules,
            {"torch": fake_torch, "torchvision": fake_torchvision},
        ):
            self.assertFalse(_installed_runtime_matches(plan))

    def test_diagnostics_explain_cpu_torch_build_on_nvidia_machine(self) -> None:
        class FakeCuda:
            @staticmethod
            def is_available() -> bool:
                return False

            @staticmethod
            def device_count() -> int:
                return 0

        class FakeTorch:
            __version__ = "2.12.1+cpu"
            version = type("Version", (), {"cuda": None})()
            cuda = FakeCuda()

        diagnostics = build_gpu_diagnostics(
            FakeTorch(),
            [
                NvidiaGpu(
                    name="NVIDIA GeForce RTX 5060 Ti",
                    driver_version="580.88",
                    compute_capability="12.0",
                )
            ],
        )

        self.assertFalse(diagnostics["available"])
        self.assertEqual(diagnostics["status"], "torch_cpu_build")
        self.assertIn("CPU", diagnostics["message"])
        self.assertEqual(diagnostics["nvidia_devices"][0]["name"], "NVIDIA GeForce RTX 5060 Ti")

    def test_diagnostics_reject_cuda_build_without_blackwell_architecture(self) -> None:
        class FakeCuda:
            @staticmethod
            def is_available() -> bool:
                return True

            @staticmethod
            def device_count() -> int:
                return 1

            @staticmethod
            def get_device_name(_index: int) -> str:
                return "NVIDIA GeForce RTX 5060 Ti"

            @staticmethod
            def get_device_capability(_index: int) -> tuple[int, int]:
                return (12, 0)

            @staticmethod
            def get_arch_list() -> list[str]:
                return ["sm_80", "sm_86", "sm_90"]

        class FakeTorch:
            __version__ = "2.12.1+cu126"
            version = type("Version", (), {"cuda": "12.6"})()
            cuda = FakeCuda()

        diagnostics = build_gpu_diagnostics(
            FakeTorch(),
            [
                NvidiaGpu(
                    name="NVIDIA GeForce RTX 5060 Ti",
                    driver_version="580.88",
                    compute_capability="12.0",
                )
            ],
        )

        self.assertFalse(diagnostics["available"])
        self.assertEqual(diagnostics["status"], "unsupported_gpu_architecture")
        self.assertIn("sm_120", diagnostics["message"])

    def test_windows_start_script_uses_runtime_bootstrap(self) -> None:
        start_script = (BACKEND_DIR.parent / "start.bat").read_text(encoding="utf-8")

        self.assertIn("runtime_bootstrap.py", start_script)
        self.assertNotIn("-m pip install --upgrade \"torch", start_script)

    def test_windows_start_script_writes_locale_independent_timestamp(self) -> None:
        start_script = (BACKEND_DIR.parent / "start.bat").read_text(encoding="utf-8")

        self.assertNotIn("%date%", start_script.lower())
        self.assertIn("Get-Date -Format 'yyyy-MM-dd HH:mm:ss'", start_script)


if __name__ == "__main__":
    unittest.main()
