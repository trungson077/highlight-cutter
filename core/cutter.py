"""FFmpeg video cutting."""

import platform
import subprocess
from pathlib import Path

from config import FFMPEG_BIN
from core.context import PipelineContext


def ts_to_secs(ts: str) -> float:
    """Parse HH:MM:SS or HH:MM:SS,mmm to seconds."""
    parts = ts.replace(",", ".").split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


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
    ext = Path(video_path).suffix
    total = len(highlights)

    video_codec = detect_hw_encoder()
    encoder_name = video_codec[1]
    ctx.log(f"    Encoder: {encoder_name}")

    for i, h in enumerate(highlights, 1):
        ctx.check_cancelled()

        safe_title = (
            h["title"].replace(" ", "_").replace("'", "").replace('"', "")
        )
        safe_title = "".join(
            c for c in safe_title if c.isalnum() or c in "_-"
        )
        output_name = f"{i:02d}_{safe_title}{ext}"
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
            h["start_time"],
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
                ctx.log(
                    f"    [{i}/{total}] FAILED: {output_name} - {stderr[:150]}"
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
