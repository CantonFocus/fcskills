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
MODULE_PATH = SCRIPT_DIR / "analyze_media_assets.py"
sys.path.insert(0, str(SCRIPT_DIR))


def load_module():
    spec = importlib.util.spec_from_file_location("analyze_media_assets_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载素材分析模块")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


analysis = load_module()


def make_result(index: int, wav: Path | None):
    item = analysis.MediaItem(
        index=index,
        path=Path(f"素材 {index}.MOV"),
        created_at=0.0,
    )
    return analysis.AnalysisResult(
        item=item,
        duration=5.0,
        frames=[],
        wav=wav,
        audio_status="有音轨" if wav else "无音轨",
    )


class AnalysisTests(unittest.TestCase):
    def test_recursive_entry_points_stop_before_scanning(self):
        recursive_args = SimpleNamespace(recursive=True)
        with patch("builtins.print") as printer:
            self.assertEqual(analysis.command_preflight(recursive_args), 1)
            self.assertEqual(analysis.command_analyze(recursive_args), 1)

        messages = "\n".join(
            str(call.args[0]) for call in printer.call_args_list if call.args
        )
        self.assertIn("逐个目录处理", messages)

    def test_frame_times_use_one_or_three_frames(self):
        self.assertEqual(len(analysis.frame_times(10.0)), 1)
        self.assertEqual(len(analysis.frame_times(10.01)), 3)
        self.assertEqual(analysis.frame_times(20.0)[1], ("middle", 10.0))

    def test_transcription_batches_split_before_command_limit(self):
        results = [
            make_result(index, Path(f"/very long path/中文 音频 {index}.wav"))
            for index in range(1, 4)
        ]

        batches = analysis.transcription_batches(
            results,
            ["whisper-cli", "-m", "model.bin"],
            max_chars=65,
        )

        self.assertEqual([[item.item.index for item in batch] for batch in batches], [[1], [2], [3]])

    def test_transcription_spawn_error_becomes_review_reason(self):
        result = make_result(1, Path("/tmp/中文 音频.wav"))
        with patch.object(analysis, "run_process", side_effect=RuntimeError("命令过长")):
            analysis.transcribe_batch(
                [result],
                Path("whisper-cli.exe"),
                Path("model.bin"),
                "zh",
                platform_system="Darwin",
            )

        self.assertIn("转写失败", result.review_reason)
        self.assertIn("命令过长", result.review_reason)

    def test_windows_transcription_uses_ascii_staging_and_maps_json_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli = root / "中文 缓存" / "bin" / "whisper-cli.exe"
            model = root / "中文 模型" / "model.bin"
            wav = root / "中文 输出" / "中文 音频.wav"
            cli.parent.mkdir(parents=True)
            model.parent.mkdir(parents=True)
            wav.parent.mkdir(parents=True)
            cli.write_bytes(b"cli")
            model.write_bytes(b"model")
            wav.write_bytes(b"audio")
            result = make_result(1, wav)
            observed_commands: list[list[str]] = []

            def fake_process(command, cwd=None, timeout=None):
                observed_commands.append(command)
                self.assertIsNotNone(cwd)
                self.assertIn(timeout, (1800, 7200))
                for argument in command[1:]:
                    argument.encode("ascii")
                for argument in command:
                    if argument.endswith(".wav"):
                        Path(cwd, argument + ".json").write_text(
                            '{"transcription": []}',
                            encoding="utf-8",
                        )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(analysis, "run_process", side_effect=fake_process):
                analysis.transcribe_batch(
                    [result],
                    cli,
                    model,
                    "zh",
                    platform_system="Windows",
                )

            target_json = Path(str(wav) + ".json")
            self.assertTrue(target_json.is_file())
            self.assertEqual(result.review_reason, "")
            self.assertTrue(observed_commands)
            self.assertEqual(observed_commands[0][0], str(cli.resolve()))
            self.assertTrue(Path(observed_commands[0][0]).is_absolute())

    def test_extract_frame_keeps_chinese_space_path_as_one_argument(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "中文 素材.MOV"
            output = root / "画面 预览.jpg"
            source.write_bytes(b"video")
            observed: list[str] = []

            def fake_process(command, cwd=None, timeout=None):
                observed.extend(command)
                self.assertIsNone(cwd)
                self.assertEqual(timeout, 120)
                Path(command[-1]).write_bytes(b"frame")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(analysis, "run_process", side_effect=fake_process):
                analysis.extract_frame(Path("ffmpeg"), source, output, None, 720)

        self.assertIn(str(source), observed)
        self.assertEqual(observed.count(str(source)), 1)

    def test_contact_sheet_uses_encoded_relative_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            out_dir = Path(temp)
            frame = out_dir / "frames" / "中文 画面.jpg"
            frame.parent.mkdir()
            frame.write_bytes(b"frame")
            result = make_result(1, None)
            result.frames = [frame]

            sheet = analysis.write_contact_sheet([result], out_dir)
            content = sheet.read_text(encoding="utf-8")

        self.assertIn("frames/%E4%B8%AD%E6%96%87%20%E7%94%BB%E9%9D%A2.jpg", content)
        self.assertNotIn("\\frames\\", content)


if __name__ == "__main__":
    unittest.main()
