"""Publish sanitized artifacts with existing Git credential-manager authorization."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--asset", type=Path, action="append", default=[])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    remote = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
    match = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?", remote)
    if not match: raise ValueError("Expected a GitHub HTTPS origin")
    repository = match.group(1)
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    credential = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True, env=environment, timeout=30)
    fields = dict(line.split("=", 1) for line in credential.stdout.splitlines() if "=" in line)
    token = fields.get("password")
    if credential.returncode != 0 or not token: raise RuntimeError("Existing noninteractive GitHub authorization is unavailable")
    def request(url, method="GET", payload=None, raw=None):
        data = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "TelegramMediaArchive-publisher", "X-GitHub-Api-Version": "2022-11-28"}
        if data is not None: headers["Content-Type"] = "application/octet-stream" if raw is not None else "application/json"
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=300) as response:
            return json.load(response)
    api = "https://api.github.com/repos/" + repository
    metadata = request(api)
    if args.check:
        print(json.dumps({"authorized": True, "push_permission": metadata.get("permissions", {}).get("push"), "repository": repository}))
        return 0
    try:
        release = request(api + "/releases/tags/" + urllib.parse.quote(args.tag))
    except urllib.error.HTTPError as exc:
        if exc.code != 404: raise
        release = request(api + "/releases", "POST", {"tag_name": args.tag, "target_commitish": args.commit, "name": "TelegramMediaArchive " + args.tag, "body": "Whole-project review fixes R1-R10. Windows x86_64 portable application and sanitized source. Preserves existing sessions and resumable files; no default startup entry. See docs/reviews/0.1.7-修复验收.md. Packages contain no local archive, credentials, manifests or personal media names.", "draft": False, "prerelease": False})
    assets = {item["name"]: item for item in release.get("assets", [])}
    for path in args.asset:
        data = path.read_bytes()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if path.name in assets:
            if assets[path.name].get("digest") != digest:
                raise RuntimeError("Existing release asset differs; refusing to overwrite")
            continue
        url = release["upload_url"].partition("{")[0] + "?" + urllib.parse.urlencode({"name": path.name})
        asset = request(url, "POST", raw=data)
        if asset.get("size") != len(data) or (asset.get("digest") and asset["digest"] != digest):
            raise RuntimeError("Uploaded release asset validation failed")
        print(json.dumps({"asset": path.name, "bytes": len(data), "sha256": digest, "url": asset["browser_download_url"]}))
    print(json.dumps({"release": release["html_url"]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"GitHub API failed with HTTP {exc.code}; authorization details withheld")
