import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UPSTREAM_CONFIG_FILE = ROOT / "upstream.json"
UPSTREAM_REQUIREMENTS_FILE = ROOT / "requirements-upstream.txt"
UPSTREAM_CHECKOUT_DIR = ROOT / "manga-image-translator"


def run(command: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
    printable = " ".join(str(part) for part in command)
    if cwd:
        print(f"--> ({cwd}) {printable}")
    else:
        print(f"--> {printable}")

    if capture:
        return subprocess.check_output(command, cwd=cwd, text=True).strip()

    subprocess.run(command, cwd=cwd, check=True)
    return ""


def load_upstream_metadata() -> dict[str, str]:
    data = json.loads(UPSTREAM_CONFIG_FILE.read_text(encoding="utf-8"))
    metadata = data["manga_image_translator"]
    return {
        "repository": str(metadata["repository"]),
        "commit": str(metadata["commit"]),
    }


def git_output(args: list[str]) -> str:
    return run(["git", *args], cwd=UPSTREAM_CHECKOUT_DIR, capture=True)


def current_upstream_commit() -> str | None:
    try:
        return git_output(["rev-parse", "HEAD"])
    except subprocess.CalledProcessError:
        return None


def upstream_has_local_changes() -> bool:
    return bool(git_output(["status", "--porcelain", "--untracked-files=all"]))


def ensure_upstream_checkout() -> None:
    metadata = load_upstream_metadata()
    repository = metadata["repository"]
    commit = metadata["commit"]

    if not UPSTREAM_CHECKOUT_DIR.exists():
        run(["git", "clone", "--no-checkout", repository, str(UPSTREAM_CHECKOUT_DIR)])
    elif not (UPSTREAM_CHECKOUT_DIR / ".git").exists():
        raise RuntimeError(
            f"{UPSTREAM_CHECKOUT_DIR} exists but is not a git checkout. "
            "Move it aside and rerun this script."
        )

    current = current_upstream_commit()
    if current == commit:
        print(f"manga-image-translator already at pinned commit {commit}.")
    else:
        if current and upstream_has_local_changes():
            raise RuntimeError(
                "The existing manga-image-translator checkout has local changes and "
                f"is not at the pinned commit {commit}. Move it aside before rerunning."
            )
        run(["git", "fetch", "--depth", "1", "origin", commit], cwd=UPSTREAM_CHECKOUT_DIR)
        try:
            run(["git", "switch", "--detach", commit], cwd=UPSTREAM_CHECKOUT_DIR)
        except subprocess.CalledProcessError:
            run(["git", "checkout", "--detach", commit], cwd=UPSTREAM_CHECKOUT_DIR)

    sys.path.insert(0, str(ROOT))
    import patch_pydensecrf

    if not patch_pydensecrf.patch_mask_refinement():
        raise RuntimeError("Failed to patch manga-image-translator runtime files.")


def install_requirements() -> None:
    if not UPSTREAM_REQUIREMENTS_FILE.exists():
        raise RuntimeError(f"Missing requirements snapshot: {UPSTREAM_REQUIREMENTS_FILE}")
    run([sys.executable, "-m", "pip", "install", "-r", str(UPSTREAM_REQUIREMENTS_FILE)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install and prepare the pinned manga-image-translator dependency."
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Clone/check out and patch the pinned upstream dependency without installing packages.",
    )
    parser.add_argument(
        "--skip-prepare",
        action="store_true",
        help="Install the pinned requirements snapshot without touching the upstream checkout.",
    )
    args = parser.parse_args(argv)

    print("===================================================")
    print("Preparing pinned manga-image-translator dependency")
    print("===================================================")

    if args.prepare_only and args.skip_prepare:
        parser.error("--prepare-only and --skip-prepare cannot be used together")

    if not args.prepare_only:
        install_requirements()

    if not args.skip_prepare:
        ensure_upstream_checkout()

    print("===================================================")
    print("Dependency preparation completed.")
    print("===================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
