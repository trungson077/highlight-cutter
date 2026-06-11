"""FFmpeg video cutting."""

import platform
import subprocess
from pathlib import Path

from config import FFMPEG_BIN
from core.context import PipelineContext


def ts_to_secs(ts: str) -> float:
    """Parse HH:MM:SS or HH:MM:SS,mmm or MM:SS,mmm to seconds."""
    parts = ts.replace(",", ".").split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def normalize_ts(ts: str) -> str:
    """Normalize any timestamp to HH:MM:SS.mmm for FFmpeg.

    Handles formats like:
      00:01:34     -> 00:01:34.000
      00:01:34,500 -> 00:01:34.500
      30:13:180    -> 00:30:13.180  (MM:SS:mmm misinterpreted as HH:MM:SS)
      01:34.500    -> 00:01:34.500  (MM:SS.mmm)
    """
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")

    if len(parts) == 3:
        p0, p1, p2 = parts
        # Detect MM:SS:mmm — 3rd part has no dot AND is exactly 3 digits (milliseconds)
        # e.g. "30:13:180" means 30m 13s 180ms, "33:00:000" means 33m 00s 000ms
        # Normal HH:MM:SS would have 1-2 digit seconds, not 3-digit
        if "." not in p2 and p2.isdigit() and len(p2) == 3:
            # It's MM:SS:mmm
            h = 0
            m = int(p0)
            s_ms = f"{p1}.{p2}"
        else:
            h = int(p0)
            m = int(p1)
            s_ms = p2
    elif len(parts) == 2:
        h = 0
        m = int(parts[0])
        s_ms = parts[1]
    else:
        h = 0
        m = 0
        s_ms = parts[0]

    # Parse seconds and milliseconds
    if "." in str(s_ms):
        s_part, ms_part = str(s_ms).split(".", 1)
        s = int(s_part)
        ms = ms_part.ljust(3, "0")[:3]
    else:
        s = int(s_ms)
        ms = "000"

    return f"{h:02d}:{m:02d}:{s:02d}.{ms}"


def detect_hw_encoder() -> list[str]:
    """Detect available hardware encoder. Returns codec args."""
    if platform.system() == "Darwin":
        try:
            r = subprocess.run(
                [FFMPEG_BIN, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "h264_videotoolbox" in r.stdout:
                return ["-c:v", "h264_videotoolbox", "-q:v", "65"]
        except Exception:
            pass
    return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"]


def cut_video(
    video_path: str, highlights: list, clips_dir: Path, ctx: PipelineContext
) -> list:
    """Cut video into clips based on highlights."""
    clip_paths = []
    used_names = set()
    ext = Path(video_path).suffix
    total = len(highlights)

    video_codec = detect_hw_encoder()
    encoder_name = video_codec[1]
    ctx.log(f"    Encoder: {encoder_name}")

    for i, h in enumerate(highlights, 1):
        ctx.check_cancelled()

        safe_title = h["title"].replace("'", "").replace('"', "")
        safe_title = "".join(
            c for c in safe_title if c.isalnum() or c in " _-"
        ).strip()
        if not safe_title:
            safe_title = f"clip_{i:02d}"
        output_name = f"{safe_title}{ext}"
        # Add a numeric suffix only when the name collides with one
        # already used in this run (e.g. duplicate titles).
        if output_name in used_names:
            n = 2
            while f"{safe_title} ({n}){ext}" in used_names:
                n += 1
            output_name = f"{safe_title} ({n}){ext}"
        used_names.add(output_name)
        output_path = clips_dir / output_name

        # Skip if clip already exists
        if output_path.exists() and output_path.stat().st_size > 1000:
            ctx.set_step(
                2,
                "running",
                i / total,
                f"Da co {i}/{total}: {h['title'][:40]}",
            )
            clip_paths.append(output_path)
            continue

        ctx.set_step(
            2,
            "running",
            i / total,
            f"Dang cat {i}/{total}: {h['title'][:40]}",
        )

        duration_secs = h.get("duration_seconds")
        if not duration_secs:
            duration_secs = ts_to_secs(h["end_time"]) - ts_to_secs(
                h["start_time"]
            )

        cmd = [
            FFMPEG_BIN,
            "-ss",
            normalize_ts(h["start_time"]),
            "-i",
            video_path,
            "-t",
            str(duration_secs),
            *video_codec,
            "-c:a",
            "aac",
            "-avoid_negative_ts",
            "make_zero",
            "-threads",
            "0",
            str(output_path),
            "-y",
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            ctx.current_process = proc
            _, stderr = proc.communicate(timeout=120)
            ctx.current_process = None

            if proc.returncode == 0:
                ctx.log(f"    [{i}/{total}] Cut: {output_name}")
                clip_paths.append(output_path)
            else:
                # Show last 500 chars of stderr (skip ffmpeg banner to see actual error)
                ctx.log(
                    f"    [{i}/{total}] FAILED: {output_name} - {stderr[-500:]}"
                )
                clip_paths.append(None)
        except subprocess.TimeoutExpired:
            proc.kill()
            ctx.current_process = None
            ctx.log(f"    [{i}/{total}] TIMEOUT: {output_name}")
            clip_paths.append(None)
        except Exception as e:
            ctx.current_process = None
            ctx.log(f"    [{i}/{total}] ERROR: {output_name}: {e}")
            clip_paths.append(None)

    return clip_paths
