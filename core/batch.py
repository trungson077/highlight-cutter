"""Batch directory management."""

import json
from pathlib import Path


def find_or_create_batch_dir(output_dir: Path, video_queue: list[str]) -> Path:
    """Reuse existing batch if it has the same video list, else create new."""
    output_dir.mkdir(parents=True, exist_ok=True)
    current_videos = sorted(Path(v).name for v in video_queue)

    existing = sorted(
        (
            d
            for d in output_dir.iterdir()
            if d.is_dir() and d.name.startswith("batch_")
        ),
        key=lambda d: d.name,
    )

    # Check existing batches (latest first) for matching video list
    for batch_dir in reversed(existing):
        manifest = batch_dir / "_videos.json"
        if manifest.exists():
            try:
                saved = json.loads(manifest.read_text(encoding="utf-8"))
                if sorted(saved) == current_videos:
                    return batch_dir
            except (json.JSONDecodeError, TypeError):
                pass

    # No match — create next batch number
    last_num = 0
    for d in existing:
        try:
            num = int(d.name.split("_", 1)[1])
            last_num = max(last_num, num)
        except (ValueError, IndexError):
            pass
    return output_dir / f"batch_{last_num + 1}"


def save_manifest(batch_dir: Path, video_queue: list[str]):
    """Save video list manifest for batch reuse."""
    current_videos = sorted(Path(v).name for v in video_queue)
    manifest = batch_dir / "_videos.json"
    manifest.write_text(
        json.dumps(current_videos, ensure_ascii=False), encoding="utf-8"
    )
