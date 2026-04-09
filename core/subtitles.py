"""Subtitle extraction and burning for highlight clips."""

import json
import re
import subprocess
import tempfile
from pathlib import Path

from config import FFMPEG_BIN
from core.context import PipelineContext
from core.cutter import ts_to_secs

# Impact font, white text, black outline — matching LeonardoAIEditer style
SUB_STYLE = (
    "FontName=Impact,"
    "FontSize=24,"
    "Bold=1,"
    "PrimaryColour=&HFFFFFF,"
    "OutlineColour=&H000000,"
    "BorderStyle=1,"
    "Outline=2,"
    "Shadow=1,"
    "Alignment=2"
)


def _parse_srt(srt_content: str) -> list:
    """Parse SRT content into list of (start_secs, end_secs, text) tuples."""
    entries = []
    blocks = re.split(r"\n\s*\n", srt_content.strip())
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        ts_match = re.match(
            r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})",
            lines[1],
        )
        if not ts_match:
            continue
        start = ts_to_secs(ts_match.group(1))
        end = ts_to_secs(ts_match.group(2))
        text = "\n".join(lines[2:])
        if text.strip():
            entries.append((start, end, text))
    return entries


def _secs_to_srt_ts(secs: float) -> str:
    """Convert seconds to SRT timestamp HH:MM:SS,mmm."""
    if secs < 0:
        secs = 0
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    ms = int(round((secs % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _extract_clip_srt(
    srt_content: str, clip_start: float, clip_end: float
) -> str:
    """Extract and time-shift SRT entries for a clip's time range."""
    entries = _parse_srt(srt_content)
    clip_entries = []

    for start, end, text in entries:
        if end <= clip_start or start >= clip_end:
            continue
        new_start = max(0.0, start - clip_start)
        new_end = min(clip_end - clip_start, end - clip_start)
        if new_end > new_start:
            clip_entries.append((new_start, new_end, text))

    lines = []
    for i, (start, end, text) in enumerate(clip_entries, 1):
        lines.append(str(i))
        lines.append(f"{_secs_to_srt_ts(start)} --> {_secs_to_srt_ts(end)}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines)


def burn_subtitles_on_clips(
    all_highlights: list,
    batch_dir: Path,
    ctx: PipelineContext,
) -> int:
    """Burn subtitles directly onto highlight clips (in-place).

    Returns number of clips successfully subtitled.
    """
    subs_dir = batch_dir / "subs"
    srt_cache = {}

    # Load cache of already-subtitled clips
    done_file = batch_dir / "_subs_done.json"
    done_set = set()
    if done_file.exists():
        try:
            done_set = set(json.loads(done_file.read_text(encoding="utf-8")))
        except Exception:
            pass

    # Collect all clips to process
    clips_to_process = []
    for entry in all_highlights:
        video_name = entry["video"]
        srt_path = subs_dir / f"{video_name}.srt"
        if srt_path.exists() and video_name not in srt_cache:
            srt_cache[video_name] = srt_path.read_text(encoding="utf-8")

        for h in entry["highlights"]:
            if "_clip_path" in h and Path(h["_clip_path"]).exists():
                clips_to_process.append((h, video_name))

    total = len(clips_to_process)
    if total == 0:
        return 0

    ctx.log(f"  Dang gan phu de cho {total} clip...")
    ok_count = 0

    for i, (h, video_name) in enumerate(clips_to_process):
        ctx.check_cancelled()

        clip_path = Path(h["_clip_path"])

        # Skip if already burned subs
        if str(clip_path) in done_set:
            ctx.set_step(
                4,
                "running",
                (i + 1) / total,
                f"Da co sub {i+1}/{total}: {clip_path.name}",
            )
            ok_count += 1
            continue

        srt_content = srt_cache.get(video_name, "")
        if not srt_content:
            continue

        clip_start = ts_to_secs(h["start_time"])
        clip_end = ts_to_secs(h["end_time"])
        clip_srt = _extract_clip_srt(srt_content, clip_start, clip_end)

        if not clip_srt.strip():
            continue

        ctx.set_step(
            4,
            "running",
            i / total,
            f"Dang gan sub {i+1}/{total}: {clip_path.name}",
        )
        ctx.log(f"    [{i+1}/{total}] Dang gan sub: {clip_path.name}")

        srt_temp = None
        try:
            # Write SRT to temp file
            with tempfile.NamedTemporaryFile(
                suffix=".srt",
                mode="w",
                delete=False,
                encoding="utf-8",
                dir="/tmp",
            ) as f:
                f.write(clip_srt)
                srt_temp = f.name

            # Output to temp file next to original
            temp_output = clip_path.with_suffix(".tmp.mp4")

            vf_filter = f"subtitles={srt_temp}:force_style='{SUB_STYLE}'"

            cmd = [
                FFMPEG_BIN,
                "-i",
                str(clip_path),
                "-vf",
                vf_filter,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "copy",
                "-y",
                str(temp_output),
            ]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            ctx.current_process = proc
            _, stderr = proc.communicate(timeout=300)
            ctx.current_process = None

            if proc.returncode == 0 and temp_output.exists():
                # Replace original with subtitled version
                temp_output.replace(clip_path)
                done_set.add(str(clip_path))
                ok_count += 1
                ctx.log(f"    [{i+1}/{total}] Xong: {clip_path.name}")
            else:
                ctx.log(
                    f"    [{i+1}/{total}] LOI: {clip_path.name} - {stderr[:200]}"
                )
                if temp_output.exists():
                    temp_output.unlink()

        except subprocess.TimeoutExpired:
            proc.kill()
            ctx.current_process = None
            ctx.log(f"    [{i+1}/{total}] TIMEOUT: {clip_path.name}")
            temp_output = clip_path.with_suffix(".tmp.mp4")
            if temp_output.exists():
                temp_output.unlink()
        except Exception as e:
            ctx.current_process = None
            ctx.log(f"    [{i+1}/{total}] LOI: {clip_path.name}: {e}")
        finally:
            if srt_temp:
                try:
                    Path(srt_temp).unlink(missing_ok=True)
                except Exception:
                    pass

    # Save done marker
    try:
        done_file.write_text(
            json.dumps(list(done_set), ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass

    return ok_count
