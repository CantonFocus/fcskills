from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "fcs-rename" / "scripts"
MODULE_PATH = SCRIPT_DIR / "rename_media_assets.py"


def load_module():
    import sys

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

    def test_rolls_back_when_final_rename_fails(self):
        first = self.create_file("IMG_0001.MOV", b"first")
        second = self.create_file("IMG_0002.MOV", b"second")
        first_target = "001_早餐价格_酒店餐厅_桌前讲述_口播.MOV"
        second_target = "002_早餐环境_酒店餐厅_餐桌远景_环境.MOV"
        rows = [(first.name, first_target), (second.name, second_target)]
        path_type = type(self.root)
        original_rename = path_type.rename

        def fail_on_second_target(path, target):
            target_path = Path(target)
            if target_path.name == second_target:
                raise OSError("模拟磁盘错误")
            return original_rename(path, target)

        with patch.object(path_type, "rename", new=fail_on_second_target):
            with self.assertRaisesRegex(OSError, "模拟磁盘错误"):
                renamer.apply_mapping(self.root, rows, dry_run=False)

        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(second.read_bytes(), b"second")
        self.assertFalse((self.root / first_target).exists())
        self.assertFalse((self.root / second_target).exists())
        self.assertEqual(list(self.root.glob(".fcs-renaming-*")), [])


if __name__ == "__main__":
    unittest.main()
