from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_HEARTBEAT_SECONDS = 15.0


def _format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes:02d}:{remaining_seconds:02d}"


def run_bootstrap_command(
    command: Sequence[str],
    *,
    label: str,
    log_path: Path | None = None,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    normalized_command = [str(part) for part in command if str(part)]
    if not normalized_command:
        raise ValueError("安装命令不能为空。")
    if heartbeat_seconds <= 0:
        raise ValueError("安装心跳间隔必须大于 0。")

    normalized_label = str(label or "安装任务").strip() or "安装任务"
    started_at = time.monotonic()
    last_output_at = [started_at]
    write_lock = threading.Lock()
    log_handle = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("ab")

    def write_bytes(payload: bytes) -> None:
        if not payload:
            return
        with write_lock:
            output_buffer = getattr(sys.stdout, "buffer", None)
            if output_buffer is not None:
                output_buffer.write(payload)
                output_buffer.flush()
            else:
                sys.stdout.write(payload.decode("utf-8", errors="replace"))
                sys.stdout.flush()
            if log_handle is not None:
                log_handle.write(payload)
                log_handle.flush()

    def write_status(message: str) -> None:
        write_bytes((message.rstrip() + os.linesep).encode("utf-8"))

    write_status(
        f"[Install] {normalized_label}：开始。下载和解包大文件可能需要几分钟，请勿关闭窗口。"
    )
    try:
        process = subprocess.Popen(
            normalized_command,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except OSError as exc:
        write_status(f"[Install] {normalized_label}：无法启动：{exc}")
        if log_handle is not None:
            log_handle.close()
        return 1

    def mirror_output() -> None:
        if process.stdout is None:
            return
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                last_output_at[0] = time.monotonic()
                write_bytes(chunk)
        finally:
            process.stdout.close()

    output_thread = threading.Thread(
        target=mirror_output,
        name="bootstrap-output",
        daemon=True,
    )
    output_thread.start()
    last_heartbeat_at = started_at
    try:
        while process.poll() is None:
            output_thread.join(timeout=min(heartbeat_seconds, 0.5))
            now = time.monotonic()
            if (
                now - last_output_at[0] >= heartbeat_seconds
                and now - last_heartbeat_at >= heartbeat_seconds
            ):
                write_status(
                    f"[Install] {normalized_label}：仍在运行，已用时 "
                    f"{_format_elapsed(now - started_at)}；最近暂时没有新输出。"
                )
                last_heartbeat_at = now
        return_code = process.wait()
        output_thread.join(timeout=2)
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        write_status(f"[Install] {normalized_label}：已由用户中止。")
        return_code = 130

    elapsed = _format_elapsed(time.monotonic() - started_at)
    if return_code == 0:
        write_status(f"[Install] {normalized_label}：已完成，总用时 {elapsed}。")
    else:
        write_status(
            f"[Install] {normalized_label}：失败（退出码 {return_code}），总用时 {elapsed}。"
        )
    if log_handle is not None:
        log_handle.close()
    return return_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="运行安装命令，同时镜像日志并在静默阶段持续显示心跳。"
    )
    parser.add_argument("--label", required=True, help="展示给用户的安装阶段名称。")
    parser.add_argument("--log", default="", help="可选的追加日志文件路径。")
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=DEFAULT_HEARTBEAT_SECONDS,
        help="没有新输出时的心跳间隔。",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    return run_bootstrap_command(
        command,
        label=args.label,
        log_path=Path(args.log) if args.log else None,
        heartbeat_seconds=args.heartbeat_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
