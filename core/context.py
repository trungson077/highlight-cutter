"""Pipeline context for passing callbacks to core functions."""

import subprocess


class PipelineContext:
    """Holds callbacks and shared state for pipeline operations."""

    def __init__(self, log_fn, set_step_fn, update_status_fn, update_overall_fn, check_cancelled_fn):
        self.log = log_fn
        self.set_step = set_step_fn
        self.update_status = update_status_fn
        self.update_overall = update_overall_fn
        self.check_cancelled = check_cancelled_fn
        self.current_process: subprocess.Popen | None = None
