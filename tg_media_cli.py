#!/usr/bin/env python3
from __future__ import annotations

import sys

from tg_media_archive import main


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    raise SystemExit(main())
