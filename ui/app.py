"""Highlight Comedy Cutter - Main UI Application."""

import json
import subprocess
import asyncio
import platform
import time
from pathlib import Path
from datetime import datetime

import flet as ft

from config import SEGMENTATION_PROMPT, HIGHLIGHT_SELECTION_PROMPT, VIDEO_EXTENSIONS, CLAUDE_BIN
from core.context import PipelineContext
from core.batch import find_or_create_batch_dir, save_manifest
from core.transcriber import transcribe_video
from core.analyzer import segment_stories, select_highlights
from core.cutter import cut_video
from core.concat import deduplicate_clips, raw_topic_map
from core.topics import group_topics_with_claude, concat_topics


class HighlightApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "Highlight Comedy Cutter"
        self.page.window.width = 1100
        self.page.window.height = 950
        self.page.padding = 20
        self.page.theme_mode = ft.ThemeMode.DARK

        self.video_queue: list[str] = []
        self._config_path = Path.home() / ".highlight_comedy_config.json"
        self._load_config()
        self._cancelled = False
        self._batch_dir: Path | None = None
        self._ctx: PipelineContext | None = None

        self.file_picker = ft.FilePicker()
        self.page.services.append(self.file_picker)

        self._claude_ok = False
        self._build_ui()
        self._check_claude_available()

    # ── Config ──

    def _load_config(self):
        default_dir = Path.home() / "Desktop" / "highlights_output"
        self._saved_seg_prompt = ""
        self._saved_hl_prompt = ""
        try:
            if self._config_path.exists():
                cfg = json.loads(self._config_path.read_text(encoding="utf-8"))
                saved = cfg.get("output_dir", "")
                self.output_dir = Path(saved) if saved else default_dir
                self._saved_seg_prompt = cfg.get("segmentation_prompt", "")
                self._saved_hl_prompt = cfg.get("highlight_prompt", "")
            else:
                self.output_dir = default_dir
        except Exception:
            self.output_dir = default_dir

    def _save_config(self):
        try:
            cfg = {}
            if self._config_path.exists():
                cfg = json.loads(self._config_path.read_text(encoding="utf-8"))
            cfg["output_dir"] = str(self.output_dir)
            self._config_path.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _save_prompts(self):
        """Save custom prompts to config file."""
        try:
            cfg = {}
            if self._config_path.exists():
                cfg = json.loads(self._config_path.read_text(encoding="utf-8"))
            seg_val = self.seg_prompt_field.value or ""
            hl_val = self.hl_prompt_field.value or ""
            # Only save if different from default
            cfg["segmentation_prompt"] = seg_val if seg_val != SEGMENTATION_PROMPT else ""
            cfg["highlight_prompt"] = hl_val if hl_val != HIGHLIGHT_SELECTION_PROMPT else ""
            self._config_path.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    # ── UI Building ──

    def _build_ui(self):
        self.model_dropdown = ft.Dropdown(
            label="Model Claude",
            value="sonnet",
            options=[
                ft.dropdown.Option("haiku", "Haiku (nhanh, re)"),
                ft.dropdown.Option("sonnet", "Sonnet (can bang)"),
                ft.dropdown.Option("opus", "Opus (chat luong cao)"),
            ],
            width=220,
        )
        # Prompt editors — 2 tabs: Segmentation + Highlight Selection
        self._prompt_height = 250
        self.seg_prompt_field = ft.TextField(
            value=self._saved_seg_prompt or SEGMENTATION_PROMPT,
            multiline=True,
            expand=True,
            text_size=11,
            border_color=ft.Colors.OUTLINE,
        )
        self.hl_prompt_field = ft.TextField(
            value=self._saved_hl_prompt or HIGHLIGHT_SELECTION_PROMPT,
            multiline=True,
            expand=True,
            text_size=11,
            border_color=ft.Colors.OUTLINE,
        )

        self.prompt_save_btn = ft.ElevatedButton(
            "Luu prompt",
            icon=ft.Icons.SAVE,
            bgcolor=ft.Colors.BLUE_700,
            color=ft.Colors.WHITE,
            on_click=lambda _: self._save_prompts_ui(),
        )
        self.prompt_reset_btn = ft.OutlinedButton(
            "Reset ve mac dinh",
            icon=ft.Icons.RESTORE,
            on_click=lambda _: self._reset_prompt(),
        )
        self.prompt_status = ft.Text(
            "", size=11, color=ft.Colors.GREEN_400
        )

        # Manual tab switcher for prompt editor
        self._prompt_tab_idx = 0
        self.seg_tab_btn = ft.ElevatedButton(
            "Phan doan cau chuyen",
            icon=ft.Icons.SEGMENT,
            bgcolor=ft.Colors.BLUE_700,
            color=ft.Colors.WHITE,
            on_click=lambda _: self._switch_prompt_tab(0),
        )
        self.hl_tab_btn = ft.OutlinedButton(
            "Chon highlight",
            icon=ft.Icons.STAR,
            on_click=lambda _: self._switch_prompt_tab(1),
        )
        self.seg_prompt_container = ft.Container(
            content=self.seg_prompt_field, expand=True, visible=True
        )
        self.hl_prompt_container = ft.Container(
            content=self.hl_prompt_field, expand=True, visible=False
        )

        self.prompt_container = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [self.seg_tab_btn, self.hl_tab_btn],
                        spacing=4,
                    ),
                    self.seg_prompt_container,
                    self.hl_prompt_container,
                    ft.Row(
                        [
                            self.prompt_save_btn,
                            self.prompt_reset_btn,
                            self.prompt_status,
                        ],
                        spacing=8,
                    ),
                ],
                spacing=4,
            ),
            height=self._prompt_height,
            border=ft.border.all(1, ft.Colors.OUTLINE),
            border_radius=8,
            padding=8,
            visible=False,
        )
        self.prompt_toggle = ft.TextButton(
            "Sua prompt AI",
            icon=ft.Icons.EDIT_NOTE,
            on_click=self._toggle_prompt_editor,
        )

        self.video_list = ft.ListView(expand=True, spacing=4, auto_scroll=True)
        self.video_count_text = ft.Text(
            "0 video", size=12, color=ft.Colors.GREY_400
        )
        self._video_list_height = 120
        self._log_height = 200

        # Timer
        self._timer_running = False
        self._timer_start = 0.0
        self.timer_text = ft.Text(
            "", size=13, color=ft.Colors.GREY_400, visible=False
        )

        self.log_text = ft.TextField(
            multiline=True,
            read_only=True,
            expand=True,
            text_size=11,
            border_color=ft.Colors.TRANSPARENT,
        )

        # Per-step progress rows
        self.step_data = []
        step_names = [
            ("1. Phien am", ft.Icons.MIC, "Whisper tiny"),
            ("2. Phan doan + Chon", ft.Icons.PSYCHOLOGY, "Claude AI x2"),
            ("3. Cat clip", ft.Icons.CONTENT_CUT, "FFmpeg"),
            ("4. Ghep theo chu de", ft.Icons.MERGE_TYPE, "FFmpeg"),
        ]
        step_controls = []
        for name, icon, desc in step_names:
            status_icon = ft.Icon(
                ft.Icons.RADIO_BUTTON_UNCHECKED,
                size=20,
                color=ft.Colors.GREY_600,
            )
            label = ft.Text(name, size=13, color=ft.Colors.GREY_500, width=120)
            desc_text = ft.Text(
                desc, size=11, color=ft.Colors.GREY_600, width=80
            )
            prog_bar = ft.ProgressBar(
                value=0, expand=True, color=ft.Colors.BLUE_400
            )
            pct_text = ft.Text(
                "--",
                size=12,
                color=ft.Colors.GREY_500,
                width=55,
                text_align=ft.TextAlign.RIGHT,
            )
            detail_text = ft.Text(
                "", size=11, color=ft.Colors.GREY_500, expand=True
            )

            row = ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [status_icon, label, desc_text, prog_bar, pct_text],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        detail_text,
                    ],
                    spacing=2,
                ),
                padding=ft.padding.symmetric(vertical=4, horizontal=8),
                border_radius=6,
            )

            self.step_data.append(
                {
                    "icon": status_icon,
                    "label": label,
                    "desc": desc_text,
                    "bar": prog_bar,
                    "pct": pct_text,
                    "detail": detail_text,
                    "container": row,
                }
            )
            step_controls.append(row)

        # Overall progress
        self.overall_bar = ft.ProgressBar(
            visible=False, expand=True, color=ft.Colors.GREEN_400
        )
        self.overall_text = ft.Text("", size=13, weight=ft.FontWeight.BOLD)
        self.status_text = ft.Text(
            "San sang",
            size=15,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.BLUE_200,
        )

        # Merge similar topics option
        self.merge_switch = ft.Switch(label="Gop chu de tuong tu", value=True)
        self.merge_count = ft.TextField(
            label="So clip toi da moi video",
            value="5",
            width=200,
            keyboard_type=ft.KeyboardType.NUMBER,
            text_size=13,
        )
        self.merge_container = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        "Cai dat gop chu de",
                        weight=ft.FontWeight.BOLD,
                        size=13,
                    ),
                    ft.Text(
                        "Khi bat, Claude AI se phan tich va gop cac chu de tuong tu lai "
                        "(VD: 'hen ho' + 'hon nhan' -> 'tinh cam'). "
                        "Moi nhom se duoc tao tieu de, mo ta & tags cho YouTube.",
                        size=11,
                        color=ft.Colors.GREY_400,
                    ),
                    ft.Row([self.merge_switch, self.merge_count], spacing=20),
                ],
                spacing=6,
            ),
            border=ft.border.all(1, ft.Colors.OUTLINE),
            border_radius=8,
            padding=10,
            bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.YELLOW),
        )

        # Output folder picker
        self.output_dir_text = ft.Text(
            str(self.output_dir), size=12, color=ft.Colors.GREY_400, expand=True
        )
        self.output_dir_row = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.FOLDER, size=18, color=ft.Colors.AMBER_400
                    ),
                    ft.Text("Output:", size=12, weight=ft.FontWeight.BOLD),
                    self.output_dir_text,
                    ft.IconButton(
                        ft.Icons.EDIT,
                        icon_size=16,
                        tooltip="Chon thu muc output",
                        on_click=self._pick_output_folder,
                    ),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            border=ft.border.all(1, ft.Colors.OUTLINE),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=10, vertical=4),
        )

        # Claude status banner
        self.claude_banner = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.ERROR, color=ft.Colors.WHITE, size=18),
                    ft.Text(
                        "Dang kiem tra Claude Code CLI...",
                        size=12,
                        color=ft.Colors.WHITE,
                        expand=True,
                    ),
                ],
                spacing=8,
            ),
            bgcolor=ft.Colors.ORANGE_700,
            border_radius=6,
            padding=8,
            visible=True,
        )

        # Buttons
        self.add_btn = ft.ElevatedButton(
            "Them video",
            icon=ft.Icons.VIDEO_FILE,
            on_click=self._pick_videos,
            disabled=True,
        )
        clear_btn = ft.OutlinedButton(
            "Xoa tat ca", icon=ft.Icons.DELETE_SWEEP, on_click=self._clear_all
        )
        self.run_btn = ft.ElevatedButton(
            "Bat dau",
            icon=ft.Icons.PLAY_ARROW,
            bgcolor=ft.Colors.GREEN_700,
            color=ft.Colors.WHITE,
            on_click=self._run_pipeline,
            height=45,
            disabled=True,
        )
        self.stop_btn = ft.ElevatedButton(
            "Dung lai",
            icon=ft.Icons.STOP,
            bgcolor=ft.Colors.RED_700,
            color=ft.Colors.WHITE,
            on_click=self._stop_pipeline,
            height=45,
            visible=False,
        )
        self.refresh_btn = ft.OutlinedButton(
            "Lam moi",
            icon=ft.Icons.REFRESH,
            on_click=self._refresh_all,
        )
        self.open_output_btn = ft.ElevatedButton(
            "Mo thu muc ket qua",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=self._open_output,
            visible=False,
        )

        # Settings section (collapsible)
        self.settings_visible = True
        self.settings_content = ft.Container(
            content=ft.Column(
                [
                    ft.Row([self.model_dropdown], spacing=10),
                    self.merge_container,
                    self.prompt_toggle,
                    self.prompt_container,
                    self._make_drag_handle("prompt"),
                ],
                spacing=8,
            ),
            padding=ft.padding.only(left=8, right=8, bottom=8),
            visible=True,
        )
        settings_toggle = ft.TextButton(
            "Cai dat",
            icon=ft.Icons.SETTINGS,
            on_click=self._toggle_settings,
        )

        # Resizable containers
        self.video_list_container = ft.Container(
            content=self.video_list,
            height=self._video_list_height,
            border=ft.border.all(1, ft.Colors.OUTLINE),
            border_radius=8,
            padding=8,
        )
        self.log_container = ft.Container(
            content=self.log_text,
            height=self._log_height,
            border=ft.border.all(1, ft.Colors.OUTLINE),
            border_radius=8,
            padding=4,
        )

        # Layout
        self.page.add(
            ft.Column(
                [
                    ft.Text(
                        "Highlight Comedy Cutter",
                        size=24,
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.Text(
                        "Video -> Phien am -> Phan tich AI -> Cat clip -> Ghep theo chu de",
                        size=12,
                        color=ft.Colors.GREY_400,
                    ),
                    ft.Divider(height=1),
                    settings_toggle,
                    self.settings_content,
                    ft.Divider(height=1),
                    self.output_dir_row,
                    self.claude_banner,
                    ft.Row([self.add_btn, clear_btn], spacing=10),
                    ft.Row(
                        [
                            ft.Text(
                                "Danh sach video",
                                weight=ft.FontWeight.BOLD,
                                size=12,
                            ),
                            self.video_count_text,
                        ],
                        spacing=8,
                    ),
                    self.video_list_container,
                    self._make_drag_handle("video_list"),
                    ft.Row(
                        [
                            self.run_btn,
                            self.stop_btn,
                            self.refresh_btn,
                            self.open_output_btn,
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=10,
                    ),
                    ft.Divider(height=1),
                    ft.Row(
                        [
                            ft.Text(
                                "Tien trinh", weight=ft.FontWeight.BOLD, size=13
                            ),
                            self.timer_text,
                            self.overall_bar,
                            self.overall_text,
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self.status_text,
                    ft.Container(
                        content=ft.Column(step_controls, spacing=1),
                        border=ft.border.all(1, ft.Colors.OUTLINE),
                        border_radius=6,
                        padding=6,
                    ),
                    ft.Text("Nhat ky", weight=ft.FontWeight.BOLD, size=13),
                    self.log_container,
                    self._make_drag_handle("log"),
                ],
                spacing=6,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            )
        )

    # ── UI Event Handlers ──

    async def _pick_output_folder(self, _):
        result = await self.file_picker.get_directory_path(
            dialog_title="Chon thu muc output",
            initial_directory=str(self.output_dir),
        )
        if result:
            self.output_dir = Path(result)
            self.output_dir_text.value = str(self.output_dir)
            self._save_config()
            self.page.update()

    def _toggle_settings(self, _):
        self.settings_content.visible = not self.settings_content.visible
        self.page.update()

    def _toggle_prompt_editor(self, _):
        self.prompt_container.visible = not self.prompt_container.visible
        self.page.update()

    def _switch_prompt_tab(self, idx: int):
        self._prompt_tab_idx = idx
        self.seg_prompt_container.visible = idx == 0
        self.hl_prompt_container.visible = idx == 1
        if idx == 0:
            self.seg_tab_btn.bgcolor = ft.Colors.BLUE_700
            self.seg_tab_btn.color = ft.Colors.WHITE
            self.hl_tab_btn.bgcolor = None
            self.hl_tab_btn.color = None
        else:
            self.seg_tab_btn.bgcolor = None
            self.seg_tab_btn.color = None
            self.hl_tab_btn.bgcolor = ft.Colors.BLUE_700
            self.hl_tab_btn.color = ft.Colors.WHITE
        self.prompt_status.value = ""
        self.page.update()

    def _reset_prompt(self):
        if self._prompt_tab_idx == 0:
            self.seg_prompt_field.value = SEGMENTATION_PROMPT
            self.prompt_status.value = "Da reset prompt phan doan"
        else:
            self.hl_prompt_field.value = HIGHLIGHT_SELECTION_PROMPT
            self.prompt_status.value = "Da reset prompt highlight"
        self.prompt_status.color = ft.Colors.ORANGE_400
        self.page.update()

    def _save_prompts_ui(self):
        self._save_prompts()
        self.prompt_status.value = "Da luu!"
        self.prompt_status.color = ft.Colors.GREEN_400
        self.page.update()

    # ── Resizable drag handles ──

    def _make_drag_handle(self, target: str) -> ft.GestureDetector:
        handle_bar = ft.Container(
            content=ft.Icon(
                ft.Icons.DRAG_HANDLE, size=14, color=ft.Colors.GREY_500
            ),
            alignment=ft.Alignment(0, 0),
            height=16,
            border_radius=4,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
        )
        return ft.GestureDetector(
            content=handle_bar,
            on_vertical_drag_update=lambda e: self._on_resize_drag(e, target),
            mouse_cursor=ft.MouseCursor.RESIZE_ROW,
        )

    def _on_resize_drag(self, e, target: str):
        delta = e.local_delta.y
        if target == "video_list":
            self._video_list_height = max(60, self._video_list_height + delta)
            self.video_list_container.height = self._video_list_height
        elif target == "log":
            self._log_height = max(80, self._log_height + delta)
            self.log_container.height = self._log_height
        elif target == "prompt":
            self._prompt_height = max(100, self._prompt_height + delta)
            self.prompt_container.height = self._prompt_height
        self.page.update()

    # ── Timer ──

    async def _start_timer(self):
        self._timer_start = time.time()
        self._timer_running = True
        self.timer_text.visible = True
        while self._timer_running:
            elapsed = time.time() - self._timer_start
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            self.timer_text.value = f"{mins:02d}:{secs:02d}"
            self.page.update()
            await asyncio.sleep(1)

    def _stop_timer(self):
        self._timer_running = False
        elapsed = time.time() - self._timer_start
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        self.timer_text.value = f"Tong: {mins:02d}:{secs:02d}"
        self.page.update()

    # ── Claude Check ──

    def _check_claude_available(self):
        try:
            result = subprocess.run(
                [CLAUDE_BIN, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.strip().split("\n")[0]
                self._claude_ok = True
                self.claude_banner.bgcolor = ft.Colors.GREEN_700
                self.claude_banner.content.controls[0].name = (
                    ft.Icons.CHECK_CIRCLE
                )
                self.claude_banner.content.controls[1].value = (
                    f"Claude Code CLI san sang ({version})"
                )
                self.add_btn.disabled = False
                self.run_btn.disabled = False
            else:
                self._claude_ok = False
                self.claude_banner.bgcolor = ft.Colors.RED_700
                self.claude_banner.content.controls[1].value = (
                    "Claude Code CLI khong hoat dong. Hay cai dat: npm install -g @anthropic-ai/claude-code"
                )
        except FileNotFoundError:
            self._claude_ok = False
            self.claude_banner.bgcolor = ft.Colors.RED_700
            self.claude_banner.content.controls[1].value = (
                "Khong tim thay Claude Code CLI. Hay cai dat: npm install -g @anthropic-ai/claude-code"
            )
        except Exception as e:
            self._claude_ok = False
            self.claude_banner.bgcolor = ft.Colors.RED_700
            self.claude_banner.content.controls[1].value = (
                f"Loi kiem tra Claude: {e}"
            )

        self.page.update()

    # ── Step UI helpers ──

    def _set_step(
        self, idx: int, state: str, progress: float = 0, detail: str = ""
    ):
        s = self.step_data[idx]
        if state == "pending":
            s["icon"].name = ft.Icons.RADIO_BUTTON_UNCHECKED
            s["icon"].color = ft.Colors.GREY_600
            s["label"].color = ft.Colors.GREY_500
            s["bar"].value = 0
            s["bar"].color = ft.Colors.BLUE_400
            s["pct"].value = "--"
            s["pct"].color = ft.Colors.GREY_500
            s["detail"].value = ""
            s["container"].bgcolor = None
        elif state == "running":
            s["icon"].name = ft.Icons.PENDING
            s["icon"].color = ft.Colors.BLUE_400
            s["label"].color = ft.Colors.BLUE_200
            s["label"].weight = ft.FontWeight.BOLD
            s["bar"].value = progress
            s["bar"].color = ft.Colors.BLUE_400
            s["pct"].value = f"{int(progress * 100)}%"
            s["pct"].color = ft.Colors.BLUE_200
            s["detail"].value = detail
            s["detail"].color = ft.Colors.BLUE_200
            s["container"].bgcolor = ft.Colors.with_opacity(
                0.05, ft.Colors.BLUE
            )
        elif state == "done":
            s["icon"].name = ft.Icons.CHECK_CIRCLE
            s["icon"].color = ft.Colors.GREEN_400
            s["label"].color = ft.Colors.GREEN_400
            s["label"].weight = None
            s["bar"].value = 1.0
            s["bar"].color = ft.Colors.GREEN_400
            s["pct"].value = "100%"
            s["pct"].color = ft.Colors.GREEN_400
            s["detail"].value = detail or "Complete"
            s["detail"].color = ft.Colors.GREEN_400
            s["container"].bgcolor = ft.Colors.with_opacity(
                0.05, ft.Colors.GREEN
            )
        elif state == "error":
            s["icon"].name = ft.Icons.ERROR
            s["icon"].color = ft.Colors.RED_400
            s["label"].color = ft.Colors.RED_400
            s["bar"].color = ft.Colors.RED_400
            s["pct"].color = ft.Colors.RED_400
            s["detail"].value = detail
            s["detail"].color = ft.Colors.RED_400
            s["container"].bgcolor = ft.Colors.with_opacity(0.05, ft.Colors.RED)
        self.page.update()

    def _reset_steps(self):
        for i in range(4):
            self._set_step(i, "pending")

    def _update_overall(self, value: float, text: str = ""):
        self.overall_bar.value = value
        self.overall_text.value = text or f"{int(value * 100)}%"
        self.page.update()

    # ── Logging ──

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        current = self.log_text.value or ""
        self.log_text.value = current + f"[{ts}] {msg}\n"
        self.page.update()

    def _update_status(self, msg: str):
        self.status_text.value = msg
        self.page.update()

    # ── File Management ──

    async def _pick_videos(self, _):
        files = await self.file_picker.pick_files(
            allow_multiple=True,
            allowed_extensions=VIDEO_EXTENSIONS,
            dialog_title="Select video files",
        )
        if files:
            for f in files:
                if f.path not in self.video_queue:
                    self.video_queue.append(f.path)
                    self._log(f"Da them: {Path(f.path).name}")
            self._refresh_video_list()

    def _clear_all(self, _):
        self.video_queue.clear()
        self._refresh_video_list()
        self._log("Da xoa tat ca.")

    def _refresh_all(self, _):
        self._cancelled = True
        if self._ctx and self._ctx.current_process:
            try:
                self._ctx.current_process.kill()
            except Exception:
                pass

        self.video_queue.clear()
        self._refresh_video_list()
        self._reset_steps()

        self.overall_bar.value = 0
        self.overall_bar.visible = False
        self.overall_text.value = ""
        self.status_text.value = "San sang"
        self.status_text.color = ft.Colors.BLUE_200
        self.log_text.value = ""

        self._stop_timer()
        self.timer_text.visible = False
        self.timer_text.value = ""

        self.run_btn.visible = True
        self.stop_btn.visible = False
        self.open_output_btn.visible = False
        self._cancelled = False
        self.page.update()

    def _refresh_video_list(self):
        self.video_list.controls.clear()
        count = len(self.video_queue)
        self.video_count_text.value = f"{count} video"
        for i, path in enumerate(self.video_queue):
            self.video_list.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.VIDEO_FILE,
                                color=ft.Colors.PURPLE_300,
                                size=18,
                            ),
                            ft.Text(Path(path).name, size=12, expand=True),
                            ft.IconButton(
                                ft.Icons.DELETE,
                                icon_size=16,
                                on_click=lambda _, idx=i: self._remove_video(
                                    idx
                                ),
                            ),
                        ]
                    ),
                    bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.PURPLE),
                    border_radius=6,
                    padding=6,
                )
            )
        self.page.update()

    def _remove_video(self, idx: int):
        if 0 <= idx < len(self.video_queue):
            removed = self.video_queue.pop(idx)
            self._log(f"Da xoa: {Path(removed).name}")
            self._refresh_video_list()

    def _open_output(self, _):
        target = getattr(self, "_batch_dir", None) or self.output_dir
        path = str(target.resolve())
        if platform.system() == "Darwin":
            subprocess.Popen(["open", path])
        elif platform.system() == "Windows":
            subprocess.Popen(["explorer", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # ── Stop ──

    def _stop_pipeline(self, _):
        self._cancelled = True
        if self._ctx and self._ctx.current_process:
            try:
                self._ctx.current_process.kill()
            except Exception:
                pass
        self._log("DANG DUNG... se dung sau thao tac hien tai.")
        self._update_status("Dang dung...")

    def _check_cancelled(self):
        if self._cancelled:
            raise InterruptedError("Pipeline cancelled by user")

    # ── Main Pipeline ──

    async def _run_pipeline(self, _):
        if not self._claude_ok:
            self._log("LOI: Claude Code CLI chua san sang. Kiem tra cai dat.")
            return

        if not self.video_queue:
            self._log("LOI: Chua co video nao trong danh sach.")
            return

        try:
            max_clips = int(self.merge_count.value or "10")
            if max_clips < 1:
                raise ValueError
        except ValueError:
            self._log("LOI: 'So clip toi da' phai la so nguyen duong.")
            return

        self._cancelled = False
        self.run_btn.visible = False
        self.stop_btn.visible = True
        self.overall_bar.visible = True
        self.open_output_btn.visible = False
        self._reset_steps()
        self.page.update()

        timer_task = asyncio.create_task(self._start_timer())

        try:
            await self._pipeline_worker()
        except InterruptedError:
            self._log("Da dung boi nguoi dung.")
            self._update_status("Da dung")
        except Exception as e:
            self._log(f"LOI NGHIEM TRONG: {e}")
            self._update_status(f"Loi: {e}")
        finally:
            self._stop_timer()
            timer_task.cancel()
            self.run_btn.visible = True
            self.stop_btn.visible = False
            self.open_output_btn.visible = True
            self.page.update()

    async def _pipeline_worker(self):
        self.output_dir.mkdir(exist_ok=True)
        self._batch_dir = find_or_create_batch_dir(
            self.output_dir, self.video_queue
        )
        self._batch_dir.mkdir(parents=True, exist_ok=True)
        save_manifest(self._batch_dir, self.video_queue)

        self._ctx = PipelineContext(
            log_fn=self._log,
            set_step_fn=self._set_step,
            update_status_fn=self._update_status,
            update_overall_fn=self._update_overall,
            check_cancelled_fn=self._check_cancelled,
        )

        self._log(f"Thu muc batch: {self._batch_dir.name}")

        model = self.model_dropdown.value
        total_videos = len(self.video_queue)
        all_highlights = []
        start_time = time.time()

        for vid_idx, video_path in enumerate(self.video_queue):
            self._check_cancelled()
            video_name = Path(video_path).stem
            vid_label = f"[{vid_idx+1}/{total_videos}]"
            self._log(f"\n{'='*60}")
            self._log(f"{vid_label} Dang xu ly: {Path(video_path).name}")

            # ── Step 1: Transcribe ──
            self._update_overall(
                vid_idx / total_videos, f"Video {vid_idx+1}/{total_videos}"
            )

            subs_dir = self._batch_dir / "subs"
            subs_dir.mkdir(parents=True, exist_ok=True)
            srt_path = subs_dir / f"{video_name}.srt"
            if srt_path.exists() and srt_path.stat().st_size > 0:
                self._log(
                    f"  SRT da ton tai, bo qua phien am: {srt_path.name}"
                )
                srt_content = srt_path.read_text(encoding="utf-8")
                seg_count = srt_content.count("\n-->")
                self._set_step(
                    0, "done", detail=f"Da co san ({seg_count} doan)"
                )
            else:
                self._update_status(
                    f"{vid_label} Dang phien am: {video_name}"
                )
                self._set_step(
                    0, "running", 0, f"Dang tai model cho {video_name}..."
                )
                self._log("  Dang phien am voi faster-whisper tiny...")
                t0 = time.time()
                try:
                    srt_content, seg_count = await asyncio.to_thread(
                        transcribe_video,
                        video_path,
                        self._batch_dir,
                        self._ctx,
                    )
                except InterruptedError:
                    raise
                except Exception as e:
                    self._log(f"  LOI phien am: {e}")
                    self._set_step(0, "error", detail=str(e)[:80])
                    continue

                elapsed = time.time() - t0
                self._set_step(
                    0,
                    "done",
                    detail=f"{seg_count} doan, {len(srt_content):,} ky tu trong {elapsed:.0f}s",
                )
                self._log(
                    f"  Phien am xong: {seg_count} doan, {len(srt_content):,} ky tu ({elapsed:.0f}s)"
                )

            self._check_cancelled()

            # ── Step 2: Analyze (2-phase: segment stories → select highlights) ──
            highlights_dir = self._batch_dir / "highlights"
            highlights_dir.mkdir(parents=True, exist_ok=True)
            segments_path = highlights_dir / f"{video_name}_segments.json"
            json_path = highlights_dir / f"{video_name}_highlights.json"

            if json_path.exists() and json_path.stat().st_size > 10:
                self._log(
                    f"  Highlights da ton tai, bo qua phan tich: {json_path.name}"
                )
                with open(json_path, "r", encoding="utf-8") as f:
                    highlights = json.load(f)
                self._set_step(
                    1,
                    "done",
                    detail=f"Da co san ({len(highlights)} highlight)",
                )
                self._log(f"  Da tai {len(highlights)} highlight tu cache")
            else:
                self._update_status(
                    f"{vid_label} Dang phan tich: {video_name}"
                )
                self._update_overall((vid_idx + 0.15) / total_videos)
                t0 = time.time()

                # Phase 1: Segment stories
                if segments_path.exists() and segments_path.stat().st_size > 10:
                    self._log(
                        f"  Segments da ton tai, bo qua phan doan: {segments_path.name}"
                    )
                    with open(segments_path, "r", encoding="utf-8") as f:
                        segments = json.load(f)
                    self._set_step(
                        1,
                        "running",
                        0.4,
                        f"Da co {len(segments)} doan, dang chon highlight...",
                    )
                else:
                    self._log(f"  Buoc 2a: Phan doan cau chuyen voi Claude ({model})...")
                    self._set_step(
                        1, "running", 0, f"Buoc 1/2: Phan doan cau chuyen ({model})..."
                    )
                    seg_prompt = self.seg_prompt_field.value or SEGMENTATION_PROMPT
                    try:
                        segments = await asyncio.to_thread(
                            segment_stories,
                            srt_content,
                            video_name,
                            model,
                            seg_prompt,
                            self._ctx,
                        )
                    except InterruptedError:
                        raise
                    except Exception as e:
                        self._log(f"  LOI phan doan: {e}")
                        self._set_step(1, "error", detail=str(e)[:80])
                        continue

                    if not segments:
                        self._log("  Khong tim thay doan cau chuyen nao, bo qua.")
                        self._set_step(1, "error", detail="Khong co segment")
                        continue

                    with open(segments_path, "w", encoding="utf-8") as f:
                        json.dump(segments, f, ensure_ascii=False, indent=2)
                    self._log(f"  Da luu {len(segments)} doan: {segments_path.name}")

                # Phase 2: Select highlights from segments
                self._log(f"  Buoc 2b: Chon highlight tu {len(segments)} doan...")
                self._update_overall((vid_idx + 0.35) / total_videos)

                hl_prompt = self.hl_prompt_field.value or HIGHLIGHT_SELECTION_PROMPT
                try:
                    highlights = await asyncio.to_thread(
                        select_highlights,
                        segments,
                        model,
                        hl_prompt,
                        self._ctx,
                    )
                except InterruptedError:
                    raise
                except Exception as e:
                    self._log(f"  LOI chon highlight: {e}")
                    self._set_step(1, "error", detail=str(e)[:80])
                    continue

                if not highlights:
                    self._log("  Khong chon duoc highlight nao, bo qua.")
                    self._set_step(1, "error", detail="Khong co highlight")
                    continue

                elapsed = time.time() - t0
                self._set_step(
                    1,
                    "done",
                    detail=f"{len(segments)} doan -> {len(highlights)} highlight ({elapsed:.0f}s)",
                )
                self._log(
                    f"  Phan tich xong: {len(segments)} doan -> {len(highlights)} highlight ({elapsed:.0f}s)"
                )

                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(highlights, f, ensure_ascii=False, indent=2)
                self._log(f"  Da luu: {json_path.name}")

            if not highlights:
                self._log("  Khong co highlight, bo qua.")
                continue

            self._check_cancelled()

            # ── Step 3: Cut ──
            clips_dir = self._batch_dir / "clips" / video_name
            clips_dir.mkdir(parents=True, exist_ok=True)

            # Check which clips already exist
            existing_clips = 0
            clip_paths = []
            for i, h in enumerate(highlights, 1):
                safe_title = (
                    h["title"]
                    .replace(" ", "_")
                    .replace("'", "")
                    .replace('"', "")
                )
                safe_title = "".join(
                    c for c in safe_title if c.isalnum() or c in "_-"
                )
                ext = Path(video_path).suffix
                output_name = f"{i:02d}_{safe_title}{ext}"
                output_path = clips_dir / output_name
                if output_path.exists() and output_path.stat().st_size > 1000:
                    clip_paths.append(output_path)
                    existing_clips += 1
                else:
                    clip_paths.append(None)

            if existing_clips == len(highlights):
                self._log(
                    f"  Tat ca {existing_clips} clip da ton tai, bo qua cat"
                )
                self._set_step(
                    2, "done", detail=f"Da co san {existing_clips} clip"
                )
            else:
                self._update_status(f"{vid_label} Dang cat: {video_name}")
                self._set_step(2, "running", 0, f"0/{len(highlights)} clip")
                self._update_overall((vid_idx + 0.5) / total_videos)

                if existing_clips > 0:
                    self._log(
                        f"  Da co {existing_clips}/{len(highlights)} clip, cat them phan con lai..."
                    )
                else:
                    self._log(f"  Dang cat {len(highlights)} clip...")

                clip_paths = await asyncio.to_thread(
                    cut_video,
                    video_path,
                    highlights,
                    clips_dir,
                    self._ctx,
                )

                ok_count = sum(1 for p in clip_paths if p)
                self._set_step(
                    2,
                    "done",
                    detail=f"Da cat {ok_count}/{len(highlights)} clip thanh cong",
                )
                self._log(
                    f"  Cat xong: {ok_count}/{len(highlights)} clip"
                )

            # Store for concat
            for h, clip_path in zip(highlights, clip_paths):
                if clip_path:
                    h["_clip_path"] = str(clip_path)
                    h["_source_video"] = video_name
            all_highlights.append(
                {
                    "video": video_name,
                    "video_path": video_path,
                    "highlights": highlights,
                }
            )

            self._update_overall(
                (vid_idx + 1) / total_videos,
                f"Video {vid_idx+1}/{total_videos} done",
            )

        self._check_cancelled()

        # ── Step 4: Concat ──
        self._update_status("Dang chuan bi ghep...")
        self._set_step(
            3, "running", 0, "Dang phan tich clip de gop nhom..."
        )
        self._log(f"\n{'='*60}")
        self._log("Buoc 4: Chuan bi ghep video...")

        all_clips = []
        for entry in all_highlights:
            for h in entry["highlights"]:
                if "_clip_path" in h and Path(h["_clip_path"]).exists():
                    all_clips.append(h)

        if not all_clips:
            self._log("  Khong co clip nao de ghep.")
            self._set_step(3, "error", detail="Khong co clip")
            return

        before_dedup = len(all_clips)
        all_clips = deduplicate_clips(all_clips)
        if len(all_clips) < before_dedup:
            removed = before_dedup - len(all_clips)
            self._log(
                f"  Da loai {removed} clip trung lap ({before_dedup} -> {len(all_clips)})"
            )

        merge_enabled = self.merge_switch.value
        max_clips_val = int(self.merge_count.value or "10")

        if merge_enabled and len(all_clips) > 1:
            self._set_step(
                3,
                "running",
                0.05,
                "Dang nho Claude gop chu de tuong tu...",
            )
            self._log("  Dang nho Claude gop chu de tuong tu...")
            topic_map = await asyncio.to_thread(
                group_topics_with_claude, all_clips, model, self._ctx
            )
            if not topic_map:
                self._log("  Claude gop nhom that bai, dung chu de goc")
                topic_map = raw_topic_map(all_clips)
            else:
                self._log(
                    f"  Claude da gop {len(all_clips)} clip thanh {len(topic_map)} nhom"
                )

            multi_clip_groups = {
                k: v for k, v in topic_map.items() if len(v) > 1
            }
            if not multi_clip_groups:
                self._log(
                    f"  Khong co chu de trung lap ({len(topic_map)} chu de rieng biet), "
                    "tu dong gop tat ca vao mot video."
                )
                topic_map = {"best_highlights": all_clips}
        else:
            # Merge OFF: concat ALL clips into one video, sorted by humor_rating (best first)
            sorted_clips = sorted(
                all_clips,
                key=lambda c: c.get("humor_rating", 5),
                reverse=True,
            )
            self._log(
                f"  Gop nhom da tat: ghep tat ca {len(sorted_clips)} clip thanh 1 video "
                "(sap xep theo do hai huoc giam dan)"
            )
            for i, c in enumerate(sorted_clips):
                rating = c.get("humor_rating", "?")
                self._log(f"    #{i+1} [rating={rating}] {c.get('title', '?')}")
            topic_map = {"best_highlights": sorted_clips}

        self._log("  Dang ghep clip theo chu de...")
        topic_count = await asyncio.to_thread(
            concat_topics,
            topic_map,
            max_clips_val,
            model,
            self._batch_dir,
            self._ctx,
        )

        self._set_step(
            3, "done", detail=f"Da tao {topic_count} thu muc chu de"
        )

        total_elapsed = time.time() - start_time
        mins = int(total_elapsed // 60)
        secs = int(total_elapsed % 60)
        self._update_status(f"Hoan tat! ({mins}p {secs}s)")
        self._update_overall(1.0, "100% Hoan tat")
        self._log(f"\nXu ly hoan tat trong {mins}p {secs}s!")
        self._log(f"Ket qua: {self._batch_dir.resolve()}")
