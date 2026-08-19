#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


WHISPER_TAG = "v1.5.5"
WHISPER_REPO = "https://github.com/ggerganov/whisper.cpp.git"
MODEL_NAME = "ggml-base-q5_1.bin"
MODEL_URL = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
    "ggml-base-q5_1.bin"
)
MODEL_SHA256 = "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898"
IMAGEIO_FFMPEG_VERSION = "0.6.0"
FFMPEG_WHEEL_NAME = "imageio_ffmpeg-0.6.0-py3-none-macosx_11_0_arm64.whl"
FFMPEG_WHEEL_URL = (
    "https://files.pythonhosted.org/packages/40/5c/"
    "f3d8a657d362cc93b81aab8feda487317da5b5d31c0e1fdfd5e986e55d17/"
    + FFMPEG_WHEEL_NAME
)
FFMPEG_WHEEL_SHA256 = "b1ae3173414b5fc5f538a726c4e48ea97edc0d2cdc11f103afee655c463fa742"


def default_cache_root() -> Path:
    configured = os.environ.get("FCS_RENAME_CACHE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if platform.system() == "Darwin":
        return (
            Path.home() / "Library" / "Caches" / "fcs-rename"
        ).resolve()
    cache_home = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return (base / "fcs-rename").resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_paths(cache_root: Path) -> dict[str, Path]:
    return {
        "cli": cache_root / "bin" / "whisper-cli",
        "model": cache_root / "models" / MODEL_NAME,
        "python": cache_root / "python",
    }


def find_cached_ffmpeg(python_root: Path) -> Path | None:
    if not python_root.is_dir():
        return None
    python_text = str(python_root)
    if python_text not in sys.path:
        sys.path.insert(0, python_text)
    try:
        module = importlib.import_module("imageio_ffmpeg")
        path = Path(module.get_ffmpeg_exe()).resolve()
    except Exception:
        return None
    return path if path.is_file() else None


def check_status(cache_root: Path) -> tuple[bool, list[str]]:
    paths = cache_paths(cache_root)
    messages: list[str] = []
    cli_ok = paths["cli"].is_file() and os.access(paths["cli"], os.X_OK)
    messages.append(f"语音识别程序：{'可用' if cli_ok else '缺失'}")

    model_ok = (
        paths["model"].is_file()
        and sha256(paths["model"]) == MODEL_SHA256
    )
    messages.append(f"中文识别模型：{'可用' if model_ok else '缺失或校验失败'}")

    ffmpeg = find_cached_ffmpeg(paths["python"])
    ffmpeg_ok = ffmpeg is not None
    messages.append(
        f"FFmpeg：{'可用 ' + str(ffmpeg) if ffmpeg_ok else '缺失'}"
    )
    return cli_ok and model_ok and ffmpeg_ok, messages


def run_checked(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def install_ffmpeg(python_root: Path) -> None:
    if find_cached_ffmpeg(python_root):
        return
    python_root.mkdir(parents=True, exist_ok=True)
    install_target = f"imageio-ffmpeg=={IMAGEIO_FFMPEG_VERSION}"
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        downloads = python_root.parent / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        wheel = downloads / FFMPEG_WHEEL_NAME
        if not wheel.is_file() or sha256(wheel) != FFMPEG_WHEEL_SHA256:
            partial = wheel.with_suffix(wheel.suffix + ".partial")
            command = [
                "curl",
                "--http1.1",
                "-L",
                "--fail",
                "--retry",
                "5",
                "--retry-delay",
                "2",
            ]
            if partial.exists():
                command.extend(["-C", "-"])
            command.extend(["-o", str(partial), FFMPEG_WHEEL_URL])
            run_checked(command)
            actual = sha256(partial)
            if actual != FFMPEG_WHEEL_SHA256:
                raise RuntimeError(
                    f"FFmpeg 安装包校验失败：期望 {FFMPEG_WHEEL_SHA256}，实际 {actual}"
                )
            os.replace(partial, wheel)
        install_target = str(wheel)
    run_checked(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--target",
            str(python_root),
            install_target,
        ]
    )
    if not find_cached_ffmpeg(python_root):
        raise RuntimeError("FFmpeg 安装后仍不可用")


def install_cli(cli_path: Path, reuse_build: Path | None) -> None:
    if cli_path.is_file() and os.access(cli_path, os.X_OK):
        return
    cli_path.parent.mkdir(parents=True, exist_ok=True)

    if reuse_build:
        source = reuse_build / "main"
        if not source.is_file():
            raise RuntimeError(f"复用目录中没有识别程序：{source}")
        shutil.copy2(source, cli_path)
    else:
        with tempfile.TemporaryDirectory(prefix="fcs-rename-build-") as temp:
            source_root = Path(temp) / "whisper.cpp"
            run_checked(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    WHISPER_TAG,
                    WHISPER_REPO,
                    str(source_root),
                ]
            )
            run_checked(["make", "-j4", "main"], cwd=source_root)
            shutil.copy2(source_root / "main", cli_path)

    cli_path.chmod(cli_path.stat().st_mode | 0o111)


def download_model(model_path: Path, reuse_build: Path | None) -> None:
    if model_path.is_file() and sha256(model_path) == MODEL_SHA256:
        return
    model_path.parent.mkdir(parents=True, exist_ok=True)

    if reuse_build:
        source = reuse_build / "models" / MODEL_NAME
        if not source.is_file():
            raise RuntimeError(f"复用目录中没有中文模型：{source}")
        partial = model_path.with_suffix(model_path.suffix + ".partial")
        shutil.copy2(source, partial)
    else:
        partial = model_path.with_suffix(model_path.suffix + ".partial")
        command = [
            "curl",
            "--http1.1",
            "-L",
            "--fail",
            "--retry",
            "5",
            "--retry-delay",
            "2",
        ]
        if partial.exists():
            command.extend(["-C", "-"])
        command.extend(["-o", str(partial), MODEL_URL])
        run_checked(command)

    actual = sha256(partial)
    if actual != MODEL_SHA256:
        raise RuntimeError(
            f"中文模型校验失败：期望 {MODEL_SHA256}，实际 {actual}"
        )
    os.replace(partial, model_path)


def command_status(args: argparse.Namespace) -> int:
    cache_root = Path(args.cache).expanduser().resolve()
    ready, messages = check_status(cache_root)
    print(f"缓存目录：{cache_root}")
    for message in messages:
        print(message)
    print(f"快速安全分析：{'可用' if ready else '不可用'}")
    return 0 if ready else 1


def command_install(args: argparse.Namespace) -> int:
    cache_root = Path(args.cache).expanduser().resolve()
    reuse_build = (
        Path(args.reuse_build).expanduser().resolve()
        if args.reuse_build
        else None
    )
    paths = cache_paths(cache_root)

    install_cli(paths["cli"], reuse_build)
    download_model(paths["model"], reuse_build)
    install_ffmpeg(paths["python"])

    ready, messages = check_status(cache_root)
    print(f"缓存目录：{cache_root}")
    for message in messages:
        print(message)
    if not ready:
        print("本地识别环境准备失败。", file=sys.stderr)
        return 1
    print("本地识别环境已准备完成。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and verify the local fcs-rename analysis cache."
    )
    parser.add_argument("--cache", default=str(default_cache_root()))
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="check the local cache")
    status.set_defaults(func=command_status)

    install = sub.add_parser("install", help="install the local analysis cache")
    install.add_argument(
        "--reuse-build",
        default="",
        help="reuse an existing whisper.cpp build directory",
    )
    install.set_defaults(func=command_install)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
