#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from dataclasses import dataclass
from datetime import datetime
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Iterable

from prepare_local_asr import (
    cache_paths,
    check_status,
    default_cache_root,
    find_cached_ffmpeg,
)
from runtime_support import current_platform, relative_file_url, run_text


VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
MEDIA_EXTS = VIDEO_EXTS | IMAGE_EXTS
DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
BRACKET_ONLY_RE = re.compile(r"^[\s（(【\[].*?[）)】\]]\s*$")
REPEATED_SPEECH_RE = re.compile(r"([\u3400-\u9fff]{2,8})\1{2,}")


@dataclass(frozen=True)
class MediaItem:
    index: int
    path: Path
    created_at: float

    @property
    def created_text(self) -> str:
        return datetime.fromtimestamp(self.created_at).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class AnalysisResult:
    item: MediaItem
    duration: float
    frames: list[Path]
    wav: Path | None
    audio_status: str
    transcript: str = ""
    confidence: float | None = None
    confidence_label: str = ""
    sound_candidate: str = ""
    review_reason: str = ""
    transcript_json: Path | None = None


def is_media(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in MEDIA_EXTS
        and not path.name.startswith(".")
    )


def file_time(path: Path) -> float:
    stat = path.stat()
    birth = getattr(stat, "st_birthtime", None)
    return float(birth or stat.st_mtime)


def scan_media(root: Path, recursive: bool) -> list[MediaItem]:
    iterator: Iterable[Path] = root.rglob("*") if recursive else root.iterdir()
    files = [path for path in iterator if is_media(path)]
    files.sort(key=lambda path: (file_time(path), path.name))
    return [
        MediaItem(index + 1, path, file_time(path))
        for index, path in enumerate(files)
    ]


def run_process(
    command: list[str],
    cwd: Path | None = None,
    timeout: float | None = None,
):
    return run_text(command, cwd=cwd, timeout=timeout)


def probe_duration(ffmpeg: Path, path: Path) -> float:
    result = run_process(
        [str(ffmpeg), "-hide_banner", "-i", str(path)],
        timeout=60,
    )
    text = result.stderr + "\n" + result.stdout
    match = DURATION_RE.search(text)
    if not match:
        raise RuntimeError(f"无法读取时长：{path.name}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def frame_times(duration: float) -> list[tuple[str, float]]:
    if duration <= 10:
        return [("full", max(0.0, min(duration * 0.25, 1.0)))]
    return [
        ("start", min(0.5, duration * 0.05)),
        ("middle", duration / 2),
        ("end", max(0.0, duration - 0.5)),
    ]


def extract_frame(
    ffmpeg: Path,
    source: Path,
    output: Path,
    at_seconds: float | None,
    width: int,
) -> None:
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
    if at_seconds is not None:
        command.extend(["-ss", f"{at_seconds:.3f}"])
    command.extend(["-i", str(source), "-frames:v", "1"])
    command.extend(
        [
            "-vf",
            f"scale='min({width},iw)':-2",
            "-q:v",
            "3",
            str(output),
        ]
    )
    result = run_process(command, timeout=120)
    if result.returncode != 0 or not output.is_file():
        error = result.stderr.strip() or "未知错误"
        raise RuntimeError(f"画面提取失败：{source.name}：{error}")


def extract_audio(ffmpeg: Path, source: Path, output: Path) -> tuple[Path | None, str]:
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0?",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    result = run_process(command, timeout=600)
    if output.is_file() and output.stat().st_size > 44:
        return output, "有音轨"
    text = (result.stderr + result.stdout).lower()
    no_audio_markers = (
        "does not contain any stream",
        "matches no streams",
        "stream map",
    )
    if result.returncode == 0 or any(marker in text for marker in no_audio_markers):
        return None, "无音轨"
    error = result.stderr.strip() or "未知错误"
    raise RuntimeError(f"音轨提取失败：{source.name}：{error}")


def analyze_one(
    item: MediaItem,
    ffmpeg: Path,
    frames_dir: Path,
    audio_dir: Path,
    frame_width: int,
) -> AnalysisResult:
    stem = f"{item.index:03d}"
    frames: list[Path] = []

    if item.path.suffix.lower() in IMAGE_EXTS:
        output = frames_dir / f"{stem}-image.jpg"
        extract_frame(ffmpeg, item.path, output, None, frame_width)
        return AnalysisResult(
            item=item,
            duration=0.0,
            frames=[output],
            wav=None,
            audio_status="图片",
        )

    duration = probe_duration(ffmpeg, item.path)
    for label, at_seconds in frame_times(duration):
        output = frames_dir / f"{stem}-{label}.jpg"
        extract_frame(ffmpeg, item.path, output, at_seconds, frame_width)
        frames.append(output)

    wav = audio_dir / f"{stem}.wav"
    wav_path, audio_status = extract_audio(ffmpeg, item.path, wav)
    return AnalysisResult(
        item=item,
        duration=duration,
        frames=frames,
        wav=wav_path,
        audio_status=audio_status,
    )


def transcribe_batch(
    results: list[AnalysisResult],
    cli: Path,
    model: Path,
    language: str,
    platform_system: str | None = None,
) -> None:
    with_audio = [result for result in results if result.wav is not None]
    if not with_audio:
        return

    if current_platform(system_name=platform_system).is_windows:
        _transcribe_windows_staged(with_audio, cli, model, language)
        return

    base_command = [
        str(cli),
        "-m",
        str(model),
        "-l",
        language,
        "-nt",
        "-np",
        "-ojf",
    ]
    arguments = {
        result.item.index: str(result.wav)
        for result in with_audio
        if result.wav is not None
    }
    json_paths = {
        result.item.index: Path(str(result.wav) + ".json")
        for result in with_audio
        if result.wav is not None
    }
    _run_transcription_commands(
        with_audio,
        base_command,
        arguments,
        json_paths,
    )


def _run_transcription_commands(
    results: list[AnalysisResult],
    base_command: list[str],
    arguments: dict[int, str],
    json_paths: dict[int, Path],
    cwd: Path | None = None,
    max_chars: int | None = None,
) -> None:
    for batch_results in transcription_batches(
        results,
        base_command,
        max_chars=max_chars,
        arguments=arguments,
    ):
        command = list(base_command)
        command.extend(arguments[result.item.index] for result in batch_results)
        batch_error = ""
        try:
            batch = run_process(command, cwd=cwd, timeout=7200)
            if batch.returncode != 0:
                batch_error = batch.stderr.strip() or batch.stdout.strip()
        except RuntimeError as exc:
            batch_error = str(exc)

        missing = [
            result
            for result in batch_results
            if not json_paths[result.item.index].is_file()
        ]
        for result in missing:
            single_error = ""
            try:
                single_command = base_command + [arguments[result.item.index]]
                single = run_process(single_command, cwd=cwd, timeout=1800)
                if single.returncode != 0:
                    single_error = single.stderr.strip() or single.stdout.strip()
            except RuntimeError as exc:
                single_error = str(exc)
            json_path = json_paths[result.item.index]
            if not json_path.is_file():
                reason = single_error or batch_error or "没有生成转写结果"
                result.review_reason = f"转写失败：{reason}"


def _link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _transcribe_windows_staged(
    results: list[AnalysisResult],
    cli: Path,
    model: Path,
    language: str,
) -> None:
    original_json_paths = {
        result.item.index: Path(str(result.wav) + ".json")
        for result in results
        if result.wav is not None
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="fcs-rename-asr-",
            dir=str(cli.parent),
        ) as temp:
            stage = Path(temp)
            staged_model = stage / "model.bin"
            _link_or_copy(model, staged_model)

            arguments: dict[int, str] = {}
            staged_json_paths: dict[int, Path] = {}
            for sequence, result in enumerate(results, 1):
                if result.wav is None:
                    continue
                staged_wav = stage / f"audio-{sequence:06d}.wav"
                _link_or_copy(result.wav, staged_wav)
                arguments[result.item.index] = staged_wav.name
                staged_json_paths[result.item.index] = Path(
                    str(staged_wav) + ".json"
                )

            base_command = [
                str(cli.resolve()),
                "-m",
                staged_model.name,
                "-l",
                language,
                "-nt",
                "-np",
                "-ojf",
            ]
            _run_transcription_commands(
                results,
                base_command,
                arguments,
                staged_json_paths,
                cwd=stage,
                max_chars=24000,
            )
            for result in results:
                source_json = staged_json_paths[result.item.index]
                if not source_json.is_file():
                    continue
                target_json = original_json_paths[result.item.index]
                if target_json.exists():
                    raise RuntimeError(f"转写目标已存在：{target_json.name}")
                shutil.move(str(source_json), target_json)
    except (OSError, RuntimeError) as exc:
        for result in results:
            target_json = original_json_paths[result.item.index]
            if not target_json.is_file() and not result.review_reason:
                result.review_reason = f"Windows 安全路径转写失败：{exc}"


def transcription_batches(
    results: list[AnalysisResult],
    base_command: list[str],
    max_chars: int | None = None,
    arguments: dict[int, str] | None = None,
) -> list[list[AnalysisResult]]:
    limit = max_chars or (24000 if os.name == "nt" else 100000)
    base_size = sum(len(part) + 3 for part in base_command)
    batches: list[list[AnalysisResult]] = []
    current: list[AnalysisResult] = []
    current_size = base_size
    for result in results:
        if result.wav is None:
            continue
        argument = (
            arguments[result.item.index]
            if arguments is not None
            else str(result.wav)
        )
        path_size = len(argument) + 3
        if current and current_size + path_size > limit:
            batches.append(current)
            current = []
            current_size = base_size
        current.append(result)
        current_size += path_size
    if current:
        batches.append(current)
    return batches


def parse_transcript(result: AnalysisResult, transcripts_dir: Path) -> None:
    if result.audio_status == "图片":
        result.confidence_label = "不适用"
        result.sound_candidate = "画面"
        return
    if result.wav is None:
        result.confidence_label = "不适用"
        result.sound_candidate = "画面"
        return

    source_json = Path(str(result.wav) + ".json")
    if not source_json.is_file():
        result.confidence_label = "低"
        result.sound_candidate = "待复查"
        if not result.review_reason:
            result.review_reason = "缺少转写结果"
        return

    # whisper.cpp 的 token 字段偶尔会写出截断的多字节字符；segment 文本仍然
    # 可用。容错替换坏字节，避免单个 token 让整批分析中断。
    try:
        raw_json = source_json.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw_json)
    except (OSError, json.JSONDecodeError) as exc:
        result.confidence_label = "低"
        result.sound_candidate = "待复查"
        result.review_reason = f"转写结果无法解析：{exc}"
        target_json = transcripts_dir / f"{result.item.index:03d}.json"
        shutil.move(str(source_json), target_json)
        result.transcript_json = target_json
        return
    segments = data.get("transcription", [])
    texts = [str(segment.get("text", "")).strip() for segment in segments]
    result.transcript = " ".join(text for text in texts if text).strip()

    probabilities: list[float] = []
    for segment in segments:
        for token in segment.get("tokens", []):
            token_text = str(token.get("text", "")).strip()
            if not token_text or token_text.startswith("["):
                continue
            probability = token.get("p")
            if isinstance(probability, (int, float)) and math.isfinite(probability):
                probabilities.append(float(probability))
    if probabilities:
        result.confidence = sum(probabilities) / len(probabilities)

    compact = re.sub(r"\s+", "", result.transcript)
    reasons: list[str] = []
    if not compact:
        result.confidence_label = "低"
        result.sound_candidate = "环境或画面"
        reasons.append("没有识别到有效说话")
    elif len(compact) < 4 or BRACKET_ONLY_RE.match(compact):
        result.confidence_label = "低"
        result.sound_candidate = "待复查"
        reasons.append("转写过短或疑似环境声误识别")
    elif REPEATED_SPEECH_RE.search(compact):
        result.confidence_label = "低"
        result.sound_candidate = "口播或环境待复查"
        reasons.append("识别文本出现机械重复，疑似环境声误识别")
    elif result.duration <= 3:
        result.confidence_label = "低"
        result.sound_candidate = "口播或环境待复查"
        reasons.append("素材短于 3 秒，声音证据不足")
    elif result.confidence is None or result.confidence < 0.35:
        result.confidence_label = "低"
        result.sound_candidate = "口播或对话待复查"
        reasons.append("平均识别置信度低于 0.35")
    elif result.confidence < 0.55:
        result.confidence_label = "中"
        result.sound_candidate = "口播或对话"
    else:
        result.confidence_label = "高"
        result.sound_candidate = "口播或对话"

    if reasons:
        existing = result.review_reason.strip()
        result.review_reason = "；".join(
            ([existing] if existing else []) + reasons
        )

    target_json = transcripts_dir / f"{result.item.index:03d}.json"
    shutil.move(str(source_json), target_json)
    result.transcript_json = target_json


def write_analysis(results: list[AnalysisResult], out_dir: Path) -> tuple[Path, Path]:
    analysis_path = out_dir / "analysis.tsv"
    review_path = out_dir / "low_confidence.tsv"
    header = [
        "序号",
        "文件排序时间",
        "原文件名",
        "时长秒",
        "画面数",
        "音频状态",
        "转写置信度",
        "平均概率",
        "声音候选",
        "转写",
        "复查原因",
    ]
    with analysis_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="\t")
        writer.writerow(header)
        for result in results:
            writer.writerow(
                [
                    f"{result.item.index:03d}",
                    result.item.created_text,
                    result.item.path.name,
                    f"{result.duration:.2f}",
                    len(result.frames),
                    result.audio_status,
                    result.confidence_label,
                    (
                        f"{result.confidence:.3f}"
                        if result.confidence is not None
                        else ""
                    ),
                    result.sound_candidate,
                    result.transcript,
                    result.review_reason,
                ]
            )

    with review_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="\t")
        writer.writerow(header)
        for result in results:
            if not result.review_reason:
                continue
            writer.writerow(
                [
                    f"{result.item.index:03d}",
                    result.item.created_text,
                    result.item.path.name,
                    f"{result.duration:.2f}",
                    len(result.frames),
                    result.audio_status,
                    result.confidence_label,
                    (
                        f"{result.confidence:.3f}"
                        if result.confidence is not None
                        else ""
                    ),
                    result.sound_candidate,
                    result.transcript,
                    result.review_reason,
                ]
            )
    return analysis_path, review_path


def write_template(results: list[AnalysisResult], out_dir: Path) -> Path:
    path = out_dir / "rename_map_template.tsv"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="\t")
        for result in results:
            writer.writerow([result.item.path.name, ""])
    return path


def write_contact_sheet(results: list[AnalysisResult], out_dir: Path) -> Path:
    path = out_dir / "contact_sheet.html"
    lines = [
        "<!doctype html>",
        "<meta charset='utf-8'>",
        "<title>素材快速安全分析</title>",
        "<style>",
        "body{font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;margin:20px;background:#f5f5f5;color:#111}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}",
        ".card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px}",
        ".frames{display:flex;gap:4px;overflow:hidden}.frames img{width:33%;height:190px;object-fit:contain;background:#eee}",
        ".idx{color:#b00020;font-weight:700}.name{font-size:12px;word-break:break-all}.meta{font-size:12px;color:#555}",
        ".transcript{font-size:13px;line-height:1.55}.review{font-size:12px;color:#b00020}",
        "</style>",
        "<h1>素材快速安全分析</h1>",
        "<div class='grid'>",
    ]
    for result in results:
        images = "".join(
            f"<img src='{relative_file_url(frame, out_dir)}'>"
            for frame in result.frames
        )
        lines.extend(
            [
                "<div class='card'>",
                f"<div class='frames'>{images}</div>",
                f"<p><span class='idx'>{result.item.index:03d}</span> <span class='name'>{html.escape(result.item.path.name)}</span></p>",
                f"<p class='meta'>{result.duration:.2f} 秒 · {html.escape(result.audio_status)} · {html.escape(result.confidence_label)} · {html.escape(result.sound_candidate)}</p>",
                f"<p class='transcript'>{html.escape(result.transcript) or '无转写'}</p>",
                (
                    f"<p class='review'>{html.escape(result.review_reason)}</p>"
                    if result.review_reason
                    else ""
                ),
                "</div>",
            ]
        )
    lines.append("</div>")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def command_preflight(args: argparse.Namespace) -> int:
    if args.recursive:
        print("递归改名尚未支持，请逐个目录处理。")
        print("快速安全分析：不可用")
        return 1
    cache_root = Path(args.cache).expanduser().resolve()
    ready, messages = check_status(cache_root)
    print(f"缓存目录：{cache_root}")
    for message in messages:
        print(message)

    root_ready = True
    if args.root:
        root = Path(args.root).expanduser().resolve()
        if not root.is_dir():
            print(f"素材目录：不存在 {root}")
            root_ready = False
        else:
            items = scan_media(root, args.recursive)
            print(f"素材目录：{root}")
            print(f"可处理素材：{len(items)}")
            if not items:
                root_ready = False

    print(f"快速安全分析：{'可用' if ready and root_ready else '不可用'}")
    return 0 if ready and root_ready else 1


def command_analyze(args: argparse.Namespace) -> int:
    if args.recursive:
        print(
            "分析停止：递归改名尚未支持，请逐个目录处理。",
            file=sys.stderr,
        )
        return 1
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"分析停止：目录不存在：{root}", file=sys.stderr)
        return 1

    items = scan_media(root, args.recursive)
    if not items:
        print("分析停止：没有找到可处理素材。", file=sys.stderr)
        return 1

    cache_root = Path(args.cache).expanduser().resolve()
    ready, messages = check_status(cache_root)
    if not ready:
        print("分析停止：本地分析环境未准备完成。", file=sys.stderr)
        for message in messages:
            print(message, file=sys.stderr)
        return 1

    out_dir = (
        Path(args.out).expanduser().resolve()
        if args.out
        else Path(tempfile.mkdtemp(prefix="fcs-rename-analysis-"))
    )
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"分析停止：输出目录非空：{out_dir}", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    if shutil.disk_usage(out_dir).free < 1024 * 1024 * 1024:
        print("分析停止：临时目录可用空间不足 1 GB。", file=sys.stderr)
        return 1

    paths = cache_paths(cache_root)
    ffmpeg = find_cached_ffmpeg(paths["python"])
    if ffmpeg is None:
        print("分析停止：FFmpeg 不可用。", file=sys.stderr)
        return 1

    frames_dir = out_dir / "frames"
    audio_dir = out_dir / "audio"
    transcripts_dir = out_dir / "transcripts"
    frames_dir.mkdir()
    audio_dir.mkdir()
    transcripts_dir.mkdir()

    results_by_index: dict[int, AnalysisResult] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                analyze_one,
                item,
                ffmpeg,
                frames_dir,
                audio_dir,
                args.frame_width,
            ): item
            for item in items
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                results_by_index[item.index] = future.result()
            except Exception as exc:
                errors.append(f"{item.index:03d} {item.path.name}：{exc}")

    if errors:
        print("分析停止：以下素材无法完成画面或音轨提取：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    results = [results_by_index[index] for index in sorted(results_by_index)]
    transcribe_batch(results, paths["cli"], paths["model"], args.language)
    for result in results:
        parse_transcript(result, transcripts_dir)

    analysis_path, review_path = write_analysis(results, out_dir)
    template_path = write_template(results, out_dir)
    sheet_path = write_contact_sheet(results, out_dir)

    if not args.keep_audio:
        for result in results:
            if result.wav and result.wav.exists():
                result.wav.unlink()
        if audio_dir.exists() and not any(audio_dir.iterdir()):
            audio_dir.rmdir()

    low_count = sum(1 for result in results if result.review_reason)
    frame_count = sum(len(result.frames) for result in results)
    print("快速安全分析已完成。")
    print(f"素材总数：{len(results)}")
    print(f"画面总数：{frame_count}")
    print(f"需要重点复查：{low_count}")
    print(f"分析表：{analysis_path}")
    print(f"重点复查表：{review_path}")
    print(f"联系表：{sheet_path}")
    print(f"映射模板：{template_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze media pictures and original sound for fcs-rename."
    )
    parser.add_argument("--cache", default=str(default_cache_root()))
    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser("preflight", help="check tools and media directory")
    preflight.add_argument("--root", default="")
    preflight.add_argument("--recursive", action="store_true")
    preflight.set_defaults(func=command_preflight)

    analyze = sub.add_parser("analyze", help="run fast safe media analysis")
    analyze.add_argument("--root", required=True)
    analyze.add_argument("--out", default="")
    analyze.add_argument("--recursive", action="store_true")
    analyze.add_argument("--workers", type=int, default=4)
    analyze.add_argument("--frame-width", type=int, default=720)
    analyze.add_argument("--language", default="zh")
    analyze.add_argument("--keep-audio", action="store_true")
    analyze.set_defaults(func=command_analyze)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
