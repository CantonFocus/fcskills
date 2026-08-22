#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import hashlib
import html
import locale
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys
import time
from typing import Mapping, Sequence
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile


SUPPORTED_PLATFORM_KEYS = {
    "darwin-arm64",
    "darwin-x86_64",
    "windows-x86_64",
}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
WINDOWS_INVALID_CHARS = set('<>:"/\\|?*')


@dataclass(frozen=True)
class PlatformInfo:
    system: str
    machine: str
    key: str
    supported: bool

    @property
    def is_windows(self) -> bool:
        return self.system == "windows"

    @property
    def executable_suffix(self) -> str:
        return ".exe" if self.is_windows else ""


def current_platform(
    system_name: str | None = None,
    machine_name: str | None = None,
) -> PlatformInfo:
    system_value = (system_name or platform.system()).strip().lower()
    machine_value = (machine_name or platform.machine()).strip().lower()
    system_aliases = {
        "darwin": "darwin",
        "macos": "darwin",
        "windows": "windows",
        "linux": "linux",
    }
    machine_aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    system_value = system_aliases.get(system_value, system_value or "unknown")
    machine_value = machine_aliases.get(machine_value, machine_value or "unknown")
    key = f"{system_value}-{machine_value}"
    return PlatformInfo(
        system=system_value,
        machine=machine_value,
        key=key,
        supported=key in SUPPORTED_PLATFORM_KEYS,
    )


def default_cache_root(
    system_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    values = os.environ if environ is None else environ
    configured = values.get("FCS_RENAME_CACHE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    info = current_platform(system_name=system_name)
    home_dir = (home or Path.home()).expanduser()
    if info.system == "darwin":
        return (home_dir / "Library" / "Caches" / "fcs-rename").resolve()
    if info.system == "windows":
        local_app_data = values.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data).expanduser() if local_app_data else home_dir / "AppData" / "Local"
        return (base / "fcs-rename").resolve()

    cache_home = values.get("XDG_CACHE_HOME", "").strip()
    base = Path(cache_home).expanduser() if cache_home else home_dir / ".cache"
    return (base / "fcs-rename").resolve()


def executable_name(base_name: str, system_name: str | None = None) -> str:
    info = current_platform(system_name=system_name)
    suffix = info.executable_suffix
    return base_name if not suffix or base_name.lower().endswith(suffix) else base_name + suffix


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def executable_ready(path: Path, system_name: str | None = None) -> bool:
    if not path.is_file():
        return False
    if current_platform(system_name=system_name).is_windows:
        return path.suffix.lower() == ".exe"
    return os.access(path, os.X_OK)


def make_executable(path: Path, system_name: str | None = None) -> None:
    if current_platform(system_name=system_name).is_windows:
        return
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def rename_no_replace(
    source: Path,
    destination: Path,
    system_name: str | None = None,
) -> None:
    """Atomically rename a file without replacing an existing destination."""
    info = current_platform(system_name=system_name)
    if info.is_windows:
        # Windows os.rename() fails when destination already exists.
        os.rename(source, destination)
        return
    if info.system != "darwin":
        raise RuntimeError(f"当前平台不支持无覆盖重命名：{info.key}")

    libc = ctypes.CDLL(None, use_errno=True)
    renamex_np = getattr(libc, "renamex_np", None)
    if renamex_np is None:
        raise RuntimeError("当前 macOS 不支持无覆盖重命名")
    renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex_np.restype = ctypes.c_int
    rename_exclusive = 0x00000004
    result = renamex_np(
        os.fsencode(source),
        os.fsencode(destination),
        rename_exclusive,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            str(destination),
        )


def _decode_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        preferred = locale.getpreferredencoding(False) or "utf-8"
        if preferred.lower().replace("_", "-") not in {"utf-8", "utf8"}:
            try:
                return value.decode(preferred)
            except (LookupError, UnicodeDecodeError):
                pass
        return value.decode("utf-8", errors="replace")


def configure_utf8_stdio() -> None:
    """Keep Chinese CLI output stable when Windows redirects stdout or stderr."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def run_text(
    command: Sequence[str],
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"缺少程序：{command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"程序运行超时：{command[0]}") from exc
    except OSError as exc:
        raise RuntimeError(f"无法运行程序 {command[0]}：{exc}") from exc
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=_decode_output(result.stdout),
        stderr=_decode_output(result.stderr),
    )


def run_checked(
    command: Sequence[str],
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    result = run_text(command, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"退出码 {result.returncode}"
        raise RuntimeError(f"程序执行失败：{command[0]}：{detail}")
    return result


def download_verified(
    url: str,
    destination: Path,
    expected_sha256: str,
    attempts: int = 3,
    timeout: float = 60.0,
) -> Path:
    if destination.is_file() and sha256(destination) == expected_sha256:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        partial.unlink(missing_ok=True)
        request = Request(url, headers={"User-Agent": "fcskills/fcs-rename"})
        try:
            with urlopen(request, timeout=timeout) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            actual = sha256(partial)
            if actual != expected_sha256:
                raise RuntimeError(
                    f"下载文件校验失败：期望 {expected_sha256}，实际 {actual}"
                )
            os.replace(partial, destination)
            return destination
        except (HTTPError, URLError, OSError, RuntimeError) as exc:
            last_error = exc
            partial.unlink(missing_ok=True)
            if attempt + 1 < max(1, attempts):
                time.sleep(2**attempt)
    raise RuntimeError(f"下载失败：{url}：{last_error}") from last_error


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            normalized = member.filename.replace("\\", "/")
            relative = PurePosixPath(normalized)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"压缩包包含不安全路径：{member.filename}")
            if relative.parts and ":" in relative.parts[0]:
                raise RuntimeError(f"压缩包包含不安全路径：{member.filename}")
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError(f"压缩包包含不支持的符号链接：{member.filename}")

            target = destination.joinpath(*relative.parts)
            target_resolved = target.resolve()
            if target_resolved != destination_root and destination_root not in target_resolved.parents:
                raise RuntimeError(f"压缩包路径越界：{member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def relative_file_url(path: Path, base: Path) -> str:
    relative = path.resolve().relative_to(base.resolve())
    return html.escape(quote(relative.as_posix(), safe="/"), quote=True)


def portable_filename_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def portable_filename_error(name: str) -> str | None:
    if not name or name in {".", ".."}:
        return "文件名为空或属于保留路径"
    invalid = sorted({character for character in name if character in WINDOWS_INVALID_CHARS})
    if invalid:
        return "包含 Windows 禁用字符：" + " ".join(invalid)
    if any(ord(character) < 32 for character in name):
        return "包含控制字符"
    if name.endswith((" ", ".")):
        return "不能以空格或句点结尾"
    stem = name.split(".", 1)[0].rstrip(" .").upper()
    if stem in WINDOWS_RESERVED_NAMES:
        return f"使用了 Windows 保留名称：{stem}"
    normalized = unicodedata.normalize("NFC", name)
    if len(normalized.encode("utf-8")) > 255:
        return "UTF-8 文件名超过 255 字节"
    if len(normalized.encode("utf-16-le")) // 2 > 255:
        return "Windows 文件名超过 255 个 UTF-16 单元"
    return None
