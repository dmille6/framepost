#!/usr/bin/env python3
"""Ask an OpenAI model a question, optionally with source files attached.

Why this exists: running one model several times catches brief-specific blind spots
but not model-specific ones. If Claude is systematically wrong about something — a
framework convention, what good scheduling UX looks like — every Claude run inherits
it, and their agreement reads as corroboration when it is only correlation. A second
model from a different lineage is the cheapest correction available.

Runs inside the backend container, which already carries the openai SDK and the key
from .env. Nothing new is installed on the host.

    askgpt "why would Meta refuse to fetch this URL?"
    askgpt -f backend/services/scheduler.py "what is wrong with _record_failure?"
    cat notes.md | askgpt -   # read the prompt from stdin
    askgpt -m gpt-5-pro "..." # slower, dearer, better at hard reasoning

Paths passed to -f are relative to the repo root, not the container.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

MODEL_DEFAULT = "gpt-5"
REPO = pathlib.Path("/app")          # backend/ is mounted here
MAX_FILE_BYTES = 400_000


def _read(rel: str) -> tuple[str, str]:
    """Resolve a repo-relative path inside the container and return (label, text)."""
    p = pathlib.Path(rel)
    for cand in (REPO / p, REPO / p.name, p):
        if cand.is_file():
            data = cand.read_bytes()[:MAX_FILE_BYTES]
            return rel, data.decode("utf-8", errors="replace")
    # backend/x.py on the host is /app/x.py in here — try that shape too
    if rel.startswith("backend/"):
        cand = REPO / rel[len("backend/"):]
        if cand.is_file():
            return rel, cand.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    raise SystemExit(f"askgpt: no such file: {rel}")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="askgpt", description="Ask an OpenAI model, with optional file context.")
    ap.add_argument("prompt", nargs="?", default="-",
                    help="the question; '-' or omitted reads stdin")
    ap.add_argument("-f", "--file", action="append", default=[], metavar="PATH",
                    help="attach a file's contents (repeatable)")
    ap.add_argument("-m", "--model", default=MODEL_DEFAULT)
    ap.add_argument("-s", "--system", default=None, help="optional system instruction")
    ap.add_argument("--raw", action="store_true", help="answer only, no header")
    args = ap.parse_args()

    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
    if not prompt.strip():
        raise SystemExit("askgpt: empty prompt")

    parts: list[str] = []
    for rel in args.file:
        label, text = _read(rel)
        parts.append(f"===== FILE: {label} =====\n{text}\n===== END {label} =====")
    parts.append(prompt.strip())
    content = "\n\n".join(parts)

    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit("askgpt: the openai package is not installed in this container")

    client = OpenAI()
    kwargs: dict = {"model": args.model}

    # Newer SDKs expose responses; fall back to chat.completions on older ones.
    try:
        payload = ([{"role": "system", "content": args.system}] if args.system else [])
        payload.append({"role": "user", "content": content})
        resp = client.responses.create(**kwargs, input=payload)
        answer = resp.output_text
        usage = getattr(resp, "usage", None)
        used = (f"{usage.input_tokens} in / {usage.output_tokens} out"
                if usage else "usage unavailable")
    except (AttributeError, TypeError):
        msgs = ([{"role": "system", "content": args.system}] if args.system else [])
        msgs.append({"role": "user", "content": content})
        resp = client.chat.completions.create(**kwargs, messages=msgs)
        answer = resp.choices[0].message.content or ""
        u = getattr(resp, "usage", None)
        used = (f"{u.prompt_tokens} in / {u.completion_tokens} out"
                if u else "usage unavailable")

    if not args.raw:
        files = f", {len(args.file)} file(s)" if args.file else ""
        print(f"── {args.model}{files} · {used} ──\n", file=sys.stderr)
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
