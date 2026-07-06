import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UPSTREAM_CONFIG_FILE = ROOT / "upstream.json"
UPSTREAM_REQUIREMENTS_FILE = ROOT / "requirements-upstream.txt"
UPSTREAM_CHECKOUT_DIR = ROOT / "manga-image-translator"
UPSTREAM_ARCHIVE_MARKER = ".solar-upstream-commit"
GITHUB_ARCHIVE_MIRRORS = (
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "",
)


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
        "archive_sha256": str(metadata.get("archive_sha256") or ""),
    }


def git_output(args: list[str]) -> str:
    return run(["git", *args], cwd=UPSTREAM_CHECKOUT_DIR, capture=True)


def current_upstream_commit() -> str | None:
    marker_path = UPSTREAM_CHECKOUT_DIR / UPSTREAM_ARCHIVE_MARKER
    if marker_path.exists():
        value = marker_path.read_text(encoding="utf-8").strip()
        if value:
            return value
    try:
        return git_output(["rev-parse", "HEAD"])
    except subprocess.CalledProcessError:
        return None


def upstream_has_local_changes() -> bool:
    return bool(git_output(["status", "--porcelain", "--untracked-files=all"]))


def upstream_archive_urls(repository: str, commit: str) -> list[str]:
    normalized = repository.removesuffix(".git").rstrip("/")
    if not normalized.startswith("https://github.com/"):
        return []
    archive_url = f"{normalized}/archive/{commit}.tar.gz"
    return [f"{prefix}{archive_url}" if prefix else archive_url for prefix in GITHUB_ARCHIVE_MIRRORS]


def _download_archive_candidate(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Solar-Manga-Translator/upstream-bootstrap"},
    )
    with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
        downloaded = 0
        next_report = 8 * 1024 * 1024
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            downloaded += len(chunk)
            if downloaded >= next_report:
                print(f"--> Upstream archive: {downloaded // (1024 * 1024)} MiB")
                next_report += 8 * 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _install_upstream_archive(archive_path: Path, checkout: Path, commit: str) -> None:
    with tempfile.TemporaryDirectory(prefix="solar-manga-upstream-") as tmp:
        extract_root = Path(tmp)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                if member.issym() or member.islnk() or member.isdev():
                    raise RuntimeError(f"Unsafe entry in upstream archive: {member.name}")
                destination = (extract_root / member.name).resolve()
                if extract_root.resolve() not in destination.parents and destination != extract_root.resolve():
                    raise RuntimeError(f"Path traversal in upstream archive: {member.name}")
            archive.extractall(extract_root, members=members)

        roots = [path for path in extract_root.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("Unexpected upstream archive layout.")
        source_root = roots[0]
        for child in checkout.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in source_root.iterdir():
            destination = checkout / child.name
            if child.is_dir():
                shutil.copytree(child, destination)
            else:
                shutil.copy2(child, destination)
        (checkout / UPSTREAM_ARCHIVE_MARKER).write_text(commit + "\n", encoding="utf-8")


def prepare_upstream_from_archive(
    repository: str,
    commit: str,
    expected_sha256: str,
    checkout: Path,
) -> None:
    if not expected_sha256:
        raise RuntimeError("Pinned upstream archive checksum is missing.")
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="solar-manga-download-") as tmp:
        archive_path = Path(tmp) / "upstream.tar.gz"
        for url in upstream_archive_urls(repository, commit):
            try:
                print(f"--> Downloading pinned upstream archive: {url}")
                _download_archive_candidate(url, archive_path)
                actual_sha256 = _sha256_file(archive_path)
                if actual_sha256 != expected_sha256:
                    raise RuntimeError(
                        f"checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
                    )
                _install_upstream_archive(archive_path, checkout, commit)
                print("Pinned upstream archive downloaded and verified.")
                return
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                errors.append(f"{url}: {exc}")
                archive_path.unlink(missing_ok=True)
    raise RuntimeError("All upstream archive sources failed: " + " | ".join(errors))


def ensure_upstream_checkout() -> None:
    metadata = load_upstream_metadata()
    repository = metadata["repository"]
    commit = metadata["commit"]
    archive_sha256 = metadata.get("archive_sha256", "")

    created_checkout = False
    if not UPSTREAM_CHECKOUT_DIR.exists():
        UPSTREAM_CHECKOUT_DIR.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "init", "--initial-branch", "main", str(UPSTREAM_CHECKOUT_DIR)])
        run(["git", "remote", "add", "origin", repository], cwd=UPSTREAM_CHECKOUT_DIR)
        created_checkout = True
    elif not (UPSTREAM_CHECKOUT_DIR / ".git").exists():
        raise RuntimeError(
            f"{UPSTREAM_CHECKOUT_DIR} exists but is not a git checkout. "
            "Move it aside and rerun this script."
        )

    current = None if created_checkout else current_upstream_commit()
    if current == commit:
        print(f"manga-image-translator already at pinned commit {commit}.")
    else:
        archive_checkout = (UPSTREAM_CHECKOUT_DIR / UPSTREAM_ARCHIVE_MARKER).exists()
        if not created_checkout and current and not archive_checkout and upstream_has_local_changes():
            raise RuntimeError(
                "The existing manga-image-translator checkout has local changes and "
                f"is not at the pinned commit {commit}. Move it aside before rerunning."
            )
        try:
            run(["git", "fetch", "--depth", "1", "origin", commit], cwd=UPSTREAM_CHECKOUT_DIR)
            try:
                run(["git", "switch", "--detach", commit], cwd=UPSTREAM_CHECKOUT_DIR)
            except subprocess.CalledProcessError:
                run(["git", "checkout", "--detach", commit], cwd=UPSTREAM_CHECKOUT_DIR)
            (UPSTREAM_CHECKOUT_DIR / UPSTREAM_ARCHIVE_MARKER).unlink(missing_ok=True)
        except subprocess.CalledProcessError:
            print("GitHub fetch failed; switching to verified source archive mirrors.")
            prepare_upstream_from_archive(
                repository,
                commit,
                archive_sha256,
                UPSTREAM_CHECKOUT_DIR,
            )

    sys.path.insert(0, str(ROOT))
    import patch_pydensecrf

    if not patch_pydensecrf.patch_mask_refinement():
        raise RuntimeError("Failed to patch manga-image-translator runtime files.")


def install_requirements() -> None:
    if not UPSTREAM_REQUIREMENTS_FILE.exists():
        raise RuntimeError(f"Missing requirements snapshot: {UPSTREAM_REQUIREMENTS_FILE}")
    from pip_install import install_with_fallback

    install_with_fallback(["-r", str(UPSTREAM_REQUIREMENTS_FILE)])


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
