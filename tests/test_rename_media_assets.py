from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "fcs-rename" / "scripts"
MODULE_PATH = SCRIPT_DIR / "rename_media_assets.py"


def load_module():
    sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location("rename_media_assets", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载重命名脚本")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


renamer = load_module()


class RenameMediaAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_file(self, name: str, content: bytes = b"test") -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def test_dry_run_does_not_change_files(self):
        source = self.create_file("IMG_0001.MOV")
        rows = [(source.name, "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV")]

        renamer.apply_mapping(self.root, rows, dry_run=True)

        self.assertTrue(source.exists())
        self.assertFalse((self.root / rows[0][1]).exists())

    def test_rejects_duplicate_source_names(self):
        source = self.create_file("IMG_0001.MOV")
        rows = [
            (source.name, "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"),
            (source.name, "002_早餐环境_酒店餐厅_餐桌远景_环境.MOV"),
        ]

        with self.assertRaisesRegex(ValueError, "原文件名重复"):
            renamer.apply_mapping(self.root, rows, dry_run=True)

    def test_rejects_casefold_duplicate_target_names(self):
        first = self.create_file("IMG_0001.MOV")
        second = self.create_file("IMG_0002.MOV")
        rows = [
            (first.name, "001_ABC_酒店餐厅_桌前讲述_口播.MOV"),
            (second.name, "001_abc_酒店餐厅_桌前讲述_口播.MOV"),
        ]

        with self.assertRaisesRegex(ValueError, "目标文件名重复"):
            renamer.apply_mapping(self.root, rows, dry_run=True)

    def test_rejects_windows_forbidden_character(self):
        source = self.create_file("IMG_0001.MOV")
        rows = [(source.name, "001_早餐:价格_酒店餐厅_桌前讲述_口播.MOV")]

        with self.assertRaisesRegex(ValueError, "Windows 禁用字符"):
            renamer.apply_mapping(self.root, rows, dry_run=True)

    def test_rejects_existing_casefold_collision(self):
        source = self.create_file("IMG_0001.MOV")
        self.create_file("001_ABC_酒店餐厅_桌前讲述_口播.MOV")
        rows = [(source.name, "001_abc_酒店餐厅_桌前讲述_口播.MOV")]

        with self.assertRaisesRegex(ValueError, "大小写冲突"):
            renamer.apply_mapping(self.root, rows, dry_run=True)

    def test_successfully_renames_multiple_files(self):
        first = self.create_file("IMG_0001.MOV", b"first")
        second = self.create_file("IMG_0002.MOV", b"second")
        rows = [
            (first.name, "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"),
            (second.name, "002_早餐环境_酒店餐厅_餐桌远景_环境.MOV"),
        ]

        renamer.apply_mapping(self.root, rows, dry_run=False)

        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertEqual((self.root / rows[0][1]).read_bytes(), b"first")
        self.assertEqual((self.root / rows[1][1]).read_bytes(), b"second")

    def test_complete_mapping_can_swap_existing_valid_names(self):
        first_name = "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"
        second_name = "002_早餐环境_酒店餐厅_餐桌远景_环境.MOV"
        self.create_file(first_name, b"first")
        self.create_file(second_name, b"second")

        renamer.apply_mapping(
            self.root,
            [(first_name, second_name), (second_name, first_name)],
            dry_run=False,
        )

        self.assertEqual((self.root / first_name).read_bytes(), b"second")
        self.assertEqual((self.root / second_name).read_bytes(), b"first")

    def test_complete_mapping_allows_case_only_name_change(self):
        original = "001_ABC_酒店餐厅_桌前讲述_口播.MOV"
        target = "001_abc_酒店餐厅_桌前讲述_口播.MOV"
        self.create_file(original, b"content")

        renamer.apply_mapping(
            self.root,
            [(original, target)],
            dry_run=False,
        )

        names = [path.name for path in self.root.iterdir()]
        self.assertEqual(names, [target])
        self.assertEqual((self.root / target).read_bytes(), b"content")

    def test_rolls_back_when_final_rename_fails(self):
        first = self.create_file("IMG_0001.MOV", b"first")
        second = self.create_file("IMG_0002.MOV", b"second")
        first_target = "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"
        second_target = "002_早餐环境_酒店餐厅_餐桌远景_环境.MOV"
        rows = [(first.name, first_target), (second.name, second_target)]
        original_rename = renamer.rename_no_replace

        def fail_on_second_target(path, target):
            target_path = Path(target)
            if target_path.name == second_target:
                raise OSError("模拟磁盘错误")
            return original_rename(path, target)

        with patch.object(renamer, "rename_no_replace", side_effect=fail_on_second_target):
            with self.assertRaisesRegex(OSError, "模拟磁盘错误"):
                renamer.apply_mapping(self.root, rows, dry_run=False)

        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(second.read_bytes(), b"second")
        self.assertFalse((self.root / first_target).exists())
        self.assertFalse((self.root / second_target).exists())
        self.assertEqual(list(self.root.glob(".fcs-renaming-*")), [])

    def test_rolls_back_when_interrupted(self):
        source = self.create_file("IMG_0001.MOV", b"source")
        target = "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"
        original_rename = renamer.rename_no_replace

        def interrupt_final_move(path, destination):
            if Path(destination).name == target:
                raise KeyboardInterrupt()
            return original_rename(path, destination)

        with patch.object(
            renamer,
            "rename_no_replace",
            side_effect=interrupt_final_move,
        ):
            with self.assertRaises(KeyboardInterrupt):
                renamer.apply_mapping(
                    self.root,
                    [(source.name, target)],
                    dry_run=False,
                )

        self.assertEqual(source.read_bytes(), b"source")
        self.assertFalse((self.root / target).exists())
        self.assertEqual(list(self.root.glob(".fcs-renaming-*")), [])

    def test_staging_names_are_short_and_do_not_include_source_name(self):
        long_source_name = "IMG_" + "A" * 120 + ".MOV"
        source = self.create_file(long_source_name)
        target = "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"
        original_rename = renamer.rename_no_replace
        staging_names: list[str] = []

        def record_staging_name(path, destination):
            destination_path = Path(destination)
            if destination_path.name.startswith(".fcs-renaming-"):
                staging_names.append(destination_path.name)
            return original_rename(path, destination)

        with patch.object(renamer, "rename_no_replace", side_effect=record_staging_name):
            renamer.apply_mapping(self.root, [(source.name, target)], dry_run=False)

        self.assertEqual(len(staging_names), 1)
        self.assertLess(len(staging_names[0]), 60)
        self.assertNotIn("IMG_", staging_names[0])

    def test_rollback_does_not_move_external_target(self):
        first = self.create_file("IMG_0001.MOV", b"first")
        second = self.create_file("IMG_0002.MOV", b"second")
        first_target = "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"
        second_target = "002_早餐环境_酒店餐厅_餐桌远景_环境.MOV"
        original_rename = renamer.rename_no_replace

        def create_competing_target(path, target):
            target_path = Path(target)
            if target_path.name == second_target:
                target_path.write_bytes(b"external")
                raise FileExistsError("模拟同步盘竞态")
            return original_rename(path, target)

        with patch.object(
            renamer,
            "rename_no_replace",
            side_effect=create_competing_target,
        ):
            with self.assertRaisesRegex(FileExistsError, "同步盘竞态"):
                renamer.apply_mapping(
                    self.root,
                    [(first.name, first_target), (second.name, second_target)],
                    dry_run=False,
                )

        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(second.read_bytes(), b"second")
        self.assertEqual((self.root / second_target).read_bytes(), b"external")
        self.assertFalse((self.root / first_target).exists())
        self.assertEqual(list(self.root.glob(".fcs-renaming-*")), [])

    def test_rollback_reports_original_path_occupied_without_overwrite(self):
        source = self.create_file("IMG_0001.MOV", b"original")
        target = "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"
        original_rename = renamer.rename_no_replace

        def occupy_original_before_rollback(path, destination):
            destination_path = Path(destination)
            if destination_path.name == target:
                source.write_bytes(b"external")
                raise OSError("模拟最终改名失败")
            return original_rename(path, destination)

        with patch.object(
            renamer,
            "rename_no_replace",
            side_effect=occupy_original_before_rollback,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "自动恢复不完整.*原素材保留在",
            ):
                renamer.apply_mapping(
                    self.root,
                    [(source.name, target)],
                    dry_run=False,
                )

        self.assertEqual(source.read_bytes(), b"external")
        recovery_files = list(self.root.glob(".fcs-renaming-*"))
        self.assertEqual(len(recovery_files), 1)
        self.assertEqual(recovery_files[0].read_bytes(), b"original")
        self.assertFalse((self.root / target).exists())

    def test_reads_crlf_tsv_with_chinese_and_spaces(self):
        mapping = self.root / "改名 映射.tsv"
        mapping.write_bytes(
            "IMG_0001.MOV\t001_早餐价格_酒店餐厅_桌前讲述_口播.MOV\r\n".encode(
                "utf-8"
            )
        )

        self.assertEqual(
            renamer.read_map(mapping),
            [("IMG_0001.MOV", "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV")],
        )

    def test_rejects_incomplete_mapping_row(self):
        mapping = self.root / "incomplete.tsv"
        mapping.write_text("IMG_0001.MOV\t\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "第 1 行映射不完整"):
            renamer.read_map(mapping)

    def test_rejects_non_media_source(self):
        source = self.create_file("notes.txt")
        rows = [(source.name, "001_早餐价格_酒店餐厅_桌前讲述_口播.txt")]

        with self.assertRaisesRegex(ValueError, "不是受支持的素材"):
            renamer.apply_mapping(self.root, rows, dry_run=True)

    def test_rejects_mapping_that_omits_media_before_any_move(self):
        first = self.create_file("IMG_0001.MOV", b"first")
        second = self.create_file("IMG_0002.MOV", b"second")
        target = "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"

        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                with self.assertRaisesRegex(ValueError, "必须覆盖.*全部素材"):
                    renamer.apply_mapping(
                        self.root,
                        [(first.name, target)],
                        dry_run=dry_run,
                    )
                self.assertEqual(first.read_bytes(), b"first")
                self.assertEqual(second.read_bytes(), b"second")
                self.assertFalse((self.root / target).exists())
                self.assertEqual(list(self.root.glob(".fcs-renaming-*")), [])

    def test_rejects_noncontinuous_target_numbers_before_any_move(self):
        first = self.create_file("IMG_0001.MOV", b"first")
        second = self.create_file("IMG_0002.MOV", b"second")
        rows = [
            (first.name, "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"),
            (second.name, "003_早餐环境_酒店餐厅_餐桌远景_环境.MOV"),
        ]

        with self.assertRaisesRegex(ValueError, "编号必须从 001 到 002 连续"):
            renamer.apply_mapping(self.root, rows, dry_run=False)

        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(second.read_bytes(), b"second")
        self.assertEqual(list(self.root.glob(".fcs-renaming-*")), [])

    def test_thumbnails_use_ffmpeg_with_unsplit_paths(self):
        source = self.create_file("中文 素材.MOV")
        item = renamer.MediaItem(1, source, 0.0)
        thumb_dir = self.root / "输出 目录" / "thumbs"
        observed: list[str] = []

        def fake_run(command, timeout=None):
            observed.extend(command)
            Path(command[-1]).write_bytes(b"thumbnail")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch.object(renamer, "find_cached_ffmpeg", return_value=Path("ffmpeg")),
            patch.object(renamer, "run_text", side_effect=fake_run),
        ):
            renamer.run_thumbnails([item], thumb_dir, 360, self.root / "cache")

        self.assertIn(str(source), observed)
        self.assertEqual(observed.count(str(source)), 1)
        self.assertTrue((thumb_dir / "001.jpg").is_file())

    def test_verify_rejects_empty_directory(self):
        args = SimpleNamespace(root=str(self.root), recursive=False)
        with patch("builtins.print") as printer:
            self.assertEqual(renamer.command_verify(args), 1)

        messages = "\n".join(
            str(call.args[0]) for call in printer.call_args_list if call.args
        )
        self.assertIn("没有找到可处理素材", messages)

    def test_cli_apply_dry_run_apply_and_verify_with_chinese_path(self):
        media_root = self.root / "中文 素材 目录"
        media_root.mkdir()
        source = media_root / "IMG_0001.MOV"
        source.write_bytes(b"content")
        target_name = "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"
        mapping = self.root / "改名 映射.tsv"
        mapping.write_text(
            f"{source.name}\t{target_name}\n",
            encoding="utf-8",
        )
        base_command = [
            sys.executable,
            str(MODULE_PATH),
            "apply",
            "--root",
            str(media_root),
            "--map",
            str(mapping),
        ]

        dry_run = subprocess.run(
            base_command + ["--dry-run"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            dry_run.returncode,
            0,
            dry_run.stderr.decode("utf-8", errors="replace"),
        )
        self.assertTrue(source.is_file())

        applied = subprocess.run(
            base_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            applied.returncode,
            0,
            applied.stderr.decode("utf-8", errors="replace"),
        )
        self.assertEqual((media_root / target_name).read_bytes(), b"content")

        verified = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "verify",
                "--root",
                str(media_root),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            verified.returncode,
            0,
            verified.stderr.decode("utf-8", errors="replace"),
        )

    def test_cli_preflight_with_empty_cache_does_not_download(self):
        media_root = self.root / "中文 素材 目录"
        cache_root = self.root / "空 缓存"
        media_root.mkdir()
        (media_root / "IMG_0001.MOV").write_bytes(b"content")

        result = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "preflight",
                "--cache",
                str(cache_root),
                "--root",
                str(media_root),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )

        self.assertEqual(result.returncode, 1)
        self.assertFalse(cache_root.exists())


if __name__ == "__main__":
    unittest.main()
