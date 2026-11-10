#!/usr/bin/env python3
"""Handoff storage for the handoff plugin: up to 3 handoff files per project,
under the OS temp dir.

    <tempdir>/handoffs/<project-slug>/handoff-<YYYYMMDD-HHMMSS>[-<n>].md

The project slug is the project's absolute path with separators replaced by
dashes (e.g. ``-Users-alice-Documents-plugins-hub``), so paths are
deterministic: the same project always maps to the same directory. The file
name embeds the creation time; a numeric suffix breaks same-second ties, so
the oldest handoff is always the entry that sorts last. ``save`` never
overwrites: each document becomes a new entry, and once more than ``LIMIT``
entries exist the oldest ones are deleted with a warning on stderr — no
confirmation.

Subcommands:
    save       read the document from stdin, add it as the newest entry,
               evict the oldest beyond the limit, print the stored path
    show [n]   without n, list all entries newest-first;
               with n, print the n-th newest document
    consume [n]
               print the n-th newest document (default: the newest) to stdout
               and delete that file
"""

import os
import re
import sys
import tempfile
from datetime import datetime

LIMIT = 3
NAME_RE = re.compile(r"^handoff-(\d{8}-\d{6})(?:-(\d+))?\.md$")


def fail(message, code=1):
    print(f"handoff: {message}", file=sys.stderr)
    sys.exit(code)


def project_dir():
    cwd = os.getcwd()
    slug = cwd.replace(":", "-").replace("/", "-").replace("\\", "-")
    return os.path.join(tempfile.gettempdir(), "handoffs", slug)


def entries():
    """All stored handoffs as (path, created) pairs, newest first."""
    try:
        names = os.listdir(project_dir())
    except FileNotFoundError:
        return []
    stored = sorted(
        (m.group(1), int(m.group(2) or 0), name)
        for m, name in ((NAME_RE.match(n), n) for n in os.listdir(project_dir()))
        if m
    )
    # ascending by (creation stamp, same-second suffix) == oldest first
    return [
        (os.path.join(project_dir(), name), datetime.strptime(stamp, "%Y%m%d-%H%M%S"))
        for stamp, _, name in reversed(stored)
    ]


def read_doc(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        fail(f"cannot read stored handoff {path}")


def first_line(path):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    return line.strip()
    except OSError:
        pass
    return ""


def cmd_save():
    try:
        text = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError:
        fail("stdin is not valid UTF-8")
    if not text.strip():
        fail("empty stdin: pipe the handoff document to save")
    directory = project_dir()
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for suffix in [""] + [f"-{i}" for i in range(1, 10)]:
        path = os.path.join(directory, f"handoff-{stamp}{suffix}.md")
        if not os.path.exists(path):
            break
    else:
        fail("no free file name within this second; save again")
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".handoff-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    evict_overflow()
    print(path)


def evict_overflow():
    stored = entries()
    for path, created in stored[LIMIT:]:
        line = first_line(path)[:60]
        os.remove(path)
        print(f"evicted oldest handoff (created {created:%Y-%m-%d %H:%M}): {line}",
              file=sys.stderr)


def parse_index(arg, count):
    try:
        index = int(arg)
    except ValueError:
        fail(f"not a handoff number: {arg}", code=2)
    if not 1 <= index <= count:
        fail(f"no handoff #{arg} for this project ({count} stored; run show to list them)")
    return index - 1


def cmd_show(arg):
    stored = entries()
    if not stored:
        fail(f"no handoff stored for this project (expected {project_dir()}/)")
    if arg is None:
        for i, (path, created) in enumerate(stored, start=1):
            print(f"{i}. {created:%Y-%m-%d %H:%M}  {first_line(path)}")
    else:
        sys.stdout.write(read_doc(stored[parse_index(arg, len(stored))][0]))


def cmd_consume(arg):
    stored = entries()
    if not stored:
        fail(f"no handoff stored for this project (expected {project_dir()}/)")
    path, _ = stored[parse_index("1" if arg is None else arg, len(stored))]
    sys.stdout.write(read_doc(path))
    os.remove(path)
    print(f"consumed: {path}", file=sys.stderr)
    print(f"{len(stored) - 1} handoff(s) remain for this project", file=sys.stderr)


def main():
    usage = "usage: handoff.py save | show [n] | consume [n]"
    args = sys.argv[1:]
    if (not args or args[0] not in ("save", "show", "consume")
            or len(args) > 2 or (args[0] == "save" and len(args) > 1)):
        fail(usage, code=2)
    if args[0] == "save":
        cmd_save()
    elif args[0] == "show":
        cmd_show(args[1] if len(args) > 1 else None)
    else:
        cmd_consume(args[1] if len(args) > 1 else None)


if __name__ == "__main__":
    main()
