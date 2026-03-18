"""Video concatenation and clip utilities."""

import re
import subprocess
from pathlib import Path

from config import FFMPEG_BIN
from core.context import PipelineContext
from core.cutter import ts_to_secs


def probe_first_clip(clip_path: str) -> tuple[int, int, float]:
    """Probe a clip for width, height, fps. Returns defaults on failure."""
    try:
        probe = subprocess.run(
            [FFMPEG_BIN, "-i", clip_path, "-hide_banner"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        stderr = probe.stderr
    except Exception:
        return 1920, 1080, 30.0

    width, height = 1920, 1080
    m = re.search(r"(\d{3,5})x(\d{3,5})", stderr)
    if m:
        width, height = int(m.group(1)), int(m.group(2))

    fps = 30.0
    m_fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", stderr)
    if m_fps:
        val = float(m_fps.group(1))
        if 10 < val < 120:
            fps = val

    return width, height, fps


def concat_clips(
    clip_paths: list[str], output_video: Path, ctx: PipelineContext
) -> bool:
    """Concat clips using -filter_complex concat filter."""
    n = len(clip_paths)
    if n == 0:
        return False

    w, h, fps = probe_first_clip(clip_paths[0])

    inputs = []
    filter_parts = []
    for i, cp in enumerate(clip_paths):
        inputs.extend(["-i", cp])
        filter_parts.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:-1:-1,fps={fps},setsar=1,format=yuv420p[v{i}]"
        )
        filter_parts.append(
            f"[{i}:a]aresample=44100,aformat=sample_fmts=fltp:"
            f"channel_layouts=stereo[a{i}]"
        )

    concat_streams = "".join(f"[v{i}][a{i}]" for i in range(n))
    filter_parts.append(f"{concat_streams}concat=n={n}:v=1:a=1[v][a]")
    filter_complex = ";".join(filter_parts)

    cmd = [
        FFMPEG_BIN,
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-y",
        str(output_video),
    ]

    ctx.log(f"    concat filter: {n} clips -> {output_video.name}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        ctx.log(f"    Ghep that bai: {result.stderr[:300]}")
        return False
    return True


def deduplicate_clips(clips: list) -> list:
    """Remove duplicate/overlapping highlights. Keeps longer duration."""

    def _title_words(title: str) -> set:
        return set(title.lower().split())

    kept = []
    for clip in clips:
        is_dup = False
        clip_start = ts_to_secs(clip["start_time"])
        clip_end = ts_to_secs(clip["end_time"])
        clip_dur = clip.get("duration_seconds", clip_end - clip_start)
        clip_words = _title_words(clip.get("title", ""))
        clip_source = clip.get("_source_video", "")

        for j, existing in enumerate(kept):
            ex_source = existing.get("_source_video", "")

            if clip_source == ex_source:
                # Same source video: check time overlap
                ex_start = ts_to_secs(existing["start_time"])
                ex_end = ts_to_secs(existing["end_time"])
                overlap_start = max(clip_start, ex_start)
                overlap_end = min(clip_end, ex_end)
                overlap = max(0, overlap_end - overlap_start)
                shorter = min(clip_end - clip_start, ex_end - ex_start)
                if shorter > 0 and overlap / shorter > 0.5:
                    ex_dur = existing.get(
                        "duration_seconds", ex_end - ex_start
                    )
                    if clip_dur > ex_dur:
                        kept[j] = clip
                    is_dup = True
                    break
            else:
                # Different source: check title similarity
                ex_words = _title_words(existing.get("title", ""))
                if clip_words and ex_words:
                    common = clip_words & ex_words
                    similarity = len(common) / min(
                        len(clip_words), len(ex_words)
                    )
                    if similarity >= 0.7:
                        ex_dur = existing.get(
                            "duration_seconds",
                            ts_to_secs(existing["end_time"])
                            - ts_to_secs(existing["start_time"]),
                        )
                        if clip_dur > ex_dur:
                            kept[j] = clip
                        is_dup = True
                        break

        if not is_dup:
            kept.append(clip)

    return kept


def raw_topic_map(clips: list) -> dict:
    """Group clips by their raw topic."""
    topic_map = {}
    for c in clips:
        topic = c.get("topic", "misc").strip().lower()
        if topic not in topic_map:
            topic_map[topic] = []
        topic_map[topic].append(c)
    return topic_map
