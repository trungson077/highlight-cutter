#!/usr/bin/env python3
"""
Highlight Comedy Cutter v2 - Video Input Pipeline
Videos -> Whisper Transcription -> Claude Analysis -> Cut -> Concat by Topic
"""

import multiprocessing
import flet as ft
from ui.app import HighlightApp


def main(page: ft.Page):
    HighlightApp(page)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    ft.app(target=main)
