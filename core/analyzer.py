"""Claude highlight analysis — 2-step: segment stories, then select highlights."""

import json

from config import SEGMENTATION_PROMPT, HIGHLIGHT_SELECTION_PROMPT
from core.context import PipelineContext
from core.claude_runner import claude_base_cmd, run_claude_with_retry, parse_json_response


def segment_stories(
    srt_content: str,
    show_name: str,
    model: str,
    system_prompt: str,
    ctx: PipelineContext,
) -> list:
    """Step 1: Identify all complete story/bit boundaries in the transcript."""
    ctx.check_cancelled()

    user_prompt = (
        f"FULL TRANSCRIPT OF: {show_name}\n"
        f"{'='*50}\n"
        f"{srt_content}\n"
        f"{'='*50}"
    )

    cmd = claude_base_cmd(model) + ["--system-prompt", system_prompt]
    ctx.set_step(1, "running", 0.05, "Buoc 1: Dang phan doan cau chuyen...")

    response_text = run_claude_with_retry(
        cmd, user_prompt, ctx, timeout=480, label="Phan doan cau chuyen"
    )
    if response_text is None:
        raise RuntimeError("Phan doan that bai sau khi thu tat ca model")

    ctx.set_step(1, "running", 0.35, "Dang phan tich ket qua phan doan...")

    segments = parse_json_response(response_text, "[")
    if segments is None:
        ctx.log(f"  Khong the parse JSON segments. Preview: {response_text[:300]}")
        return []

    ctx.log(f"  Tim thay {len(segments)} doan cau chuyen hoan chinh:")
    for s in segments:
        dur = s.get("duration_seconds", "?")
        ctx.log(
            f"    #{s['segment_number']} [{s['start_time']} -> {s['end_time']}] "
            f"({dur}s) {s['topic']}: {s.get('summary', '')[:60]}"
        )

    return segments


def select_highlights(
    segments: list,
    model: str,
    highlight_prompt: str,
    ctx: PipelineContext,
) -> list:
    """Step 2: From pre-segmented stories, select the best ones as highlights."""
    ctx.check_cancelled()

    segments_json = json.dumps(segments, ensure_ascii=False, indent=2)
    prompt = highlight_prompt.format(segments_json=segments_json)

    cmd = claude_base_cmd(model)
    ctx.set_step(1, "running", 0.45, "Buoc 2: Dang chon highlight tu cac doan...")

    response_text = run_claude_with_retry(
        cmd, prompt, ctx, timeout=300, label="Chon highlight"
    )
    if response_text is None:
        raise RuntimeError("Chon highlight that bai sau khi thu tat ca model")

    ctx.set_step(1, "running", 0.75, "Dang phan tich ket qua highlight...")

    highlights = parse_json_response(response_text, "[")
    if highlights is None:
        ctx.log(f"  Khong the parse JSON highlights. Preview: {response_text[:300]}")
        return []

    ctx.log(f"  Da chon {len(highlights)} highlight tu {len(segments)} doan:")
    for h in highlights:
        ctx.log(
            f"    [{h['start_time']} -> {h['end_time']}] {h['title']} ({h.get('topic', '?')})"
        )

    return highlights
