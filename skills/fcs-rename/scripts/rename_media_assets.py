#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from prepare_local_asr import default_cache_root

MEDIA_EXTS = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv",
    ".jpg", ".jpeg", ".png", ".heic", ".webp",
}
NAME_RE = re.compile(r"^\d{3}_[^_]+_[^_]+_[^_]+_[^_]+\.[^.]+$")
DEFAULT_CACHE = str(default_cache_root())

@dataclass(frozen=True)
class MediaItem:
    index: int
    path: Path
    created_at: float

    @property
    def created_text(self) -> str:
        return datetime.fromtimestamp(self.created_at).strftime("%Y-%m-%d %H:%M:%S")

def is_media(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in MEDIA_EXTS and not path.name.startswith(".")

def file_time(path: Path) -> float:
    stat = path.stat()
    birth = getattr(stat, "st_birthtime", None)
    return float(birth or stat.st_mtime)

def scan_media(root: Path, recursive: bool = False) -> list[MediaItem]:
    iterator: Iterable[Path] = root.rglob("*") if recursive else root.iterdir()
    files = [p for p in iterator if is_media(p)]
    files.sort(key=lambda p: (file_time(p), p.name))
    return [MediaItem(i + 1, p, file_time(p)) for i, p in enumerate(files)]

def run_quicklook(files: list[Path], out_dir: Path, size: int) -> None:
    qlmanage = shutil.which("qlmanage")
    if not qlmanage or not files:
        raise RuntimeError("Quick Look 不可用")
    out_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for start in range(0, len(files), 50):
        chunk = files[start:start + 50]
        cmd = [qlmanage, "-t", "-s", str(size), "-o", str(out_dir)] + [str(p) for p in chunk]
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            failures.append(result.stderr.strip() or f"退出码 {result.returncode}")
    if failures:
        raise RuntimeError("Quick Look 生成失败：" + "；".join(failures))

def thumbnail_for(item: MediaItem, thumb_dir: Path) -> Path | None:
    for suffix in (".png", ".jpg"):
        candidate = thumb_dir / f"{item.path.name}{suffix}"
        if candidate.exists():
            return candidate
    return None

def write_contact_sheet(items: list[MediaItem], out_dir: Path, title: str) -> Path:
    html_path = out_dir / "contact_sheet.html"
    thumb_dir = out_dir / "thumbs"
    lines = [
        "<!doctype html>",
        "<meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title>",
        "<style>",
        "body{font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;margin:24px;background:#f7f7f7;color:#111}",
        "h1{font-size:20px;margin:0 0 16px}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}",
        ".card{background:white;border:1px solid #ddd;border-radius:6px;padding:10px}",
        "img{width:100%;height:240px;object-fit:contain;background:#eee}",
        ".idx{color:#c00;font-weight:700}.name{word-break:break-all;font-size:12px}.time{font-size:12px;color:#666}",
        "</style>",
        f"<h1>{html.escape(title)}</h1>",
        "<div class='grid'>",
    ]
    for item in items:
        thumb = thumbnail_for(item, thumb_dir)
        img = f"<img src='{html.escape(str(thumb))}'>" if thumb else "<div style='height:240px;background:#eee;display:flex;align-items:center;justify-content:center;color:#777'>no thumbnail</div>"
        lines.extend([
            "<div class='card'>", img,
            f"<p><span class='idx'>{item.index:03d}</span></p>",
            f"<p class='name'>{html.escape(item.path.name)}</p>",
            f"<p class='time'>{html.escape(item.created_text)}</p>",
            "</div>",
        ])
    lines.append("</div>")
    html_path.write_text("\n".join(lines), encoding="utf-8")
    return html_path

def write_template(items: list[MediaItem], out_dir: Path) -> Path:
    path = out_dir / "rename_map_template.tsv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        for item in items:
            writer.writerow([item.path.name, ""])
    return path

def command_scan(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"目录不存在：{root}", file=sys.stderr)
        return 1
    items = scan_media(root, args.recursive)
    if not items:
        print("没有找到可处理素材。", file=sys.stderr)
        return 1
    out_dir = Path(args.out).expanduser().resolve() if args.out else Path(tempfile.mkdtemp(prefix="fcs-rename-"))
    thumb_dir = out_dir / "thumbs"
    try:
        run_quicklook([item.path for item in items], thumb_dir, args.thumb_size)
    except Exception as exc:
        print(f"扫描停止：{exc}", file=sys.stderr)
        return 1
    missing = [item.path.name for item in items if thumbnail_for(item, thumb_dir) is None]
    if missing:
        print(
            "扫描停止：缩略图数量不足，缺少：" + "、".join(missing),
            file=sys.stderr,
        )
        return 1
    sheet = write_contact_sheet(items, out_dir, f"素材联系表：{root}")
    template = write_template(items, out_dir)
    print(f"素材总数：{len(items)}")
    print(f"联系表：{sheet}")
    print(f"映射模板：{template}")
    for item in items:
        print(f"{item.index:03d}\t{item.created_text}\t{item.path.name}")
    return 0


def run_analysis_command(args: argparse.Namespace) -> int:
    script = Path(__file__).with_name("analyze_media_assets.py")
    command = [sys.executable, str(script), "--cache", args.cache, args.command]
    if getattr(args, "root", ""):
        command.extend(["--root", args.root])
    if getattr(args, "out", ""):
        command.extend(["--out", args.out])
    if getattr(args, "recursive", False):
        command.append("--recursive")
    if args.command == "analyze":
        command.extend(["--workers", str(args.workers)])
        command.extend(["--frame-width", str(args.frame_width)])
        command.extend(["--language", args.language])
        if args.keep_audio:
            command.append("--keep-audio")
    return subprocess.run(command, check=False).returncode

def read_map(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        for lineno, row in enumerate(reader, 1):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) < 2:
                raise ValueError(f"第 {lineno} 行少于 2 列")
            old, new = row[0].strip(), row[1].strip()
            if old and new:
                rows.append((old, new))
    return rows

def validate_mapping(root: Path, rows: list[tuple[str, str]]) -> None:
    root = root.expanduser().resolve()
    if not rows:
        raise ValueError("映射为空")
    old_names = [old for old, _ in rows]
    new_names = [new for _, new in rows]
    duplicate_sources = sorted({x for x in old_names if old_names.count(x) > 1})
    if duplicate_sources:
        raise ValueError("原文件名重复：" + ", ".join(duplicate_sources))
    duplicate_targets = sorted({x for x in new_names if new_names.count(x) > 1})
    if duplicate_targets:
        raise ValueError("目标文件名重复：" + ", ".join(duplicate_targets))
    for old, new in rows:
        old_path = root / old
        new_path = root / new
        if not old_path.exists():
            raise ValueError(f"原文件不存在：{old}")
        if old_path.resolve().parent != root:
            raise ValueError(f"原文件不在目标目录内：{old}")
        if "/" in new or "\\" in new or new in {".", ".."}:
            raise ValueError(f"目标文件名非法：{new}")
        if old_path.suffix != new_path.suffix:
            raise ValueError(f"扩展名不一致：{old} -> {new}")
        if not NAME_RE.match(new):
            raise ValueError(f"目标文件名不符合格式：{new}")
        if new_path.exists() and new not in old_names:
            raise ValueError(f"目标文件已存在：{new}")

def apply_mapping(root: Path, rows: list[tuple[str, str]], dry_run: bool) -> None:
    root = root.expanduser().resolve()
    validate_mapping(root, rows)
    if dry_run:
        return
    token = uuid.uuid4().hex[:12]
    staged: list[tuple[Path, Path, Path]] = []
    try:
        for old, new in rows:
            old_path = root / old
            tmp_path = root / f".fcs-renaming-{token}-{old_path.name}"
            if tmp_path.exists():
                raise ValueError(f"临时文件已存在：{tmp_path.name}")
            staged.append((tmp_path, root / new, old_path))
            old_path.rename(tmp_path)
        for tmp_path, new_path, _old_path in staged:
            tmp_path.rename(new_path)
    except Exception as exc:
        rollback_errors: list[str] = []
        for tmp_path, new_path, old_path in reversed(staged):
            current_path = new_path if new_path.exists() else tmp_path
            if old_path.exists() or not current_path.exists():
                continue
            try:
                current_path.rename(old_path)
            except Exception as rollback_exc:
                rollback_errors.append(f"{current_path.name} -> {old_path.name}：{rollback_exc}")
        if rollback_errors:
            detail = "；".join(rollback_errors)
            raise RuntimeError(f"重命名失败，且自动恢复不完整：{detail}") from exc
        raise

def command_apply(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    map_path = Path(args.map).expanduser().resolve()
    if not root.is_dir():
        print(f"目录不存在：{root}", file=sys.stderr)
        return 1
    try:
        rows = read_map(map_path)
        apply_mapping(root, rows, args.dry_run)
    except Exception as exc:
        print(f"重命名停止：{exc}", file=sys.stderr)
        return 1
    print("检查通过" if args.dry_run else "已完成素材重命名")
    print(f"素材总数：{len(rows)}")
    return 0

def command_verify(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    items = scan_media(root, args.recursive)
    names = [item.path.name for item in items]
    numbered = [name for name in names if NAME_RE.match(name)]
    nums = []
    for name in numbered:
        try:
            nums.append(int(name.split("_", 1)[0]))
        except ValueError:
            pass
    nums.sort()
    continuous = nums == list(range(1, len(nums) + 1))
    leftovers = [name for name in names if name.startswith("IMG_") or name.startswith("dji_mimo_")]
    print(f"素材总数：{len(items)}")
    print(f"符合命名格式：{len(numbered)}")
    print(f"编号连续：{'是' if continuous else '否'}")
    if nums:
        print(f"编号范围：{nums[0]:03d} 到 {nums[-1]:03d}")
    print(f"残留旧文件名：{'无' if not leftovers else len(leftovers)}")
    return 0 if continuous and len(numbered) == len(items) and not leftovers else 1

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze, scan, apply, and verify media renaming maps."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    preflight = sub.add_parser("preflight", help="check the fast safe analysis environment")
    preflight.add_argument("--cache", default=DEFAULT_CACHE)
    preflight.add_argument("--root", default="")
    preflight.add_argument("--recursive", action="store_true")
    preflight.set_defaults(func=run_analysis_command)
    analyze = sub.add_parser("analyze", help="analyze pictures and original sound")
    analyze.add_argument("--cache", default=DEFAULT_CACHE)
    analyze.add_argument("--root", required=True)
    analyze.add_argument("--out", default="")
    analyze.add_argument("--recursive", action="store_true")
    analyze.add_argument("--workers", type=int, default=4)
    analyze.add_argument("--frame-width", type=int, default=720)
    analyze.add_argument("--language", default="zh")
    analyze.add_argument("--keep-audio", action="store_true")
    analyze.set_defaults(func=run_analysis_command)
    scan = sub.add_parser("scan", help="scan media and build a contact sheet")
    scan.add_argument("--root", default=".")
    scan.add_argument("--out", default="")
    scan.add_argument("--recursive", action="store_true")
    scan.add_argument("--thumb-size", type=int, default=360)
    scan.set_defaults(func=command_scan)
    apply = sub.add_parser("apply", help="apply a TSV rename map")
    apply.add_argument("--root", default=".")
    apply.add_argument("--map", required=True)
    apply.add_argument("--dry-run", action="store_true")
    apply.set_defaults(func=command_apply)
    verify = sub.add_parser("verify", help="verify current media names")
    verify.add_argument("--root", default=".")
    verify.add_argument("--recursive", action="store_true")
    verify.set_defaults(func=command_verify)
    return parser

def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())
