"""Configuration constants, prompts, and path setup."""

import os
import shutil
from pathlib import Path

VIDEO_EXTENSIONS = ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v"]

MODEL_FALLBACK = {"opus": "sonnet", "sonnet": "haiku", "haiku": None}

SEGMENTATION_PROMPT = """You are an expert stand-up comedy analyst specializing in identifying complete story/bit boundaries.

Your task: Read the ENTIRE SRT transcript and divide it into COMPLETE, SELF-CONTAINED stories/bits.

=== WHAT MAKES A COMPLETE BIT ===

OPENING — The comedian clearly starts a NEW topic:
  - New premise or setup introduction
  - Phrases like: "So...", "You know what...", "Let me tell you...", "The other day...", "I was thinking about..."
  - A visible energy reset after the previous bit's laughter died down
  - A completely new subject with no connection to what came before

BODY — The bit develops:
  - Setups build on each other
  - Act-outs, callbacks, escalations
  - The audience is being led through a narrative arc

CLOSING — The bit reaches its natural end:
  - The FINAL punchline, tag, or callback that wraps up this specific story
  - A big audience laugh/applause moment
  - The comedian pauses, takes a breath, drinks water, or physically resets
  - The energy noticeably shifts before the next topic begins

=== HOW TO IDENTIFY TRANSITIONS BETWEEN BITS ===

You MUST find clear evidence of at least ONE of these transition signals:
  1. PAUSE/RESET: Deliberate silence, energy drop, physical movement after big laugh
  2. VERBAL TRANSITION: "So anyway...", "But speaking of...", "Now let me talk about...", "OK so...", "Moving on..."
  3. COMPLETE TOPIC SHIFT: The subject changes entirely (e.g., "dating" -> "airport security")
  4. STRUCTURAL RESET: New setup/premise begins from scratch — NOT a continuation, callback, or extension
  5. AUDIENCE BREAK: Applause break or extended laughter that creates a natural divider

=== CRITICAL: DO NOT CUT MID-STORY ===

If the comedian is still:
  - Building on the same premise or scenario
  - Doing callbacks to an earlier punchline in the same bit
  - Saying "and then...", "but wait...", "the best part is..."
  - Extending the same narrative (even with mini-punchlines along the way)
...then it is STILL THE SAME BIT. Do NOT split it.

Return a JSON array of ALL story segments:
[
  {
    "segment_number": 1,
    "start_time": "00:01:30",
    "end_time": "00:04:15",
    "duration_seconds": 165,
    "topic": "Short topic (1-4 words)",
    "opening_signal": "What indicates a new bit starts here (quote the transition or describe the signal)",
    "closing_signal": "What indicates this bit ends here (quote the final punchline or describe the break)",
    "summary": "Brief description of the complete story/bit"
  }
]

RULES:
1. EVERY segment MUST be a COMPLETE story — NEVER cut mid-setup, mid-story, or mid-joke.
2. start_time MUST land where the comedian CLEARLY begins a new topic — NOT during leftover laughter or transitions.
3. end_time MUST land AFTER the final punchline/tag — include the audience reaction before the next topic.
4. Between consecutive segments there MUST be a clear, identifiable topic shift.
5. Segments are typically 1-8 minutes (the natural length of a complete bit). Short bits (<1min) are OK if they are clearly self-contained.
6. Include ALL identifiable complete bits — funny or not. We will select the best ones in the next step.
7. Timestamps MUST come from actual SRT timestamps.
8. NO overlapping timestamps.
9. Small filler (crowd work, drinking water, brief transitions) is NOT a segment — absorb it into adjacent segments or skip it.
10. Calculate duration_seconds accurately from start_time and end_time.

RETURN ONLY THE JSON ARRAY. NO OTHER TEXT."""

# Backward compat — UI prompt editor defaults to SEGMENTATION_PROMPT
SYSTEM_PROMPT = SEGMENTATION_PROMPT

HIGHLIGHT_SELECTION_PROMPT = """You are given a list of complete comedy story segments from a stand-up special.
Each segment has been verified to have a clear opening, body, and closing — they are complete bits.

Your task: Review each segment and decide which ones deserve to be in a highlight compilation video.

IMPORTANT: Be GENEROUS with your selection. If a segment is entertaining, funny, or has a good story — INCLUDE IT.
Only exclude segments that are clearly weak: filler, dead crowd work, or material that simply does not land.
If ALL segments are good, select ALL of them. Do NOT artificially limit the number of highlights.

Story segments identified:
{segments_json}

For each selected highlight, return this EXACT format:
[
  {{
    "highlight_number": 1,
    "segment_number": <from the segment list>,
    "title": "Short catchy title for this bit",
    "start_time": "<EXACT start_time from segment — DO NOT MODIFY>",
    "end_time": "<EXACT end_time from segment — DO NOT MODIFY>",
    "duration_seconds": <from segment>,
    "topic": "<from segment topic>",
    "description": "Why this bit is funny — describe the setup, the punchline, what makes it memorable",
    "context": "The comedian's angle, perspective, or performance style in this bit",
    "humor_rating": <integer 1-10, how funny/entertaining this bit is — 10 = absolute killer, crowd roaring; 7-9 = very funny; 4-6 = decent; 1-3 = weak>
  }}
]

RULES:
1. Be GENEROUS — include every segment that has genuine comedic value. When in doubt, INCLUDE it.
2. Use the EXACT start_time and end_time from the segments — do NOT change them. These boundaries are verified complete stories.
3. Order highlights chronologically by start_time.
4. The number of highlights is FLEXIBLE — could be nearly all segments if the special is strong. A great special might have 10-15+ highlights.
5. Only EXCLUDE segments that are clearly filler, crowd work with no payoff, or material that genuinely does not work.
6. Each highlight is guaranteed to be a complete story with clean opening and closing.
7. humor_rating MUST be an honest assessment. Do NOT give everything a 10. Differentiate between killer bits and merely good ones.

RETURN ONLY THE JSON ARRAY. NO OTHER TEXT."""

TOPIC_GROUP_PROMPT = """You are given a list of comedy highlight clips with their topics and titles from multiple videos.

Your job:
1. Group clips with SIMILAR or RELATED topics together (e.g. "dating", "marriage", "relationships" should be one group; "airport", "flying", "travel" should be one group)
2. Give each group a short, clean group name (1-3 words, lowercase)

Input (JSON list of clips with their index, topic, and title):
{clips_json}

Return a JSON object mapping group_name to list of clip indices:
{{
  "relationships": [0, 3, 7],
  "travel": [1, 5],
  "politics": [2, 4, 6]
}}

RULES:
- Be aggressive about merging related topics. If two topics are even loosely related, merge them.
- Group name should be the most general/common term for the group.
- Every clip index must appear in exactly one group.
- RETURN ONLY THE JSON OBJECT. NO OTHER TEXT."""

YOUTUBE_SEO_PROMPT = """You are a professional YouTube content creator and SEO specialist for comedy compilation channels.

Given the following comedy highlight clips that will be concatenated into ONE compilation video, generate:

1. **YouTube Title** - catchy, SEO-optimized, under 100 chars. Use power words, include the topic. Must make people want to click.
2. **YouTube Description** - professional, SEO-rich description including:
   - Engaging opening paragraph (2-3 sentences)
   - Timestamps for each clip in the compilation (use the format 0:00 - Clip Title)
   - List of comedians/shows featured
   - Relevant hashtags (5-10)
   - Call to action (subscribe, like, comment)
3. **Tags** - comma-separated list of 15-20 YouTube tags for SEO

Topic/Group: {topic}
Total duration: {total_duration}

Clips info:
{clips_info}

Return as JSON:
{{
  "youtube_title": "...",
  "youtube_description": "...",
  "youtube_tags": "tag1, tag2, tag3, ..."
}}

RETURN ONLY THE JSON OBJECT. NO OTHER TEXT."""


def fix_path():
    """Add common macOS bin paths so .app bundles can find claude/ffmpeg."""
    extra_paths = [
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
        "/usr/local/bin",
        "/opt/homebrew/sbin",
        "/opt/homebrew/bin",
    ]
    home = Path.home()
    for d in (home / ".nvm/versions/node").glob("*/bin"):
        extra_paths.append(str(d))
    npm_prefix = home / ".npm-global/bin"
    if npm_prefix.exists():
        extra_paths.append(str(npm_prefix))
    n_prefix = Path("/usr/local/n/versions/node")
    if n_prefix.exists():
        for d in n_prefix.glob("*/bin"):
            extra_paths.append(str(d))

    current = os.environ.get("PATH", "")
    for p in extra_paths:
        if p not in current:
            current = p + ":" + current
    os.environ["PATH"] = current

    lib_paths = [
        "/opt/homebrew/lib",
        "/usr/local/lib",
        "/opt/homebrew/opt/openssl@3/lib",
        "/usr/local/opt/openssl@3/lib",
    ]
    current_lib = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    for p in lib_paths:
        if p not in current_lib:
            current_lib = p + ":" + current_lib if current_lib else p
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = current_lib


def find_bin(name: str) -> str:
    """Find full path of a binary, searching common macOS locations."""
    found = shutil.which(name)
    if found:
        return found
    for d in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]:
        p = f"{d}/{name}"
        if Path(p).exists():
            return p
    return name


fix_path()

FFMPEG_BIN = find_bin("ffmpeg")
CLAUDE_BIN = find_bin("claude")
