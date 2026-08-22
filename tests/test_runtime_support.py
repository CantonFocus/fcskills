from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "fcs-rename" / "scripts"
MODULE_PATH = SCRIPT_DIR / "runtime_support.py"


def load_module():
    spec = importlib.util.spec_from_file_location("runtime_support", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载运行支持模块")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runtime = load_module()


class RuntimeSupportTests(unittest.TestCase):
    def test_normalizes_supported_platforms(self):
        windows = runtime.current_platform("Windows", "AMD64")
        mac_arm = runtime.current_platform("Darwin", "arm64")
        mac_intel = runtime.current_platform("macOS", "x86_64")

        self.assertEqual(windows.key, "windows-x86_64")
        self.assertTrue(windows.supported)
        self.assertEqual(mac_arm.key, "darwin-arm64")
        self.assertTrue(mac_arm.supported)
        self.assertEqual(mac_intel.key, "darwin-x86_64")
        self.assertTrue(mac_intel.supported)

    def test_uses_platform_standard_cache_roots(self):
        windows = runtime.default_cache_root(
            system_name="Windows",
            environ={"LOCALAPPDATA": "/platform/localappdata"},
            home=Path("/home/person"),
        )
        mac = runtime.default_cache_root(
            system_name="Darwin",
            environ={},
            home=Path("/home/person"),
        )

        self.assertEqual(windows, Path("/platform/localappdata/fcs-rename").resolve())
        self.assertEqual(mac, Path("/home/person/Library/Caches/fcs-rename").resolve())

    def test_uses_executable_suffix_only_on_windows(self):
        self.assertEqual(runtime.executable_name("whisper-cli", "Windows"), "whisper-cli.exe")
        self.assertEqual(runtime.executable_name("whisper-cli", "Darwin"), "whisper-cli")

    def test_rejects_portability_hazards(self):
        self.assertIn("Windows 禁用字符", runtime.portable_filename_error("a:b.mov"))
        self.assertIn("Windows 保留名称", runtime.portable_filename_error("CON.txt"))
        self.assertIn("空格或句点结尾", runtime.portable_filename_error("name. "))
        self.assertIsNone(runtime.portable_filename_error("中文 素材.mov"))
        self.assertEqual(
            runtime.portable_filename_key("Ａ.MOV"),
            runtime.portable_filename_key("Ａ.mov"),
        )

    def test_builds_percent_encoded_relative_file_url(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            frame = base / "frames" / "中文 画面.jpg"
            frame.parent.mkdir()
            frame.write_bytes(b"frame")

            value = runtime.relative_file_url(frame, base)

        self.assertEqual(
            value,
            "frames/%E4%B8%AD%E6%96%87%20%E7%94%BB%E9%9D%A2.jpg",
        )

    def test_verified_download_is_atomic_without_network(self):
        payload = b"verified payload"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "asset.bin"
            with patch.object(runtime, "urlopen", return_value=io.BytesIO(payload)):
                result = runtime.download_verified(
                    "https://example.invalid/asset.bin",
                    destination,
                    digest,
                    attempts=1,
                )

            self.assertEqual(result.read_bytes(), payload)
            self.assertFalse((Path(temp) / "asset.bin.partial").exists())

    def test_verified_download_removes_bad_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "asset.bin"
            with patch.object(runtime, "urlopen", return_value=io.BytesIO(b"bad")):
                with self.assertRaisesRegex(RuntimeError, "校验失败"):
                    runtime.download_verified(
                        "https://example.invalid/asset.bin",
                        destination,
                        "0" * 64,
                        attempts=1,
                    )

            self.assertFalse(destination.exists())
            self.assertFalse((Path(temp) / "asset.bin.partial").exists())

    def test_safe_zip_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../escape.txt", "bad")

            with self.assertRaisesRegex(RuntimeError, "不安全路径"):
                runtime.safe_extract_zip(archive, Path(temp) / "out")

    def test_subprocess_output_uses_utf8_replacement(self):
        completed = subprocess.CompletedProcess(
            args=["tool"],
            returncode=0,
            stdout=b"ok\xff",
            stderr=b"",
        )
        with patch.object(runtime.subprocess, "run", return_value=completed):
            result = runtime.run_text(["tool"])

        self.assertEqual(result.stdout, "ok�")

    def test_subprocess_output_falls_back_to_windows_local_encoding(self):
        completed = subprocess.CompletedProcess(
            args=["tool"],
            returncode=0,
            stdout="中文路径".encode("gbk"),
            stderr=b"",
        )
        with (
            patch.object(runtime.subprocess, "run", return_value=completed),
            patch.object(
                runtime.locale,
                "getpreferredencoding",
                return_value="gbk",
            ),
        ):
            result = runtime.run_text(["tool"])

        self.assertEqual(result.stdout, "中文路径")

    def test_rename_no_replace_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mov"
            destination = root / "destination.mov"
            source.write_bytes(b"source")
            destination.write_bytes(b"external")

            with self.assertRaises(OSError):
                runtime.rename_no_replace(source, destination)

            self.assertEqual(source.read_bytes(), b"source")
            self.assertEqual(destination.read_bytes(), b"external")

    def test_rename_no_replace_moves_when_destination_is_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mov"
            destination = root / "destination.mov"
            source.write_bytes(b"source")

            runtime.rename_no_replace(source, destination)

            self.assertFalse(source.exists())
            self.assertEqual(destination.read_bytes(), b"source")


if __name__ == "__main__":
    unittest.main()
