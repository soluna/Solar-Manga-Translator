from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Sequence


PYTORCH_VERSION = "2.12.1"
TORCHVISION_VERSION = "0.27.1"
DOWNLOAD_PROBE_BYTES = 256 * 1024
DOWNLOAD_PROBE_TIMEOUT_SECONDS = 8
PIP_SOCKET_TIMEOUT_SECONDS = 30
WINDOWS_WHEEL_SHA256 = {
    ("cu126", "cp310", "torch"): "75b223d98517a4f14d1cf4f53767ddbc953f2e6f7d811f3fd045b7cbbb129b05",
    ("cu126", "cp310", "torchvision"): "cc1dbe9fa2507a27ebdcb1d415fff4f40f28349743dd0fd28eec8e86a24179d3",
    ("cu126", "cp311", "torch"): "b7d68e60097b75d7dd507d1268144c7770de3b019f2a4cb3fe36550c9b4f3320",
    ("cu126", "cp311", "torchvision"): "9f994c24e7ef9e9b9149a6f83c235cc6e9794862339350abb35fbe66858a923b",
    ("cu130", "cp310", "torch"): "3b6e6e3ce55c3ebd688b00001cd44ff1a43fa30823f0394d20c8fd9910fb7087",
    ("cu130", "cp310", "torchvision"): "1649be85c5ffde20a6fb68b659df4114a8a507ed11613de26ceb4a063075ed2b",
    ("cu130", "cp311", "torch"): "5ff38932260cb4d5a52170d955642f6ede17f565de64e62eaca12a875851471b",
    ("cu130", "cp311", "torchvision"): "14da1217bef76488d3a1647bca9da8b1bc0f52238b027a4a69036d461e60102a",
}


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


def _parse_cuda_architecture(architecture: str) -> tuple[str, int, int] | None:
    prefix, separator, encoded = str(architecture).lower().partition("_")
    if separator != "_" or prefix not in {"sm", "compute"} or not encoded.isdigit():
        return None
    if len(encoded) < 2:
        return None
    return prefix, int(encoded[:-1]), int(encoded[-1])


def _cuda_architecture_is_compatible(
    device_capability: tuple[int, int],
    compiled_architectures: Sequence[str],
) -> bool:
    device_major, device_minor = device_capability
    for architecture in compiled_architectures:
        parsed = _parse_cuda_architecture(str(architecture))
        if parsed is None:
            continue
        kind, compiled_major, compiled_minor = parsed
        if kind == "sm":
            if compiled_major == device_major and compiled_minor <= device_minor:
                return True
            continue
        if (compiled_major, compiled_minor) <= (device_major, device_minor):
            return True
    return False


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
                    if arch_list and not _cuda_architecture_is_compatible(
                        (int(major), int(minor)),
                        arch_list,
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
        torch_version = importlib.metadata.version("torch")
        torchvision_version = importlib.metadata.version("torchvision")
    except importlib.metadata.PackageNotFoundError:
        return False

    if not torch_version.startswith(PYTORCH_VERSION):
        return False
    if not torchvision_version.startswith(TORCHVISION_VERSION):
        return False
    if plan.accelerator == "cuda":
        runtime = plan.index_url.rstrip("/").rsplit("/", 1)[-1]
        build_tag = f"+{runtime}"
        return build_tag in torch_version and build_tag in torchvision_version
    return True


def _remove_obsolete_torchaudio() -> None:
    try:
        installed_version = importlib.metadata.version("torchaudio")
    except importlib.metadata.PackageNotFoundError:
        return

    print(
        f"[PyTorch] 正在移除项目未使用的旧 torchaudio {installed_version}，"
        "避免与目标 torch 运行时冲突。"
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "--yes", "torchaudio"],
        check=False,
    )
    if completed.returncode != 0:
        print("[PyTorch] torchaudio 清理未完成；它不影响本项目，将继续准备运行时。")


def _windows_cuda_wheel_urls(
    plan: PytorchRuntimePlan,
    *,
    platform_name: str,
    python_tag: str,
    machine: str,
    base_url: str | None = None,
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

    resolved_base_url = (
        base_url or f"https://download-r2.pytorch.org/whl/{runtime}"
    ).rstrip("/")
    torch_sha256 = WINDOWS_WHEEL_SHA256[(runtime, python_tag, "torch")]
    torchvision_sha256 = WINDOWS_WHEEL_SHA256[(runtime, python_tag, "torchvision")]
    return (
        f"{resolved_base_url}/torch-{PYTORCH_VERSION}%2B{runtime}"
        f"-{python_tag}-{python_tag}-win_amd64.whl#sha256={torch_sha256}",
        f"{resolved_base_url}/torchvision-{TORCHVISION_VERSION}%2B{runtime}"
        f"-{python_tag}-{python_tag}-win_amd64.whl#sha256={torchvision_sha256}",
    )


def _probe_wheel_source(
    wheel_urls: Sequence[str],
    *,
    timeout: int = DOWNLOAD_PROBE_TIMEOUT_SECONDS,
) -> float:
    request = urllib.request.Request(
        wheel_urls[0],
        headers={
            "Range": f"bytes=0-{DOWNLOAD_PROBE_BYTES - 1}",
            "User-Agent": "Solar-Manga-Translator/bootstrap",
        },
    )
    started_at = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        sample = response.read(DOWNLOAD_PROBE_BYTES)
    if not sample:
        raise OSError("下载源未返回数据")
    return max(time.monotonic() - started_at, 0.001)


def _ranked_windows_cuda_wheel_sources(
    plan: PytorchRuntimePlan,
    *,
    platform_name: str,
    python_tag: str,
    machine: str,
) -> list[tuple[str, tuple[str, ...]]]:
    official_urls = _windows_cuda_wheel_urls(
        plan,
        platform_name=platform_name,
        python_tag=python_tag,
        machine=machine,
    )
    if not official_urls:
        return []

    runtime = plan.index_url.rstrip("/").rsplit("/", 1)[-1]
    aliyun_urls = _windows_cuda_wheel_urls(
        plan,
        platform_name=platform_name,
        python_tag=python_tag,
        machine=machine,
        base_url=f"https://mirrors.aliyun.com/pytorch-wheels/{runtime}",
    )
    candidates = [
        ("PyTorch 官方", official_urls),
        ("阿里云镜像", aliyun_urls),
    ]
    available: list[tuple[float, str, tuple[str, ...]]] = []
    unavailable: list[tuple[str, tuple[str, ...]]] = []
    for name, wheel_urls in candidates:
        print(f"[Download] 正在测速：{name}...")
        try:
            elapsed = _probe_wheel_source(wheel_urls)
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            print(f"[Download] {name}测速失败：{exc}")
            unavailable.append((name, wheel_urls))
            continue
        speed_kib = DOWNLOAD_PROBE_BYTES / elapsed / 1024
        print(f"[Download] {name}可用：{speed_kib:.0f} KiB/s")
        available.append((elapsed, name, wheel_urls))

    ranked = [
        (name, wheel_urls)
        for _elapsed, name, wheel_urls in sorted(available, key=lambda item: item[0])
    ]
    ranked.extend(unavailable)
    print("[Download] 下载顺序：" + " -> ".join(name for name, _urls in ranked))
    return ranked


def _pip_install_command(
    packages: Sequence[str],
    *,
    index_url: str = "",
) -> list[str]:
    command = [sys.executable, "-m", "pip"]
    bootstrap_log = os.environ.get("BOOTSTRAP_LOG", "").strip()
    if bootstrap_log:
        command.extend(["--log", bootstrap_log])
    command.extend([
        "--disable-pip-version-check",
        "install",
        "--upgrade",
        "--force-reinstall",
        "--timeout",
        str(PIP_SOCKET_TIMEOUT_SECONDS),
        "--retries",
        "1",
        "--progress-bar",
        "on",
        *packages,
    ])
    if index_url:
        command.extend(["--index-url", index_url])
    return command


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
    _remove_obsolete_torchaudio()
    if _installed_runtime_matches(plan):
        print(f"[PyTorch] 已就绪：{plan.reason}")
        return

    resolved_python_tag = python_tag or f"cp{sys.version_info.major}{sys.version_info.minor}"
    resolved_machine = machine or platform.machine()
    direct_sources = _ranked_windows_cuda_wheel_sources(
        plan,
        platform_name=platform_name,
        python_tag=resolved_python_tag,
        machine=resolved_machine,
    )
    print(
        f"[Runtime] Python {sys.version.split()[0]} / {platform_name} / "
        f"{resolved_machine or 'unknown'} / {resolved_python_tag}"
    )
    print(f"[PyTorch] {plan.reason}")
    if direct_sources:
        failures = []
        for index, (source_name, wheel_urls) in enumerate(direct_sources, start=1):
            print(
                f"[PyTorch] 下载源 {index}/{len(direct_sources)}：{source_name}。"
                f"若连续 {PIP_SOCKET_TIMEOUT_SECONDS} 秒无数据会自动重试或切换。"
            )
            command = _pip_install_command(wheel_urls)
            try:
                subprocess.run(
                    command,
                    check=True,
                )
            except subprocess.CalledProcessError as exc:
                failures.append(f"{source_name}：pip 退出码 {exc.returncode}")
                print(f"[PyTorch] {source_name}安装失败，正在切换下载源...")
                continue
            return
        raise RuntimeError("所有 PyTorch 下载源均失败：" + "；".join(failures))

    print(f"[PyTorch] 安装源：{plan.index_url or 'PyPI'}")
    subprocess.run(
        _pip_install_command(plan.packages, index_url=plan.index_url),
        check=True,
    )


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
