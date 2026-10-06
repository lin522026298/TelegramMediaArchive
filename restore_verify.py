"""Restore into an empty directory and verify against the local manifest."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


def restore_plan(manifest: Path, remote_path: str, destination: Path) -> dict[Path, dict[str, Any]]:
    remote = PurePosixPath(remote_path)
    if remote.is_absolute() or ".." in remote.parts or "\\" in remote_path or not remote.parts or remote.parts[0] != "archive":
        raise ValueError("Select a relative path inside archive")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Recovery requires an empty destination; existing files will not be overwritten or skipped")
    requested = remote.as_posix().rstrip("/")
    records: dict[str, dict[str, Any]] = {}
    with manifest.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            path = str(record.get("remote_path", ""))
            if record.get("event") in {"remote_verified", "local_purged"} and (path == requested or path.startswith(requested + "/")):
                records[path] = record
    if not records:
        raise ValueError("No verified manifest records match the requested path")
    plan = {}
    case_names = set()
    root = destination.resolve()
    for path, record in records.items():
        relative = PurePosixPath(path).name if path == requested else path[len(requested) + 1:]
        if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts or "\\" in relative:
            raise ValueError("Unsafe manifest path")
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or str(target).casefold() in case_names:
            raise ValueError("Manifest paths escape the destination or collide")
        if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", ""))):
            raise ValueError("Manifest record has no valid SHA-256")
        if not isinstance(record.get("bytes"), int) or record["bytes"] < 0:
            raise ValueError("Manifest record has no valid size")
        case_names.add(str(target).casefold())
        plan[target] = record
    return plan


def verify_restored(plan: dict[Path, dict[str, Any]], destination: Path) -> int:
    actual = {path.resolve() for path in destination.rglob("*") if path.is_file()}
    if actual != set(plan):
        raise ValueError("Restored file set does not match the manifest")
    for path, record in plan.items():
        if path.is_symlink() or path.stat().st_size != record["bytes"]:
            raise ValueError("Restored file size/type mismatch")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024**2), b""):
                digest.update(chunk)
        if digest.hexdigest() != record["sha256"]:
            raise ValueError("Restored file SHA-256 mismatch")
    return len(plan)
