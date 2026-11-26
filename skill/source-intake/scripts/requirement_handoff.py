#!/usr/bin/env python3
"""Read or append work handoffs without rewriting requirement materials."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from handoff_store import append_handoff, read_handoffs
from requirement_context import read_context


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        parser = JsonParser()
        commands = parser.add_subparsers(dest="command", required=True)
        reader = commands.add_parser("read")
        reader.add_argument("--directory", required=True)
        reader.add_argument("--limit", type=int, default=20)
        writer = commands.add_parser("append")
        writer.add_argument("--directory", required=True)
        writer.add_argument("--input", required=True)
        args = parser.parse_args(argv)
        context = read_context(args.directory, include_handoffs=False)
        if context.get("ok") is False:
            raise ValueError(context["error"]["message"])
        if args.command == "read":
            result = {"ok": True, **read_handoffs(args.directory, context=context, limit=args.limit)}
        else:
            with Path(args.input).open("rb") as handle:
                data = handle.read(128 * 1024 + 1)
            if len(data) > 128 * 1024:
                raise ValueError("Handoff input exceeds 128 KiB")
            result = append_handoff(args.directory, json.loads(data.decode("utf-8-sig")), context=context)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
