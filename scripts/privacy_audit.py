"""Local publication check. Reports locations/counts, never matched private values."""
from __future__ import annotations
import argparse
import configparser
import json
import re
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing
from pathlib import Path


def private_values(archive: Path | None, cloud: Path | None) -> set[str]:
    values: set[str] = set()
    if archive:
        config = json.loads((archive / "state" / "config.json").read_text(encoding="utf-8-sig"))
        for key in ("phone", "api_hash", "chat_title", "chat_id"):
            if config.get(key): values.add(str(config[key]))
        with closing(sqlite3.connect((archive / "state" / "archive.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)) as db:
            for name, local in db.execute("select file_name, local_path from media"):
                if name: values.add(name)
                if local: values.add(Path(local).name)
    if cloud:
        credentials = cloud / "credentials"
        for path in list(credentials.glob("*.env")) + list(credentials.glob("*.txt")):
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    if any(word in key.upper() for word in ("KEY", "TOKEN", "PASSWORD", "APP_ID")):
                        values.add(value.strip().strip('"\''))
                elif any(word in line.upper() for word in ("PASSWORD", "密码", "TOKEN", "SECRET")):
                    parts = re.split(r"[:：]", line, maxsplit=1)
                    if len(parts) == 2: values.add(parts[1].strip())
        config = configparser.ConfigParser(interpolation=None)
        config.read(cloud / "config" / "rclone.conf", encoding="utf-8")
        for section in config.values():
            for key, value in section.items():
                if any(word in key.lower() for word in ("token", "password", "secret", "key")):
                    values.add(value)
    return {v for v in values if len(v) >= 8}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--cloud-dir", type=Path)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--zip", type=Path, action="append", default=[])
    parser.add_argument("--exe", type=Path, action="append", default=[])
    args = parser.parse_args()
    values = private_values(args.archive_root, args.cloud_dir)
    buckets: dict[bytes, list[bytes]] = {}
    for value in values:
        raw = value.encode("utf-8")
        buckets.setdefault(raw[:8], []).append(raw)
    prefixes = re.compile(b"|".join(re.escape(prefix) for prefix in buckets)) if buckets else None
    def is_sensitive(content: bytes) -> bool:
        if prefixes is None: return False
        return any(content.startswith(value, match.start()) for match in prefixes.finditer(content) for value in buckets[match.group()])
    # Vendor DLLs can contain their own build paths, not this user's private data.
    user_path = re.compile(rb"C:[/\\]+Users[/\\]+" + re.escape(Path.home().name.encode()) + rb"(?:[/\\]|\x00|$)", re.I)
    unsafe_name = re.compile(r"(^|/)(credentials|manifests|state|media|logs|state-backups)/|\.(session|sqlite3|env|part)([-.]|$)", re.I)
    findings = []
    checked = 0
    def scan(label: str, content: bytes) -> None:
        nonlocal checked
        checked += 1
        if is_sensitive(content) or user_path.search(content):
            findings.append({"location": label, "reason": "private value or user-specific absolute path"})
    for name in subprocess.check_output(["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"]).decode().split("\0"):
        if not name: continue
        if unsafe_name.search(name): findings.append({"location": name, "reason": "runtime data must not be tracked"})
        path = Path(name)
        if path.is_file(): scan(name, path.read_bytes())
    if args.history:
        objects = subprocess.check_output(["git", "rev-list", "--objects", args.ref]).decode().splitlines()
        process = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            for entry in objects:
                oid, _, name = entry.partition(" ")
                process.stdin.write((oid + "\n").encode()); process.stdin.flush()
                header = process.stdout.readline().split()
                data = process.stdout.read(int(header[2])); process.stdout.read(1)
                if header[1] in (b"blob", b"commit", b"tag"):
                    scan(f"history:{oid[:12]}:{name}", data)
                if name and unsafe_name.search(name):
                    findings.append({"location": f"history:{oid[:12]}", "reason": "runtime data path in history"})
        finally:
            process.stdin.close(); process.stdout.close(); process.wait()
    for path in args.zip:
        with zipfile.ZipFile(path) as package:
            if package.testzip(): raise ValueError("ZIP CRC validation failed")
            for name in package.namelist():
                if name.endswith("/"): continue
                if unsafe_name.search(name): findings.append({"location": path.name + ":" + name, "reason": "runtime data in package"})
                scan(path.name + ":" + name, package.read(name))
    for path in args.exe:
        from PyInstaller.archive.readers import CArchiveReader
        package = CArchiveReader(str(path))
        for name, entry in package.toc.items():
            if entry[-1] == "z":
                modules = package.open_embedded_archive(name)
                for module in modules.toc:
                    data = modules.extract(module, raw=True)
                    if data: scan(path.name + ":module:" + module, data)
            else:
                scan(path.name + ":entry:" + name, package.extract(name))
    print(json.dumps({"checked": checked, "private_values_loaded": len(values), "findings": findings}, ensure_ascii=True, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
