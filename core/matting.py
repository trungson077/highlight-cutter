"""Background removal using RobustVideoMatting."""

import random
import sys
import platform
from pathlib import Path

from core.context import PipelineContext

_RVM_DIR = Path(__file__).resolve().parent.parent / "RobustVideoMatting"

_model_cache = None
_device_cache = None


def _load_model():
    """Load RobustVideoMatting model from local files (no internet needed)."""
    global _model_cache, _device_cache
    if _model_cache is not None:
        return _model_cache, _device_cache

    import torch

    # Add RVM dir to path so we can import the model module
    rvm_str = str(_RVM_DIR)
    if rvm_str not in sys.path:
        sys.path.insert(0, rvm_str)
    from model import MattingNetwork

    # Load model from local weights file
    weights_path = _RVM_DIR / "rvm_mobilenetv3.pth"
    model = MattingNetwork("mobilenetv3")
    model.load_state_dict(
        torch.load(weights_path, map_location="cpu", weights_only=True)
    )

    if platform.system() == "Darwin" and torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"

    model = model.to(device)
    _model_cache = model
    _device_cache = device
    return model, device


def remove_background(
    clip_paths: list,
    background_sources: list | str,
    output_dir: Path,
    ctx: PipelineContext,
) -> list:
    """Remove background from clips and replace with given background(s).

    background_sources can be a single path string or a list of paths.
    When multiple backgrounds are provided, each clip gets a random one.

    Returns list of new clip paths (matted versions).
    """
    # Normalize to list
    if isinstance(background_sources, str):
        background_sources = [background_sources]
    import torch

    rvm_str = str(_RVM_DIR)
    if rvm_str not in sys.path:
        sys.path.insert(0, rvm_str)
    from inference import convert_video

    output_dir.mkdir(parents=True, exist_ok=True)

    ctx.log("  Dang tai model RobustVideoMatting...")
    model, device = _load_model()
    ctx.log(f"  Model da san sang (device: {device})")

    total = len(clip_paths)
    matted_paths = []

    for i, clip_path in enumerate(clip_paths):
        ctx.check_cancelled()

        if clip_path is None:
            matted_paths.append(None)
            continue

        clip_file = Path(clip_path)
        output_path = output_dir / clip_file.name

        # Skip if already processed
        if output_path.exists() and output_path.stat().st_size > 1000:
            ctx.log(f"    [{i+1}/{total}] Da co: {clip_file.name}")
            ctx.set_step(
                3,
                "running",
                (i + 1) / total,
                f"Da co {i+1}/{total}: {clip_file.name}",
            )
            matted_paths.append(str(output_path))
            continue

        ctx.set_step(
            3,
            "running",
            i / total,
            f"Dang tach BG {i+1}/{total}: {clip_file.name}",
        )
        bg = random.choice(background_sources)
        ctx.log(f"    [{i+1}/{total}] Dang tach BG: {clip_file.name} (bg: {Path(bg).name})")

        try:
            convert_video(
                model,
                input_source=str(clip_file),
                input_resize=(854, 480),
                output_type="video",
                output_composition=str(output_path),
                output_video_mbps=3,
                downsample_ratio=0.25,
                seq_chunk=32,
                num_workers=0,
                device=device,
                dtype=torch.float32,
                background_source=bg,
            )
            matted_paths.append(str(output_path))
            ctx.log(f"    [{i+1}/{total}] Xong: {clip_file.name}")
        except Exception as e:
            ctx.log(f"    [{i+1}/{total}] LOI: {clip_file.name}: {e}")
            matted_paths.append(
                None
            )  # Mark as failed, don't fallback to missing original

    return matted_paths
