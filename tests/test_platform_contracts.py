from __future__ import annotations

from contextlib import nullcontext
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "fcs-rename" / "scripts"
MODULE_PATH = SCRIPT_DIR / "prepare_local_asr.py"
sys.path.insert(0, str(SCRIPT_DIR))


def load_module():
    spec = importlib.util.spec_from_file_location("prepare_local_asr_contract", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载依赖准备模块")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


prepare = load_module()


class PlatformContractTests(unittest.TestCase):
    def test_cmake_version_check_rejects_too_old_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            cmake = Path(temp) / "cmake"
            cmake.write_bytes(b"binary")
            too_old = subprocess.CompletedProcess(
                [str(cmake), "--version"],
                0,
                "cmake version 3.13.9\n",
                "",
            )
            supported = subprocess.CompletedProcess(
                [str(cmake), "--version"],
                0,
                "cmake version 3.27.7\n",
                "",
            )
            with patch.object(prepare, "run_text", return_value=too_old):
                self.assertFalse(prepare._cmake_works(cmake))
            with patch.object(prepare, "run_text", return_value=supported):
                self.assertTrue(prepare._cmake_works(cmake))

    def test_whisper_without_required_json_capability_is_rejected(self):
        old_help = subprocess.CompletedProcess(
            ["whisper-cli", "--help"],
            0,
            "usage: whisper-cli -m model -otxt input.wav",
            "",
        )
        with (
            patch.object(prepare, "executable_ready", return_value=True),
            patch.object(prepare, "run_text", return_value=old_help),
        ):
            ready = prepare.whisper_cli_capable(Path("whisper-cli"), "Darwin")

        self.assertFalse(ready)

    def test_whisper_with_required_capabilities_is_ready(self):
        current_help = subprocess.CompletedProcess(
            ["whisper-cli", "--help"],
            0,
            "options: -m -l -nt -np -ojf",
            "",
        )
        with (
            patch.object(prepare, "executable_ready", return_value=True),
            patch.object(prepare, "run_text", return_value=current_help),
        ):
            ready = prepare.whisper_cli_capable(Path("whisper-cli"), "Darwin")

        self.assertTrue(ready)

    def test_whisper_ready_requires_pinned_cache_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            cli = Path(temp) / "whisper-cli"
            cli.write_bytes(b"binary")
            with patch.object(prepare, "whisper_cli_capable", return_value=True):
                self.assertFalse(prepare.whisper_cli_ready(cli, "Darwin"))
                prepare._write_cli_marker(cli)
                self.assertTrue(prepare.whisper_cli_ready(cli, "Darwin"))

            marker = cli.parent / "whisper-cli.version"
            self.assertEqual(
                marker.read_text(encoding="utf-8").strip(),
                prepare.WHISPER_CACHE_MARKER,
            )

    def test_cache_paths_use_platform_executable_names(self):
        root = Path("/cache/fcs-rename")

        windows = prepare.cache_paths(root, "Windows")
        mac = prepare.cache_paths(root, "Darwin")

        self.assertEqual(windows["cli"].name, "whisper-cli.exe")
        self.assertEqual(mac["cli"].name, "whisper-cli")

    def test_status_is_offline_and_reports_platform_and_python(self):
        platform_info = prepare.current_platform("Windows", "AMD64")
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(prepare, "current_platform", return_value=platform_info),
                patch.object(prepare, "whisper_cli_ready", return_value=False),
                patch.object(prepare, "find_cached_ffmpeg", return_value=None),
                patch.object(prepare, "pip_available", return_value=True),
                patch.object(
                    prepare,
                    "windows_vc_runtime_missing",
                    return_value=(),
                ),
                patch.object(prepare, "download_verified") as downloader,
            ):
                ready, messages = prepare.check_status(Path(temp))

        self.assertFalse(ready)
        self.assertIn("平台：windows-x86_64", messages[0])
        self.assertTrue(any(message.startswith("Python：") for message in messages))
        downloader.assert_not_called()

    def test_ffmpeg_install_uses_current_python_and_binary_wheel(self):
        platform_info = prepare.current_platform("Windows", "AMD64")
        with tempfile.TemporaryDirectory() as temp:
            python_root = Path(temp) / "python cache"
            with (
                patch.object(prepare, "current_platform", return_value=platform_info),
                patch.object(
                    prepare,
                    "find_cached_ffmpeg",
                    side_effect=[None, Path(temp) / "ffmpeg"],
                ),
                patch.object(prepare, "pip_available", return_value=True),
                patch.object(prepare, "download_verified") as downloader,
                patch.object(prepare, "run_checked") as runner,
            ):
                prepare.install_ffmpeg(python_root)

        command = runner.call_args.args[0]
        self.assertEqual(command[:4], [sys.executable, "-m", "pip", "install"])
        self.assertIn("--no-deps", command)
        self.assertIn("--no-index", command)
        self.assertTrue(command[-1].endswith("imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl"))
        self.assertNotIn("curl", command)
        downloader.assert_called_once()
        self.assertEqual(len(downloader.call_args.args[2]), 64)

    def test_windows_x64_uses_verified_official_prebuilt_path(self):
        platform_info = prepare.current_platform("Windows", "AMD64")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli = root / "bin" / "whisper-cli.exe"
            with (
                patch.object(prepare, "current_platform", return_value=platform_info),
                patch.object(prepare, "whisper_cli_ready", side_effect=[False, True]),
                patch.object(prepare, "whisper_cli_capable", return_value=True),
                patch.object(
                    prepare,
                    "windows_vc_runtime_missing",
                    return_value=(),
                ),
                patch.object(prepare, "_write_cli_marker"),
                patch.object(prepare, "_install_windows_prebuilt") as installer,
                patch.object(prepare, "_build_whisper_cli") as builder,
            ):
                prepare.install_cli(cli, root, None)

        installer.assert_called_once_with(cli, root)
        builder.assert_not_called()
        self.assertEqual(prepare.WHISPER_TAG, "v1.9.2")
        self.assertEqual(len(prepare.WINDOWS_X64_ARCHIVE_SHA256), 64)

    def test_windows_install_stops_before_download_when_vc_runtime_is_missing(self):
        platform_info = prepare.current_platform("Windows", "AMD64")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli = root / "bin" / "whisper-cli.exe"
            with (
                patch.object(prepare, "current_platform", return_value=platform_info),
                patch.object(prepare, "whisper_cli_ready", return_value=False),
                patch.object(
                    prepare,
                    "windows_vc_runtime_missing",
                    return_value=("MSVCP140.dll",),
                ),
                patch.object(prepare, "_install_windows_prebuilt") as installer,
            ):
                with self.assertRaisesRegex(RuntimeError, r"Visual C\+\+ Runtime"):
                    prepare.install_cli(cli, root, None)

        installer.assert_not_called()

    def test_pinned_ffmpeg_cache_is_found_without_printing_unicode_path(self):
        info = prepare.current_platform()
        binary_name = prepare.IMAGEIO_FFMPEG_BINARY_NAMES.get(info.key)
        if binary_name is None:
            self.skipTest("当前测试平台不在公开支持范围")
        with tempfile.TemporaryDirectory() as temp:
            python_root = Path(temp) / "中文 缓存"
            metadata = (
                python_root
                / f"imageio_ffmpeg-{prepare.IMAGEIO_FFMPEG_VERSION}.dist-info"
                / "METADATA"
            )
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                f"Metadata-Version: 2.1\nVersion: {prepare.IMAGEIO_FFMPEG_VERSION}\n",
                encoding="utf-8",
            )
            binary = python_root / "imageio_ffmpeg" / "binaries" / binary_name
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"binary")
            with patch.object(prepare, "_ffmpeg_works", return_value=True):
                found = prepare.find_cached_ffmpeg(python_root)

        self.assertEqual(found, binary.resolve())

    def test_system_ffmpeg_does_not_bypass_pinned_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(prepare, "_cached_ffmpeg_path", return_value=None),
                patch.object(prepare.shutil, "which", return_value="ffmpeg") as finder,
            ):
                self.assertIsNone(prepare.find_cached_ffmpeg(Path(temp)))
        finder.assert_not_called()

    def test_ffmpeg_install_stops_before_download_when_pip_is_missing(self):
        platform_info = prepare.current_platform("Windows", "AMD64")
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(prepare, "current_platform", return_value=platform_info),
                patch.object(prepare, "find_cached_ffmpeg", return_value=None),
                patch.object(prepare, "pip_available", return_value=False),
                patch.object(prepare, "download_verified") as downloader,
            ):
                with self.assertRaisesRegex(RuntimeError, "缺少 pip"):
                    prepare.install_ffmpeg(Path(temp) / "python")
        downloader.assert_not_called()

    def test_reuse_build_accepts_exact_pinned_git_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "build" / "bin" / "whisper-cli"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"binary")
            exact_commit = subprocess.CompletedProcess(
                ["git", "rev-parse", "HEAD"],
                0,
                prepare.WHISPER_COMMIT + "\n",
                "",
            )
            with (
                patch.object(prepare, "_cli_marker_matches", return_value=False),
                patch.object(
                    prepare,
                    "find_working_program",
                    return_value=Path("/usr/bin/git"),
                ),
                patch.object(prepare, "run_text", return_value=exact_commit),
            ):
                self.assertTrue(prepare._reuse_build_matches(source, root))

    def test_macos_build_uses_cmake_without_make(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            destination = temp_root / "installed" / "whisper-cli"
            commands: list[list[str]] = []

            def fake_run(command, **_kwargs):
                commands.append(command)
                if command[:2] == ["cmake", "--build"]:
                    program = temp_root / "whisper.cpp" / "build" / "bin" / "whisper-cli"
                    program.parent.mkdir(parents=True, exist_ok=True)
                    program.write_bytes(b"binary")

            with (
                patch.object(
                    prepare.tempfile,
                    "TemporaryDirectory",
                    return_value=nullcontext(str(temp_root)),
                ),
                patch.object(prepare, "run_checked", side_effect=fake_run),
            ):
                prepare._build_whisper_cli(
                    destination,
                    Path("git"),
                    Path("cmake"),
                    "Ninja",
                )
            self.assertTrue(destination.is_file())
            self.assertEqual(commands[0][0:2], ["git", "init"])
            self.assertTrue(any(prepare.WHISPER_COMMIT in command for command in commands))
            cmake_commands = [command for command in commands if command[0] == "cmake"]
            self.assertEqual(len(cmake_commands), 2)
            self.assertIn("Ninja", cmake_commands[0])
            self.assertEqual(cmake_commands[1][0:2], ["cmake", "--build"])
            self.assertIn("whisper-cli", cmake_commands[1])
            self.assertFalse(any(command[0] == "make" for command in commands))

    def test_macos_install_prepares_pinned_cmake_before_build(self):
        platform_info = prepare.current_platform("Darwin", "arm64")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli = root / "bin" / "whisper-cli"
            with (
                patch.object(prepare, "current_platform", return_value=platform_info),
                patch.object(prepare, "whisper_cli_ready", side_effect=[False, True]),
                patch.object(prepare, "whisper_cli_capable", return_value=True),
                patch.object(prepare, "_write_cli_marker"),
                patch.object(
                    prepare,
                    "find_working_program",
                    side_effect=[Path("/usr/bin/git"), Path("/usr/bin/clang")],
                ),
                patch.object(
                    prepare,
                    "find_cmake_generator",
                    return_value="Ninja",
                ),
                patch.object(
                    prepare,
                    "install_cmake",
                    return_value=Path("/cache/cmake"),
                ) as cmake_installer,
                patch.object(prepare, "_build_whisper_cli") as builder,
            ):
                prepare.install_cli(cli, root, None)

        cmake_installer.assert_called_once_with(root / "python-tools")
        builder.assert_called_once_with(
            cli,
            Path("/usr/bin/git"),
            Path("/cache/cmake"),
            "Ninja",
        )

    def test_cmake_install_uses_verified_universal_macos_wheel(self):
        platform_info = prepare.current_platform("Darwin", "arm64")
        with tempfile.TemporaryDirectory() as temp:
            tools_root = Path(temp) / "python-tools"
            with (
                patch.object(prepare, "current_platform", return_value=platform_info),
                patch.object(
                    prepare,
                    "find_cmake",
                    side_effect=[None, Path(temp) / "cmake"],
                ),
                patch.object(prepare, "pip_available", return_value=True),
                patch.object(prepare, "download_verified") as downloader,
                patch.object(prepare, "run_checked") as runner,
            ):
                installed = prepare.install_cmake(tools_root)

        self.assertEqual(installed, Path(temp) / "cmake")
        downloader.assert_called_once()
        self.assertEqual(downloader.call_args.args[2], prepare.CMAKE_MACOS_WHEEL_SHA256)
        command = runner.call_args.args[0]
        self.assertIn("--no-index", command)
        self.assertTrue(command[-1].endswith(prepare.CMAKE_MACOS_WHEEL_NAME))

    def test_pinned_cmake_cache_is_found_without_printing_unicode_path(self):
        with tempfile.TemporaryDirectory() as temp:
            tools_root = Path(temp) / "中文 工具缓存"
            metadata = (
                tools_root
                / f"cmake-{prepare.CMAKE_VERSION}.dist-info"
                / "METADATA"
            )
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                f"Metadata-Version: 2.1\nVersion: {prepare.CMAKE_VERSION}\n",
                encoding="utf-8",
            )
            cmake = tools_root / "cmake" / "data" / "bin" / "cmake"
            cmake.parent.mkdir(parents=True)
            cmake.write_bytes(b"binary")
            with patch.object(prepare, "_cmake_works", return_value=True):
                found = prepare._cached_cmake_path(tools_root)

        self.assertEqual(found, cmake.resolve())


if __name__ == "__main__":
    unittest.main()
