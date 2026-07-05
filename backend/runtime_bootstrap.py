from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any, Sequence


PYTORCH_VERSION = "2.12.1"
TORCHVISION_VERSION = "0.27.1"


@dataclass(frozen=True, slots=True)
class NvidiaGpu:
    name: str
    driver_version: str = ""
    compute_capability: str = ""


@dataclass(frozen=True, slots=True)
class PytorchRuntimePlan:
    accelerator: str
    index_url: str
    packages: tuple[str, ...]
    reason: str


def _run_nvidia_query(fields: str) -> list[str]:
    completed = subprocess.run(
        [
            "nvidia-smi",
            f"--query-gpu={fields}",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=8,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def detect_nvidia_gpus() -> list[NvidiaGpu]:
    try:
        rows = _run_nvidia_query("name,driver_version,compute_cap")
        result = []
        for row in rows:
            parts = [part.strip() for part in row.split(",", 2)]
            result.append(NvidiaGpu(
                name=parts[0] if parts else "NVIDIA GPU",
                driver_version=parts[1] if len(parts) > 1 else "",
                compute_capability=parts[2] if len(parts) > 2 else "",
            ))
        return result
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass

    try:
        rows = _run_nvidia_query("name,driver_version")
        result = []
        for row in rows:
            parts = [part.strip() for part in row.split(",", 1)]
            result.append(NvidiaGpu(
                name=parts[0] if parts else "NVIDIA GPU",
                driver_version=parts[1] if len(parts) > 1 else "",
            ))
        return result
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []


def _compute_capability(gpus: Sequence[NvidiaGpu]) -> float | None:
    capabilities = []
    for gpu in gpus:
        try:
            capabilities.append(float(gpu.compute_capability))
        except (TypeError, ValueError):
            continue
    return max(capabilities) if capabilities else None


def _driver_major(gpus: Sequence[NvidiaGpu]) -> int | None:
    versions = []
    for gpu in gpus:
        try:
            versions.append(int(str(gpu.driver_version).split(".", 1)[0]))
        except (TypeError, ValueError):
            continue
    return min(versions) if versions else None


def choose_pytorch_runtime(
    *,
    platform_name: str = sys.platform,
    nvidia_gpus: Sequence[NvidiaGpu] | None = None,
) -> PytorchRuntimePlan:
    gpus = list(nvidia_gpus if nvidia_gpus is not None else detect_nvidia_gpus())
    packages = (
        f"torch=={PYTORCH_VERSION}",
        f"torchvision=={TORCHVISION_VERSION}",
    )
    if platform_name.startswith(("win", "linux")) and gpus:
        capability = _compute_capability(gpus)
        driver_major = _driver_major(gpus)
        if (
            capability is not None
            and capability < 7.5
        ) or (
            driver_major is not None
            and driver_major < 580
            and (capability is None or capability < 10)
        ):
            return PytorchRuntimePlan(
                accelerator="cuda",
                index_url="https://download.pytorch.org/whl/cu126",
                packages=packages,
                reason="检测到旧架构 NVIDIA 显卡，使用兼容 CUDA 12.6 运行时。",
            )
        return PytorchRuntimePlan(
            accelerator="cuda",
            index_url="https://download.pytorch.org/whl/cu130",
            packages=packages,
            reason="检测到 NVIDIA 显卡，使用支持 RTX 50 / Blackwell 的 CUDA 13.0 运行时。",
        )
    if platform_name.startswith(("win", "linux")):
        return PytorchRuntimePlan(
            accelerator="cpu",
            index_url="https://download.pytorch.org/whl/cpu",
            packages=packages,
            reason="未检测到 NVIDIA 驱动，使用 CPU 运行时。",
        )
    return PytorchRuntimePlan(
        accelerator="mps" if platform_name == "darwin" else "cpu",
        index_url="",
        packages=packages,
        reason="使用当前平台的 PyTorch 官方运行时。",
    )


def _safe_cuda_error(torch_module: Any) -> str:
    try:
        torch_module.cuda.init()
    except Exception as exc:
        return str(exc)
    return ""


def build_gpu_diagnostics(
    torch_module: Any | None,
    nvidia_gpus: Sequence[NvidiaGpu] | None = None,
) -> dict[str, Any]:
    gpus = list(nvidia_gpus if nvidia_gpus is not None else detect_nvidia_gpus())
    result: dict[str, Any] = {
        "available": False,
        "accelerator": "cpu",
        "status": "no_gpu",
        "message": "未检测到 NVIDIA GPU，将使用 CPU。",
        "action": "",
        "device_count": 0,
        "devices": [],
        "nvidia_devices": [asdict(gpu) for gpu in gpus],
        "torch_version": "",
        "cuda_version": "",
        "arch_list": [],
    }
    if torch_module is None:
        result.update({
            "status": "torch_unavailable",
            "message": "PyTorch 未安装，无法初始化识别运行时。",
            "action": "重新运行启动脚本以安装依赖。",
        })
        return result

    result["torch_version"] = str(getattr(torch_module, "__version__", "") or "")
    result["cuda_version"] = str(getattr(getattr(torch_module, "version", None), "cuda", "") or "")
    cuda = getattr(torch_module, "cuda", None)
    try:
        cuda_available = bool(cuda and cuda.is_available())
    except Exception as exc:
        cuda_available = False
        result["error"] = str(exc)

    if cuda_available:
        try:
            device_count = int(cuda.device_count())
            arch_list = list(cuda.get_arch_list()) if hasattr(cuda, "get_arch_list") else []
            devices = []
            unsupported_architectures = []
            for index in range(device_count):
                device = {"index": index, "name": str(cuda.get_device_name(index))}
                if hasattr(cuda, "get_device_capability"):
                    major, minor = cuda.get_device_capability(index)
                    architecture = f"sm_{int(major)}{int(minor)}"
                    device["compute_capability"] = f"{int(major)}.{int(minor)}"
                    device["architecture"] = architecture
                    if arch_list and not any(
                        str(supported).startswith(architecture)
                        for supported in arch_list
                    ):
                        unsupported_architectures.append(architecture)
                devices.append(device)
        except Exception as exc:
            result.update({
                "status": "cuda_query_failed",
                "message": "CUDA 已启用，但读取显卡信息失败。",
                "action": "请导出诊断包并提交问题。",
                "error": str(exc),
            })
            return result
        if unsupported_architectures:
            unsupported = ", ".join(sorted(set(unsupported_architectures)))
            result.update({
                "status": "unsupported_gpu_architecture",
                "message": f"CUDA 可以初始化，但当前 PyTorch 不包含显卡架构 {unsupported}。",
                "action": "关闭应用后重新运行 start.bat，安装与显卡匹配的 CUDA 运行时。",
                "device_count": device_count,
                "devices": devices,
                "arch_list": arch_list,
            })
            return result
        result.update({
            "available": True,
            "accelerator": "cuda",
            "status": "ready",
            "message": "CUDA GPU 可用。",
            "device_count": device_count,
            "devices": devices,
            "arch_list": arch_list,
        })
        return result

    mps = getattr(getattr(torch_module, "backends", None), "mps", None)
    try:
        if mps and mps.is_available():
            result.update({
                "available": True,
                "accelerator": "mps",
                "status": "ready",
                "message": "Apple Metal GPU 可用。",
                "device_count": 1,
                "devices": [{"index": 0, "name": "Apple Metal (MPS)"}],
            })
            return result
    except Exception:
        pass

    if gpus and not result["cuda_version"]:
        result.update({
            "status": "torch_cpu_build",
            "message": "检测到 NVIDIA 显卡，但当前安装的是 CPU 版 PyTorch。",
            "action": "关闭应用后重新运行 start.bat，启动脚本会安装 CUDA 版 PyTorch。",
        })
        return result

    if gpus:
        cuda_error = _safe_cuda_error(torch_module)
        result.update({
            "status": "cuda_initialization_failed",
            "message": "检测到 NVIDIA 显卡和 CUDA 版 PyTorch，但 CUDA 初始化失败。",
            "action": "请更新 NVIDIA 驱动后重启；若仍失败，请导出诊断包。",
        })
        if cuda_error:
            result["error"] = cuda_error
        return result

    return result


def _installed_runtime_matches(plan: PytorchRuntimePlan) -> bool:
    try:
        import torch  # type: ignore
        import torchvision  # type: ignore
    except Exception:
        return False

    torch_version = str(getattr(torch, "__version__", "") or "")
    torchvision_version = str(getattr(torchvision, "__version__", "") or "")
    if not torch_version.startswith(PYTORCH_VERSION):
        return False
    if not torchvision_version.startswith(TORCHVISION_VERSION):
        return False
    if plan.accelerator == "cuda":
        cuda_version = str(getattr(torch.version, "cuda", "") or "")
        if plan.index_url.endswith("/cu130"):
            return cuda_version.startswith("13.")
        if plan.index_url.endswith("/cu126"):
            return cuda_version.startswith("12.6")
        return bool(cuda_version)
    return True


def _windows_cuda_wheel_urls(
    plan: PytorchRuntimePlan,
    *,
    platform_name: str,
    python_tag: str,
    machine: str,
) -> tuple[str, ...]:
    architecture = machine.lower()
    runtime = plan.index_url.rstrip("/").rsplit("/", 1)[-1]
    if (
        not platform_name.startswith("win")
        or plan.accelerator != "cuda"
        or architecture not in {"amd64", "x86_64"}
        or runtime not in {"cu126", "cu130"}
        or python_tag not in {"cp310", "cp311"}
    ):
        return ()

    base_url = f"https://download-r2.pytorch.org/whl/{runtime}"
    return (
        f"{base_url}/torch-{PYTORCH_VERSION}%2B{runtime}"
        f"-{python_tag}-{python_tag}-win_amd64.whl",
        f"{base_url}/torchvision-{TORCHVISION_VERSION}%2B{runtime}"
        f"-{python_tag}-{python_tag}-win_amd64.whl",
    )


def install_pytorch_runtime(
    plan: PytorchRuntimePlan,
    *,
    platform_name: str = sys.platform,
    python_tag: str | None = None,
    machine: str | None = None,
) -> None:
    detected_gpus = detect_nvidia_gpus()
    capability = _compute_capability(detected_gpus)
    driver_major = _driver_major(detected_gpus)
    if (
        plan.index_url.endswith("/cu130")
        and driver_major is not None
        and driver_major < 580
    ):
        device = detected_gpus[0].name if detected_gpus else "NVIDIA GPU"
        architecture_hint = "（RTX 50 / Blackwell 需要 CUDA 12.8 以上）" if (capability or 0) >= 10 else ""
        raise RuntimeError(
            f"{device} 的驱动版本为 {detected_gpus[0].driver_version}，"
            f"CUDA 13 运行时要求 NVIDIA R580 或更高版本{architecture_hint}。"
            "请先从 NVIDIA 官方更新驱动，再重新运行启动脚本。"
        )
    if _installed_runtime_matches(plan):
        print(f"[PyTorch] 已就绪：{plan.reason}")
        return

    resolved_python_tag = python_tag or f"cp{sys.version_info.major}{sys.version_info.minor}"
    resolved_machine = machine or platform.machine()
    direct_wheels = _windows_cuda_wheel_urls(
        plan,
        platform_name=platform_name,
        python_tag=resolved_python_tag,
        machine=resolved_machine,
    )
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--force-reinstall",
        *(direct_wheels or plan.packages),
    ]
    if plan.index_url and not direct_wheels:
        command.extend(["--index-url", plan.index_url])
    print(
        f"[Runtime] Python {sys.version.split()[0]} / {platform_name} / "
        f"{resolved_machine or 'unknown'} / {resolved_python_tag}"
    )
    print(f"[PyTorch] {plan.reason}")
    print(
        "[PyTorch] 安装源："
        + ("PyTorch 官方固定 wheel" if direct_wheels else (plan.index_url or "PyPI"))
    )
    subprocess.run(command, check=True)


def verify_runtime(plan: PytorchRuntimePlan) -> dict[str, Any]:
    try:
        import torch  # type: ignore
    except Exception:
        torch = None
    diagnostics = build_gpu_diagnostics(torch, detect_nvidia_gpus())
    if plan.accelerator == "cuda" and diagnostics["status"] != "ready":
        raise RuntimeError(
            f"{diagnostics['message']} {diagnostics.get('action', '')} "
            f"详细原因：{diagnostics.get('error') or '无'}"
        )
    return diagnostics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="选择、安装并检查 Solar Manga Translator 的 PyTorch 运行时。")
    parser.add_argument("--install", action="store_true", help="安装与当前硬件匹配的 PyTorch。")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出诊断结果。")
    args = parser.parse_args(argv)

    gpus = detect_nvidia_gpus()
    plan = choose_pytorch_runtime(nvidia_gpus=gpus)
    if args.install:
        install_pytorch_runtime(plan)
    diagnostics = verify_runtime(plan)
    if args.json:
        print(json.dumps({"plan": asdict(plan), "diagnostics": diagnostics}, ensure_ascii=False, indent=2))
    else:
        print(f"[PyTorch] {diagnostics['message']}")
        if diagnostics.get("devices"):
            print("[PyTorch] " + " / ".join(device["name"] for device in diagnostics["devices"]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[错误] PyTorch 运行时准备失败：{exc}", file=sys.stderr)
        raise SystemExit(2)
