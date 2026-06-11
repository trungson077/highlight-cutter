"""Topic grouping with Claude, YouTube SEO, and topic concatenation."""

import json
import shutil
from datetime import datetime
from pathlib import Path

from config import TOPIC_GROUP_PROMPT, YOUTUBE_SEO_PROMPT
from core.context import PipelineContext
from core.claude_runner import claude_base_cmd, run_claude_with_retry, parse_json_response
from core.concat import concat_clips


def group_topics_with_claude(
    clips_data: list, model: str, ctx: PipelineContext
) -> dict:
    """Use Claude to intelligently group similar topics."""
    simple_clips = []
    for i, c in enumerate(clips_data):
        simple_clips.append(
            {
                "index": i,
                "topic": c.get("topic", "misc"),
                "title": c.get("title", ""),
                "source": c.get("_source_video", ""),
            }
        )

    prompt = TOPIC_GROUP_PROMPT.format(
        clips_json=json.dumps(simple_clips, indent=2)
    )
    cmd = claude_base_cmd(model)

    response = run_claude_with_retry(
        cmd, prompt, ctx, timeout=120, label="Gop chu de"
    )
    if response is None:
        ctx.log("    Gop chu de that bai sau 3 lan thu, dung chu de goc")
        return {}

    grouping = parse_json_response(response, "{")
    if grouping is None:
        ctx.log("    Khong the phan tich ket qua gop nhom")
        return {}

    result = {}
    for group_name, indices in grouping.items():
        group_clips = []
        for idx in indices:
            if 0 <= idx < len(clips_data):
                group_clips.append(clips_data[idx])
        if group_clips:
            result[group_name.strip().lower()] = group_clips

    return result


def generate_youtube_seo(
    topic: str, clips: list, model: str, ctx: PipelineContext
) -> dict:
    """Use Claude to generate YouTube title, description, and tags."""
    total_secs = sum(c.get("duration_seconds", 0) for c in clips)
    mins = int(total_secs // 60)
    secs = int(total_secs % 60)
    total_duration = f"{mins}m {secs}s"

    clips_info_lines = []
    running_time = 0
    for i, c in enumerate(clips, 1):
        dur = c.get("duration_seconds", 0)
        ts_min = int(running_time // 60)
        ts_sec = int(running_time % 60)
        clips_info_lines.append(
            f'{i}. [{ts_min}:{ts_sec:02d}] "{c["title"]}" '
            f"(from: {c.get('_source_video', '?')}) - {c.get('description', '')[:150]}"
        )
        running_time += dur

    prompt = YOUTUBE_SEO_PROMPT.format(
        topic=topic,
        total_duration=total_duration,
        clips_info="\n".join(clips_info_lines),
    )
    cmd = claude_base_cmd(model)

    response = run_claude_with_retry(
        cmd, prompt, ctx, timeout=120, label="YouTube SEO"
    )
    if response is None:
        return {}

    result = parse_json_response(response, "{")
    return result if result is not None else {}


def _concat_one_format(
    topic_map: dict,
    max_clips: int,
    model: str,
    topics_dir: Path,
    ctx: PipelineContext,
    clip_path_key: str = "_clip_path",
    generate_seo: bool = True,
) -> tuple[int, dict]:
    """Concatenate clips for a single format (youtube or tiktok).

    Returns (topic_count, seo_cache) where seo_cache maps topic -> seo_data.
    """
    topics_dir.mkdir(parents=True, exist_ok=True)

    # Separate grouped (>1 clip) from ungrouped (1 clip)
    grouped = {k: v for k, v in topic_map.items() if len(v) > 1}
    ungrouped_clips = []
    for k, v in topic_map.items():
        if len(v) == 1:
            ungrouped_clips.extend(v)

    # Copy ungrouped clips to a separate folder
    if ungrouped_clips:
        ungrouped_dir = topics_dir / "ungrouped"
        ungrouped_dir.mkdir(parents=True, exist_ok=True)
        for c in ungrouped_clips:
            clip_p = c.get(clip_path_key) or c.get("_clip_path")
            if clip_p:
                src = Path(clip_p)
                if src.exists():
                    shutil.copy2(src, ungrouped_dir / src.name)
        ctx.log(
            f"  {len(ungrouped_clips)} clip le da copy vao '{topics_dir.name}/ungrouped'"
        )

    if not grouped:
        ctx.log(f"  [{topics_dir.name}] Khong co nhom nao co hon 1 clip de ghep.")
        return 0, {}

    total_topics = len(grouped)
    seo_cache = {}

    for t_idx, (topic, clips) in enumerate(grouped.items()):
        ctx.check_cancelled()

        if topic != "best_highlights" and len(clips) > max_clips:
            ctx.log(
                f"  Chu de '{topic}': cat giam {len(clips)} -> {max_clips} clip (toi da)"
            )
            clips = clips[:max_clips]

        safe_topic = topic.replace(" ", "_").replace("/", "-")
        safe_topic = "".join(
            c for c in safe_topic if c.isalnum() or c in "_-"
        )
        if not safe_topic:
            safe_topic = "misc"

        topic_dir = topics_dir / safe_topic
        topic_dir.mkdir(parents=True, exist_ok=True)

        ctx.set_step(
            5,
            "running",
            (t_idx + 0.3) / total_topics,
            f"[{topics_dir.name}] Dang ghep '{topic}': {len(clips)} clip",
        )
        ctx.log(f"  [{topics_dir.name}] Chu de '{topic}': {len(clips)} clip")

        # Concat video - only use clips that actually exist
        clip_paths = []
        for c in clips:
            cp = c.get(clip_path_key) or c.get("_clip_path")
            if cp and Path(cp).exists() and Path(cp).stat().st_size > 1000:
                clip_paths.append(cp)
        if not clip_paths:
            ctx.log(f"    CANH BAO: khong co clip hop le cho '{topic}', bo qua")
            continue

        if len(clip_paths) < len(clips):
            ctx.log(f"    CANH BAO: chi co {len(clip_paths)}/{len(clips)} clip hop le")

        output_video = topic_dir / f"{safe_topic}_compilation.mp4"

        if len(clip_paths) == 1:
            shutil.copy2(clip_paths[0], output_video)
        else:
            if not concat_clips(clip_paths, output_video, ctx):
                continue

        ctx.log(f"    Da tao: {output_video.name}")

        # Generate YouTube SEO with Claude (only once, reuse for tiktok)
        if generate_seo:
            ctx.set_step(
                5,
                "running",
                (t_idx + 0.7) / total_topics,
                f"Dang tao tieu de YouTube cho '{topic}'...",
            )
            ctx.log("    Dang tao tieu de & mo ta YouTube...")

            seo_data = generate_youtube_seo(topic, clips, model, ctx)
            seo_cache[topic] = seo_data
        else:
            seo_data = {}

        # Determine final title from SEO data
        final_title = seo_data.get("youtube_title") or f"Best of {topic.title()} - Comedy Highlights Compilation"

        # Create filesystem-safe name from SEO title
        safe_final = final_title
        for ch in '/\\:*?"<>\n\r':
            safe_final = safe_final.replace(ch, "")
        safe_final = safe_final.strip()
        if not safe_final:
            safe_final = safe_topic

        # Rename video file to match SEO title
        new_video = topic_dir / f"{safe_final}.mp4"
        if output_video.exists() and new_video != output_video:
            output_video.rename(new_video)
            ctx.log(f"    Da doi ten video: {new_video.name}")

        # Write YouTube info file
        yt_path = topic_dir / f"{safe_topic}_youtube.txt"
        with open(yt_path, "w", encoding="utf-8") as f:
            f.write(f"TITLE:\n{final_title}\n\n")
            if seo_data.get("youtube_description"):
                f.write(
                    f"DESCRIPTION:\n{seo_data['youtube_description']}\n\n"
                )
            if seo_data.get("youtube_tags"):
                f.write(f"TAGS:\n{seo_data['youtube_tags']}\n\n")

        ctx.log(f"    Title: {final_title}")

        # Write detailed info file
        info_path = topic_dir / f"{safe_topic}_info.txt"
        with open(info_path, "w", encoding="utf-8") as f:
            f.write(f"Topic: {topic.title()}\n")
            f.write(
                f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            )
            f.write(f"Total clips: {len(clips)}\n")

            total_secs = sum(c.get("duration_seconds", 0) for c in clips)
            f.write(
                f"Total duration: {int(total_secs // 60)}m {int(total_secs % 60)}s\n"
            )
            f.write(f"{'='*60}\n\n")

            running_time = 0
            for i, c in enumerate(clips, 1):
                dur = c.get("duration_seconds", 0)
                ts_min = int(running_time // 60)
                ts_sec = int(running_time % 60)
                f.write(f"--- Clip {i} [{ts_min}:{ts_sec:02d}] ---\n")
                f.write(f"Source: {c.get('_source_video', 'unknown')}\n")
                f.write(f"Title: {c['title']}\n")
                f.write(
                    f"Original time: {c['start_time']} -> {c['end_time']} ({dur}s)\n"
                )
                f.write(f"Description: {c['description']}\n")
                if c.get("key_quotes"):
                    f.write(f"Key quotes: {'; '.join(c['key_quotes'])}\n")
                f.write(f"Humor level: {c.get('humor_level', '?')}\n")
                f.write(f"Context: {c.get('context', '')}\n\n")
                running_time += dur

    ctx.log(f"  Da tao {total_topics} thu muc chu de trong {topics_dir}")
    return total_topics, seo_cache


def concat_topics(
    topic_map: dict,
    max_clips: int,
    model: str,
    batch_dir: Path,
    ctx: PipelineContext,
    has_tiktok: bool = False,
) -> int:
    """Concatenate clips by topic and generate YouTube metadata.

    When has_tiktok=True, also produces tiktok/ compilations using
    _tiktok_clip_path from each clip.
    Output structure: topics/youtube/... and topics/tiktok/...
    """
    ctx.check_cancelled()

    topics_dir = batch_dir / "topics"
    # Clean old topics dir to avoid stale cache
    if topics_dir.exists():
        shutil.rmtree(topics_dir)
        ctx.log("  Da xoa thu muc topics cu")

    if has_tiktok:
        # Two sub-folders: youtube/ and tiktok/
        yt_dir = topics_dir / "youtube"
        tk_dir = topics_dir / "tiktok"

        ctx.log("  Dang ghep video YouTube (ngang)...")
        yt_count, seo_cache = _concat_one_format(
            topic_map, max_clips, model, yt_dir, ctx,
            clip_path_key="_clip_path",
            generate_seo=True,
        )

        ctx.log("  Dang ghep video TikTok (doc)...")
        tk_count, _ = _concat_one_format(
            topic_map, max_clips, model, tk_dir, ctx,
            clip_path_key="_tiktok_clip_path",
            generate_seo=False,
        )

        # Apply cached SEO data to tiktok topic folders
        for topic, seo_data in seo_cache.items():
            safe_topic = topic.replace(" ", "_").replace("/", "-")
            safe_topic = "".join(
                c for c in safe_topic if c.isalnum() or c in "_-"
            )
            if not safe_topic:
                safe_topic = "misc"
            tk_topic_dir = tk_dir / safe_topic
            if tk_topic_dir.exists():
                yt_path = tk_topic_dir / f"{safe_topic}_youtube.txt"
                final_title = seo_data.get("youtube_title") or f"Best of {topic.title()} - Comedy Highlights Compilation"
                with open(yt_path, "w", encoding="utf-8") as f:
                    f.write(f"TITLE:\n{final_title}\n\n")
                    if seo_data.get("youtube_description"):
                        f.write(f"DESCRIPTION:\n{seo_data['youtube_description']}\n\n")
                    if seo_data.get("youtube_tags"):
                        f.write(f"TAGS:\n{seo_data['youtube_tags']}\n\n")

        return max(yt_count, tk_count)
    else:
        # No TikTok: single output directly in topics/ (backward compatible)
        count, _ = _concat_one_format(
            topic_map, max_clips, model, topics_dir, ctx,
            clip_path_key="_clip_path",
            generate_seo=True,
        )
        return count
