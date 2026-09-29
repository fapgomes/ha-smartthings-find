#!/usr/bin/env python3
"""Download the public SmartThings Find web bundle and list API surface.

No session or cookie is used: the Next.js chunks are public.

Usage:
    extract_site_js.py [OUT_DIR] [PATTERN ...]

Without patterns it prints the endpoints (``*.do``) and operation names
found in the bundle. With patterns it prints the code around each match.
"""

import re
import sys
import urllib.request
from pathlib import Path

BASE = "https://smartthingsfind.samsung.com"
UA = {"User-Agent": "Mozilla/5.0"}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf8", "ignore")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "stf_js")
    patterns = sys.argv[2:]
    out.mkdir(parents=True, exist_ok=True)

    index = fetch(BASE + "/")
    chunks = sorted(set(re.findall(r'/_next/static/chunks/[^"]+\.js', index)))
    bundle = []
    for path in chunks:
        text = fetch(BASE + path)
        (out / Path(path).name).write_text(text)
        bundle.append(text)
    js = "\n".join(bundle)
    (out / "all.js").write_text(js)
    print(f"{len(chunks)} chunks, {len(js)} bytes -> {out}")

    if not patterns:
        print("endpoints:", sorted(set(re.findall(r'["\'`](/?[\w/]*\.do)', js))))
        print("operations:", sorted(set(re.findall(r'operation:"([A-Z_]+)"', js))))
        print("cases:", sorted(set(re.findall(r'case"([A-Z_]{4,})"', js))))
        return

    for pat in patterns:
        for m in list(re.finditer(pat, js))[:3]:
            print(f"## {pat} ::", js[max(0, m.start() - 400):m.end() + 400], "\n")


if __name__ == "__main__":
    main()
