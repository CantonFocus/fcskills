#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from runtime_support import (
    current_platform,
    default_cache_root,
    download_verified,
    executable_name,
    executable_ready,
    make_executable,
    run_checked,
    run_text,
    safe_extract_zip,
    sha256,
)


WHISPER_TAG = "v1.9.2"
WHISPER_COMMIT = "306c88f4d1286aec1bf96e544632897886af5501"
WHISPER_REPO = "https://github.com/ggml-org/whisper.cpp.git"
WINDOWS_X64_ARCHIVE_NAME = "whisper-bin-x64.zip"
WINDOWS_X64_ARCHIVE_URL = (
    f"https://github.com/ggml-org/whisper.cpp/releases/download/{WHISPER_TAG}/"
    + WINDOWS_X64_ARCHIVE_NAME
)
WINDOWS_X64_ARCHIVE_SHA256 = (
    "49dcc16de826f20bd53d44f947a1ae49dfa81f86cad67a64d80820cb192d674a"
)
MODEL_NAME = "ggml-base-q5_1.bin"
MODEL_URL = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
    "ggml-base-q5_1.bin"
)
MODEL_SHA256 = "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898"
IMAGEIO_FFMPEG_VERSION = "0.6.0"
IMAGEIO_FFMPEG_WHEELS = {
    "windows-x86_64": (
        "imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl",
        "https://files.pythonhosted.org/packages/2c/c6/"
        "fa760e12a2483469e2bf5058c5faff664acf66cadb4df2ad6205b016a73d/"
        "imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl",
        "02fa47c83703c37df6bfe4896aab339013f62bf02c5ebf2dce6da56af04ffc0a",
    ),
    "darwin-arm64": (
        "imageio_ffmpeg-0.6.0-py3-none-macosx_11_0_arm64.whl",
        "https://files.pythonhosted.org/packages/40/5c/"
        "f3d8a657d362cc93b81aab8feda487317da5b5d31c0e1fdfd5e986e55d17/"
        "imageio_ffmpeg-0.6.0-py3-none-macosx_11_0_arm64.whl",
        "b1ae3173414b5fc5f538a726c4e48ea97edc0d2cdc11f103afee655c463fa742",
    ),
    "darwin-x86_64": (
        "imageio_ffmpeg-0.6.0-py3-none-macosx_10_9_intel.macosx_10_9_x86_64.whl",
        "https://files.pythonhosted.org/packages/da/58/"
        "87ef68ac83f4c7690961bce288fd8e382bc5f1513860fc7f90a9c1c1c6bf/"
        "imageio_ffmpeg-0.6.0-py3-none-macosx_10_9_intel.macosx_10_9_x86_64.whl",
        "9d2baaf867088508d4a3458e61eeb30e945c4ad8016025545f66c4b5aaef0a61",
    ),
}
CMAKE_VERSION = "3.27.7"
CMAKE_MINIMUM_VERSION = (3, 14, 0)
CMAKE_MACOS_WHEEL_NAME = (
    "cmake-3.27.7-py2.py3-none-macosx_10_10_universal2."
    "macosx_10_10_x86_64.macosx_11_0_arm64.macosx_11_0_universal2.whl"
)
CMAKE_MACOS_WHEEL_URL = (
    "https://files.pythonhosted.org/packages/d9/e0/"
    "6c919f63a2ef07ab02daa9039688cda551f69483592bd5852095ba3f62b7/"
    + CMAKE_MACOS_WHEEL_NAME
)
CMAKE_MACOS_WHEEL_SHA256 = (
    "d582ef3e9ff0bd113581c1a32e881d1c2f9a34d2de76c93324a28593a76433db"
)
MINIMUM_PYTHON = (3, 9)
WHISPER_CACHE_MARKER = f"{WHISPER_TAG} {WHISPER_COMMIT}"
WINDOWS_VC_RUNTIME_DLLS = (
    "MSVCP140.dll",
    "VCRUNTIME140.dll",
    "VCRUNTIME140_1.dll",
)
WINDOWS_VC_REDIST_URL = "https://aka.ms/vc14/vc_redist.x64.exe"
IMAGEIO_FFMPEG_BINARY_NAMES = {
    "windows-x86_64": "ffmpeg-win-x86_64-v7.1.exe",
    "darwin-arm64": "ffmpeg-macos-aarch64-v7.1",
    "darwin-x86_64": "ffmpeg-macos-x86_64-v7.1",
}


def cache_paths(
    cache_root: Path,
    system_name: str | None = None,
) -> dict[str, Path]:
    return {
        "cli": cache_root / "bin" / executable_name("whisper-cli", system_name),
        "cli_version": cache_root / "bin" / "whisper-cli.version",
        "model": cache_root / "models" / MODEL_NAME,
        "python": cache_root / "python",
        "tools": cache_root / "python-tools",
    }


def whisper_cli_capable(path: Path, system_name: str | None = None) -> bool:
    if not executable_ready(path, system_name=system_name):
        return False
    try:
        result = run_text([str(path), "--help"], timeout=15)
    except RuntimeError:
        return False
    if result.returncode != 0:
        return False
    help_text = result.stdout + "\n" + result.stderr
    required_options = ("-ojf", "-np", "-nt", "-l", "-m")
    return all(option in help_text for option in required_options)


def _cli_marker_path(path: Path) -> Path:
    return path.parent / "whisper-cli.version"


def _cli_marker_matches(path: Path) -> bool:
    marker = _cli_marker_path(path)
    try:
        return marker.read_text(encoding="utf-8").strip() == WHISPER_CACHE_MARKER
    except OSError:
        return False


def _write_cli_marker(path: Path) -> None:
    marker = _cli_marker_path(path)
    partial = marker.with_suffix(marker.suffix + ".partial")
    partial.write_text(WHISPER_CACHE_MARKER + "\n", encoding="utf-8")
    os.replace(partial, marker)


def whisper_cli_ready(path: Path, system_name: str | None = None) -> bool:
    return whisper_cli_capable(path, system_name) and _cli_marker_matches(path)


def _cached_ffmpeg_path(python_root: Path) -> Path | None:
    info = current_platform()
    binary_name = IMAGEIO_FFMPEG_BINARY_NAMES.get(info.key)
    if not python_root.is_dir() or binary_name is None:
        return None
    metadata = (
        python_root
        / f"imageio_ffmpeg-{IMAGEIO_FFMPEG_VERSION}.dist-info"
        / "METADATA"
    )
    try:
        metadata_text = metadata.read_text(encoding="utf-8")
    except OSError:
        return None
    if f"\nVersion: {IMAGEIO_FFMPEG_VERSION}\n" not in "\n" + metadata_text:
        return None
    candidate = python_root / "imageio_ffmpeg" / "binaries" / binary_name
    return candidate.resolve() if candidate.is_file() else None


def _ffmpeg_works(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        result = run_text([str(path), "-version"], timeout=15)
    except RuntimeError:
        return False
    return result.returncode == 0


def find_cached_ffmpeg(python_root: Path) -> Path | None:
    cached = _cached_ffmpeg_path(python_root)
    if cached and _ffmpeg_works(cached):
        return cached
    return None


def pip_available() -> bool:
    try:
        result = run_text(
            [sys.executable, "-m", "pip", "--version"],
            timeout=20,
        )
    except RuntimeError:
        return False
    return result.returncode == 0


def _require_pip() -> None:
    if not pip_available():
        raise RuntimeError(
            "当前 Python 缺少 pip；请先为这个 Python 安装 pip 后重试"
        )


def windows_vc_runtime_missing(
    cli_dir: Path | None = None,
    system_name: str | None = None,
) -> tuple[str, ...]:
    if not current_platform(system_name=system_name).is_windows:
        return ()
    loader = getattr(ctypes, "WinDLL", None)
    missing: list[str] = []
    for name in WINDOWS_VC_RUNTIME_DLLS:
        if cli_dir is not None and (cli_dir / name).is_file():
            continue
        if loader is None:
            missing.append(name)
            continue
        try:
            loader(name)
        except OSError:
            missing.append(name)
    return tuple(missing)


def check_status(cache_root: Path) -> tuple[bool, list[str]]:
    info = current_platform()
    paths = cache_paths(cache_root, info.system)
    messages: list[str] = []
    messages.append(
        f"平台：{info.key}（{'支持目标' if info.supported else '尚未支持'}）"
    )

    python_ok = sys.version_info >= MINIMUM_PYTHON
    version = ".".join(str(part) for part in sys.version_info[:3])
    messages.append(
        f"Python：{version}（{'可用' if python_ok else '需要 3.9 或更高版本'}）"
    )
    messages.append(
        f"pip：{'可用' if pip_available() else '缺失；首次安装依赖前需要准备'}"
    )

    cli_ok = whisper_cli_ready(paths["cli"], info.system)
    cli_capable = cli_ok or whisper_cli_capable(paths["cli"], info.system)
    if cli_ok:
        cli_status = f"可用 {WHISPER_TAG}"
    elif cli_capable:
        cli_status = f"可运行但版本未登记；需要授权升级到 {WHISPER_TAG}"
    else:
        cli_status = "缺失或无法运行"
    messages.append(f"语音识别程序：{cli_status}")
    vc_runtime_ok = True
    if info.is_windows:
        missing_runtime = windows_vc_runtime_missing(
            paths["cli"].parent,
            info.system,
        )
        vc_runtime_ok = not missing_runtime
        messages.append(
            "Windows VC++ Runtime："
            + (
                "可用"
                if vc_runtime_ok
                else "缺失 " + "、".join(missing_runtime)
            )
        )
    if info.system == "darwin" and not cli_ok:
        git_program = find_working_program(("git",))
        compiler = find_working_program(("clang", "cc"))
        cmake_program = find_cmake(paths["tools"])
        generator = find_cmake_generator()
        messages.append(
            f"macOS 构建 Git：{'可用 ' + str(git_program) if git_program else '缺失'}"
        )
        messages.append(
            f"macOS 构建编译器：{'可用 ' + str(compiler) if compiler else '缺失'}"
        )
        messages.append(
            "macOS 构建 CMake："
            + (
                "可用 " + str(cmake_program)
                if cmake_program
                else "未安装；授权安装时将准备固定版本"
            )
        )
        messages.append(
            "macOS 构建生成器："
            + (generator if generator else "缺少 Ninja 或 make")
        )

    model_ok = (
        paths["model"].is_file()
        and sha256(paths["model"]) == MODEL_SHA256
    )
    messages.append(f"中文识别模型：{'可用' if model_ok else '缺失或校验失败'}")

    ffmpeg = find_cached_ffmpeg(paths["python"])
    ffmpeg_ok = ffmpeg is not None
    messages.append(
        f"FFmpeg：{'可用 ' + str(ffmpeg) if ffmpeg_ok else '缺失或无法运行'}"
    )
    return (
        info.supported
        and python_ok
        and cli_ok
        and vc_runtime_ok
        and model_ok
        and ffmpeg_ok,
        messages,
    )


def install_ffmpeg(python_root: Path) -> None:
    if find_cached_ffmpeg(python_root):
        return
    info = current_platform()
    wheel_info = IMAGEIO_FFMPEG_WHEELS.get(info.key)
    if wheel_info is None:
        raise RuntimeError(f"当前平台没有固定的 FFmpeg 安装包：{info.key}")
    wheel_name, wheel_url, wheel_sha256 = wheel_info
    _require_pip()
    wheel_path = python_root.parent / "downloads" / wheel_name
    download_verified(wheel_url, wheel_path, wheel_sha256)
    python_root.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--no-deps",
            "--no-index",
            "--target",
            str(python_root),
            str(wheel_path),
        ],
        timeout=600,
    )
    if not find_cached_ffmpeg(python_root):
        raise RuntimeError("FFmpeg 安装后仍不可用")


def _program_version_works(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        result = run_text([str(path), "--version"], timeout=20)
    except RuntimeError:
        return False
    return result.returncode == 0


def _cmake_works(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        result = run_text([str(path), "--version"], timeout=20)
    except RuntimeError:
        return False
    if result.returncode != 0:
        return False
    output = result.stdout + "\n" + result.stderr
    match = re.search(r"cmake version (\d+)\.(\d+)\.(\d+)", output)
    if not match:
        return False
    version = tuple(int(part) for part in match.groups())
    return version >= CMAKE_MINIMUM_VERSION


def _cached_cmake_path(python_root: Path) -> Path | None:
    if not python_root.is_dir():
        return None
    metadata = (
        python_root
        / f"cmake-{CMAKE_VERSION}.dist-info"
        / "METADATA"
    )
    try:
        metadata_text = metadata.read_text(encoding="utf-8")
    except OSError:
        return None
    if f"\nVersion: {CMAKE_VERSION}\n" not in "\n" + metadata_text:
        return None
    candidate = python_root / "cmake" / "data" / "bin" / "cmake"
    return candidate.resolve() if _cmake_works(candidate) else None


def find_cmake(python_root: Path) -> Path | None:
    system_cmake = shutil.which("cmake")
    if system_cmake:
        candidate = Path(system_cmake)
        if _cmake_works(candidate):
            return candidate.resolve()
    return _cached_cmake_path(python_root)


def install_cmake(python_root: Path) -> Path:
    existing = find_cmake(python_root)
    if existing:
        return existing
    info = current_platform()
    if info.system != "darwin":
        raise RuntimeError(f"当前平台不需要本地 CMake 安装包：{info.key}")

    _require_pip()
    wheel_path = python_root.parent / "downloads" / CMAKE_MACOS_WHEEL_NAME
    download_verified(
        CMAKE_MACOS_WHEEL_URL,
        wheel_path,
        CMAKE_MACOS_WHEEL_SHA256,
    )
    python_root.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--no-deps",
            "--no-index",
            "--target",
            str(python_root),
            str(wheel_path),
        ],
        timeout=600,
    )
    installed = find_cmake(python_root)
    if installed is None:
        raise RuntimeError("CMake 安装后仍不可用")
    return installed


def find_working_program(names: tuple[str, ...]) -> Path | None:
    for name in names:
        located = shutil.which(name)
        if not located:
            continue
        candidate = Path(located)
        if _program_version_works(candidate):
            return candidate.resolve()
    return None


def find_cmake_generator() -> str | None:
    if find_working_program(("ninja",)):
        return "Ninja"
    if find_working_program(("make",)):
        return "Unix Makefiles"
    return None


def _find_whisper_program(root: Path) -> Path | None:
    preferred = [
        "whisper-cli.exe",
        "main.exe",
        "whisper-cli",
        "main",
    ]
    by_name: dict[str, list[Path]] = {name: [] for name in preferred}
    for path in root.rglob("*"):
        if path.is_file() and path.name in by_name:
            by_name[path.name].append(path)
    for name in preferred:
        candidates = sorted(by_name[name], key=lambda path: (len(path.parts), str(path)))
        if candidates:
            return candidates[0]
    return None


def _reuse_build_matches(source: Path, reuse_root: Path) -> bool:
    if _cli_marker_matches(source):
        return True

    git_program = find_working_program(("git",))
    if git_program is None:
        return False

    checked: set[Path] = set()
    for candidate in (source.parent, reuse_root):
        candidate = candidate.resolve()
        if candidate in checked:
            continue
        checked.add(candidate)
        try:
            result = run_text(
                [
                    str(git_program),
                    "-C",
                    str(candidate),
                    "rev-parse",
                    "HEAD",
                ],
                timeout=20,
            )
        except RuntimeError:
            continue
        if result.returncode == 0 and result.stdout.strip() == WHISPER_COMMIT:
            return True
    return False


def _copy_whisper_runtime(source: Path, destination: Path, system_name: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if system_name == "windows":
        for dependency in source.parent.iterdir():
            if dependency.is_file() and dependency.suffix.lower() == ".dll":
                shutil.copy2(dependency, destination.parent / dependency.name)
    for license_name in ("LICENSE", "LICENSE.txt"):
        license_path = source.parent / license_name
        if license_path.is_file():
            shutil.copy2(license_path, destination.parent / license_path.name)
    make_executable(destination, system_name=system_name)


def _install_windows_prebuilt(cli_path: Path, cache_root: Path) -> None:
    archive = cache_root / "downloads" / WINDOWS_X64_ARCHIVE_NAME
    download_verified(
        WINDOWS_X64_ARCHIVE_URL,
        archive,
        WINDOWS_X64_ARCHIVE_SHA256,
    )
    with tempfile.TemporaryDirectory(prefix="fcs-rename-whisper-") as temp:
        extracted = Path(temp) / "whisper"
        safe_extract_zip(archive, extracted)
        source = _find_whisper_program(extracted)
        if source is None:
            raise RuntimeError("Windows Whisper 安装包中没有找到 whisper-cli.exe")
        _copy_whisper_runtime(source, cli_path, "windows")


def _build_whisper_cli(
    cli_path: Path,
    git_program: Path,
    cmake_program: Path,
    cmake_generator: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="fcs-rename-build-") as temp:
        source_root = Path(temp) / "whisper.cpp"
        build_root = source_root / "build"
        run_checked(
            [
                str(git_program),
                "init",
                str(source_root),
            ],
            timeout=60,
        )
        run_checked(
            [
                str(git_program),
                "-C",
                str(source_root),
                "remote",
                "add",
                "origin",
                WHISPER_REPO,
            ],
            timeout=60,
        )
        run_checked(
            [
                str(git_program),
                "-C",
                str(source_root),
                "fetch",
                "--depth",
                "1",
                "origin",
                WHISPER_COMMIT,
            ],
            timeout=300,
        )
        run_checked(
            [
                str(git_program),
                "-C",
                str(source_root),
                "checkout",
                "--detach",
                WHISPER_COMMIT,
            ],
            timeout=60,
        )
        run_checked(
            [
                str(cmake_program),
                "-G",
                cmake_generator,
                "-S",
                str(source_root),
                "-B",
                str(build_root),
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_SHARED_LIBS=OFF",
                "-DWHISPER_BUILD_TESTS=OFF",
            ],
            timeout=300,
        )
        run_checked(
            [
                str(cmake_program),
                "--build",
                str(build_root),
                "--config",
                "Release",
                "--target",
                "whisper-cli",
                "--parallel",
                str(max(1, min(os.cpu_count() or 2, 4))),
            ],
            timeout=1800,
        )
        source = _find_whisper_program(build_root)
        if source is None:
            raise RuntimeError("Whisper 编译完成后没有找到命令行程序")
        _copy_whisper_runtime(source, cli_path, "darwin")


def install_cli(
    cli_path: Path,
    cache_root: Path,
    reuse_build: Path | None,
) -> None:
    info = current_platform()
    if whisper_cli_ready(cli_path, info.system):
        return
    cli_path.parent.mkdir(parents=True, exist_ok=True)

    if reuse_build:
        source = _find_whisper_program(reuse_build)
        if source is None:
            raise RuntimeError("复用目录中没有找到 whisper-cli 或 main")
        if not _reuse_build_matches(source, reuse_build):
            raise RuntimeError(
                "复用的 Whisper 版本无法确认；"
                f"需要 Git HEAD 为 {WHISPER_COMMIT}，"
                f"或版本标记为 {WHISPER_CACHE_MARKER}"
            )
        _copy_whisper_runtime(source, cli_path, info.system)
    elif info.key == "windows-x86_64":
        missing_runtime = windows_vc_runtime_missing(cli_path.parent, info.system)
        if missing_runtime:
            raise RuntimeError(
                "Windows 缺少 Microsoft Visual C++ Runtime："
                + "、".join(missing_runtime)
                + f"；请从微软官方安装 {WINDOWS_VC_REDIST_URL}"
            )
        _install_windows_prebuilt(cli_path, cache_root)
    elif info.key in {"darwin-arm64", "darwin-x86_64"}:
        git_program = find_working_program(("git",))
        compiler = find_working_program(("clang", "cc"))
        if git_program is None:
            raise RuntimeError("macOS 构建工具缺失：Git")
        if compiler is None:
            raise RuntimeError(
                "macOS 构建工具缺失：C/C++ 编译器；"
                "请先安装 Apple Command Line Tools"
            )
        generator = find_cmake_generator()
        if generator is None:
            raise RuntimeError(
                "macOS 构建工具缺失：需要 Ninja 或 Apple Command Line Tools 中的 make"
            )
        tools_root = cache_paths(cache_root, info.system)["tools"]
        cmake_program = install_cmake(tools_root)
        _build_whisper_cli(
            cli_path,
            git_program,
            cmake_program,
            generator,
        )
    else:
        raise RuntimeError(f"当前平台尚未支持自动安装：{info.key}")

    if not whisper_cli_capable(cli_path, info.system):
        raise RuntimeError("Whisper 安装后仍无法运行")
    _write_cli_marker(cli_path)
    if not whisper_cli_ready(cli_path, info.system):
        raise RuntimeError("Whisper 版本标记写入失败")


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
        actual = sha256(partial)
        if actual != MODEL_SHA256:
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"中文模型校验失败：期望 {MODEL_SHA256}，实际 {actual}"
            )
        os.replace(partial, model_path)
        return

    download_verified(MODEL_URL, model_path, MODEL_SHA256)


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
    info = current_platform()
    if not info.supported:
        print(f"安装停止：当前平台尚未支持：{info.key}", file=sys.stderr)
        return 1
    if sys.version_info < MINIMUM_PYTHON:
        print("安装停止：需要 Python 3.9 或更高版本。", file=sys.stderr)
        return 1

    paths = cache_paths(cache_root, info.system)
    try:
        install_ffmpeg(paths["python"])
        install_cli(paths["cli"], cache_root, reuse_build)
        download_model(paths["model"], reuse_build)
    except RuntimeError as exc:
        print(f"安装停止：{exc}", file=sys.stderr)
        return 1

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

    status = sub.add_parser("status", help="check the local cache without downloads")
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
