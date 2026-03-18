"""Claude CLI wrapper with retry and model fallback."""

import json
import subprocess
import tempfile
import threading
from pathlib import Path

from config import CLAUDE_BIN, MODEL_FALLBACK
from core.context import PipelineContext


def claude_base_cmd(model: str) -> list[str]:
    """Build base claude CLI command with model."""
    return [
        CLAUDE_BIN,
        "-p",
        "--model",
        model,
        "--output-format",
        "text",
        "--no-session-persistence",
    ]


def parse_json_response(text: str, bracket: str = "{"):
    """Parse JSON from Claude response, handling code blocks and extra text.
    bracket: '{' for objects, '[' for arrays.
    Returns parsed JSON or None on failure."""
    import re

    text = text.strip()

    # Strip code block markers (```json, ```JSON, ``` json, etc.)
    text = re.sub(r"^```\w*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Extract JSON between outermost brackets
    close = "}" if bracket == "{" else "]"
    start = text.find(bracket)
    end = text.rfind(close)
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def run_claude_with_retry(
    cmd: list[str],
    prompt: str,
    ctx: PipelineContext,
    timeout: int = 480,
    label: str = "Claude",
) -> str | None:
    """Run a Claude CLI command with retries and model fallback.
    Returns stdout on success, None on all models exhausted."""
    current_cmd = list(cmd)

    current_model = None
    for i, arg in enumerate(current_cmd):
        if arg == "--model" and i + 1 < len(current_cmd):
            current_model = current_cmd[i + 1]
            break

    while True:
        result = _run_claude_single(
            current_cmd, prompt, ctx, timeout, label, current_model
        )
        if result is not None:
            return result

        next_model = MODEL_FALLBACK.get(current_model)
        if next_model is None:
            ctx.log(f"    {label}: tat ca model deu that bai")
            return None

        ctx.log(f"    {label}: chuyen sang model {next_model}...")
        current_model = next_model
        for i, arg in enumerate(current_cmd):
            if arg == "--model" and i + 1 < len(current_cmd):
                current_cmd[i + 1] = next_model
                break


def _run_claude_single(
    cmd: list[str],
    prompt: str,
    ctx: PipelineContext,
    timeout: int,
    label: str,
    model_name: str | None,
) -> str | None:
    """Run claude command with up to 4 attempts. Returns stdout or None."""
    max_attempts = 4
    model_tag = f" [{model_name}]" if model_name else ""
    ctx.log(
        f"    {label}{model_tag}: bat dau (timeout={timeout}s, prompt={len(prompt):,} ky tu)"
    )

    for attempt in range(1, max_attempts + 1):
        ctx.check_cancelled()
        if attempt > 1:
            ctx.log(f"    {label}{model_tag}: retry lan {attempt - 1}/3...")

        ctx.log(
            f"    {label}{model_tag}: goi Claude lan {attempt}/{max_attempts}..."
        )
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(prompt)
            tmp.close()

            with open(tmp.name, "r", encoding="utf-8") as stdin_file:
                proc = subprocess.Popen(
                    cmd,
                    stdin=stdin_file,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                ctx.current_process = proc

                stdout_chunks = []
                stderr_chunks = []

                def _read_stderr():
                    for line in proc.stderr:
                        line_s = line.rstrip()
                        stderr_chunks.append(line)
                        if line_s:
                            ctx.log(f"    [Claude] {line_s}")

                stderr_thread = threading.Thread(
                    target=_read_stderr, daemon=True
                )
                stderr_thread.start()

                try:
                    stdout_data = proc.stdout.read()
                    stdout_chunks.append(stdout_data if stdout_data else "")
                    proc.wait(timeout=timeout)
                    stderr_thread.join(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    stderr_thread.join(timeout=2)
                    ctx.log(
                        f"    {label}{model_tag}: qua thoi gian (lan {attempt}/{max_attempts})"
                    )
                    if attempt == max_attempts:
                        return None
                    continue
                finally:
                    ctx.current_process = None

            stdout = "".join(stdout_chunks)
            stderr = "".join(stderr_chunks)

            if proc.returncode != 0:
                ctx.log(
                    f"    {label}{model_tag}: loi exit code {proc.returncode} (lan {attempt}/{max_attempts})"
                )
                if stderr:
                    ctx.log(f"    stderr: {stderr[:300]}")
                if attempt == max_attempts:
                    return None
                continue

            return stdout.strip()
        finally:
            try:
                Path(tmp.name).unlink(missing_ok=True)
            except Exception:
                pass

    return None
