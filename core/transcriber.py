"""Whisper transcription."""

import re
from pathlib import Path

from core.context import PipelineContext


def format_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe_video(
    video_path: str, batch_dir: Path, ctx: PipelineContext
) -> tuple[str, int]:
    """Transcribe video with faster-whisper and save SRT."""
    from faster_whisper import WhisperModel

    ctx.check_cancelled()
    ctx.set_step(0, "running", 0.1, "Dang tai model Whisper...")

    model = WhisperModel("tiny", device="cpu", compute_type="int8")

    ctx.set_step(0, "running", 0.2, "Dang phien am...")
    segments, _info = model.transcribe(
        video_path, beam_size=5, word_timestamps=True
    )

    srt_lines = []
    seg_count = 0
    raw_seg_count = 0

    for seg in segments:
        ctx.check_cancelled()
        raw_seg_count += 1

        if not seg.words:
            seg_count += 1
            start = format_srt_time(seg.start)
            end = format_srt_time(seg.end)
            srt_lines.append(f"{seg_count}")
            srt_lines.append(f"{start} --> {end}")
            srt_lines.append(seg.text.strip())
            srt_lines.append("")
        else:
            sentence_words = []
            for word in seg.words:
                sentence_words.append(word)
                if re.search(r"[.?!]$", word.word.strip()):
                    seg_count += 1
                    s_start = format_srt_time(sentence_words[0].start)
                    s_end = format_srt_time(sentence_words[-1].end)
                    text = "".join(w.word for w in sentence_words).strip()
                    srt_lines.append(f"{seg_count}")
                    srt_lines.append(f"{s_start} --> {s_end}")
                    srt_lines.append(text)
                    srt_lines.append("")
                    sentence_words = []
            if sentence_words:
                seg_count += 1
                s_start = format_srt_time(sentence_words[0].start)
                s_end = format_srt_time(sentence_words[-1].end)
                text = "".join(w.word for w in sentence_words).strip()
                srt_lines.append(f"{seg_count}")
                srt_lines.append(f"{s_start} --> {s_end}")
                srt_lines.append(text)
                srt_lines.append("")

        if raw_seg_count % 50 == 0:
            ctx.set_step(
                0,
                "running",
                min(
                    0.2
                    + 0.7 * (raw_seg_count / max(raw_seg_count + 50, 100)),
                    0.95,
                ),
                f"Da phien am {seg_count} doan...",
            )

    srt_content = "\n".join(srt_lines)

    ctx.set_step(0, "running", 0.95, "Dang luu file SRT...")
    subs_dir = batch_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)
    srt_path = subs_dir / f"{Path(video_path).stem}.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_content)

    return srt_content, seg_count
