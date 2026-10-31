"""Bounded process lifecycle evidence independent of GUI stdout capture."""
from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import time
import uuid

_ACTIVE = None


def process_in_job() -> bool | None:
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    inside = ctypes.c_int()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(inside)):
        return None
    return bool(inside.value)


def runtime_event(event: str, *, throttle: bool = False, **fields) -> None:
    if _ACTIVE is not None:
        _ACTIVE.record(event, throttle=throttle, **fields)


class RuntimeJournal:
    def __init__(self, directory: Path, role: str, command: str):
        self.directory, self.role, self.command = directory, role, command
        self.run_id = uuid.uuid4().hex
        self.last_checkpoint = {}
        self.handler = None
        self.logger = logging.getLogger("runtime-" + self.run_id)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    def __enter__(self):
        global _ACTIVE
        self.previous = _ACTIVE
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / ("runtime-" + self.role + ".jsonl")
        previous = None
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 65536))
                lines = stream.read().splitlines()
            previous = json.loads(lines[-1]) if lines else None
        except (OSError, ValueError):
            pass
        self.handler = RotatingFileHandler(path, maxBytes=2 * 1024**2, backupCount=3, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(self.handler)
        _ACTIVE = self
        self.record("start", pid=os.getpid(), parent_pid=os.getppid(), command=self.command,
                    in_windows_job=process_in_job(), previous_run_without_exit=bool(previous and previous.get("event") != "exit"),
                    previous_run_id=previous.get("run_id") if previous else None)
        return self

    def record(self, event, *, throttle=False, **fields):
        now = time.monotonic()
        slot = (event, fields.get("message_id"))
        if throttle and now - self.last_checkpoint.get(slot, float("-inf")) < 60:
            return
        self.last_checkpoint[slot] = now
        payload = {"time": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id,
                   "role": self.role, "event": event, **fields}
        try:
            self.logger.info(json.dumps(payload, ensure_ascii=True))
            if event in {"start", "exit"} and self.handler and self.handler.stream:
                self.handler.flush()
                os.fsync(self.handler.stream.fileno())
        except OSError:
            pass

    def __exit__(self, typ, value, traceback):
        global _ACTIVE
        self.record("exit", reason="normal" if typ is None else typ.__name__,
                    exception_code=getattr(value, "code", None) if isinstance(getattr(value, "code", None), (int, str)) else None)
        _ACTIVE = self.previous
        if self.handler:
            self.logger.removeHandler(self.handler)
            self.handler.close()
