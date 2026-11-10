#!/usr/bin/env python3
"""Refresh the bundled spec copies under references/ from their upstream sources.

Every file in references/ is an as-is copy of an upstream spec document with a
small provenance header (source URL, fetch date, version, license) prepended.
Markdown/text files get an HTML comment; JSON schemas carry the same
provenance in a top-level "x-spec-provenance" key so the file stays valid JSON.

This is a manual, human-run tool — nothing here is invoked automatically:

    python3 scripts/update-specs.py           # fetch and rewrite changed files
    python3 scripts/update-specs.py --check   # report drift only, write nothing

Re-running when nothing changed upstream rewrites nothing (fetch dates are
kept stable until the body actually changes). Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
REFERENCES = SKILL_ROOT / "references"

SKILLS_BASE = "https://agentskills.io"
PLUGINS_RAW = "https://raw.githubusercontent.com/agentplugins/agent-plugins-spec/main"
PLUGINS_REPO = "https://github.com/agentplugins/agent-plugins-spec"

ATTRIBUTION = f"see {REFERENCES.name}/agent-plugins/LICENSE.md"


@dataclass(frozen=True)
class Source:
    url: str
    path: str  # relative to the skill root
    version: str
    license: str = ""  # empty → License line omitted (upstream declares none)

    @property
    def is_json(self) -> bool:
        return self.path.endswith(".json")


SOURCES = [
    # Agent Skills (agentskills.io) — living web docs, no version declared.
    Source(f"{SKILLS_BASE}/specification.md", "references/skills/specification.md",
           "unversioned web document (Agent Skills format spec)"),
    Source(f"{SKILLS_BASE}/skill-creation/best-practices.md",
           "references/skills/skill-creation/best-practices.md",
           "unversioned web document (skill-creation guide)"),
    Source(f"{SKILLS_BASE}/skill-creation/using-scripts.md",
           "references/skills/skill-creation/using-scripts.md",
           "unversioned web document (skill-creation guide)"),
    Source(f"{SKILLS_BASE}/skill-creation/optimizing-descriptions.md",
           "references/skills/skill-creation/optimizing-descriptions.md",
           "unversioned web document (skill-creation guide)"),
    Source(f"{SKILLS_BASE}/skill-creation/evaluating-skills.md",
           "references/skills/skill-creation/evaluating-skills.md",
           "unversioned web document (skill-creation guide)"),
    # Agent Plugins — pinned to published spec 1.0.0 (CC BY 4.0).
    Source(f"{PLUGINS_RAW}/spec/1.0.0.md", "references/agent-plugins/spec/1.0.0.md",
           "Agent Plugins Specification 1.0.0 (Published)",
           f"CC BY 4.0, (c) Agent Plugins contributors, attribution preserved; {ATTRIBUTION}"),
    Source(f"{PLUGINS_RAW}/schemas/1.0.0/plugin.schema.json",
           "references/agent-plugins/schemas/1.0.0/plugin.schema.json",
           "Agent Plugins schemas 1.0.0", f"Apache-2.0; {ATTRIBUTION}"),
    Source(f"{PLUGINS_RAW}/schemas/1.0.0/mcp.schema.json",
           "references/agent-plugins/schemas/1.0.0/mcp.schema.json",
           "Agent Plugins schemas 1.0.0", f"Apache-2.0; {ATTRIBUTION}"),
    Source(f"{PLUGINS_RAW}/LICENSE.md", "references/agent-plugins/LICENSE.md",
           "licensing document of the agent-plugins-spec repository",
           "CC BY 4.0 (docs) / Apache-2.0 (code), as stated within"),
]

TEXT_HEADER_RE = re.compile(r"\A<!--\nSource:  .*?-->\n\n", re.DOTALL)
JSON_PROVENANCE_KEY = "x-spec-provenance"
JSON_PROVENANCE_RE = re.compile(
    rf'^  "{JSON_PROVENANCE_KEY}": ".*",\n', re.MULTILINE
)


def fetch(url: str) -> str:
    """Fetch a URL as text, retrying: agentskills.io intermittently stalls."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "plugins-hub plugin-creator spec updater"}
    )
    last_error: Exception = RuntimeError("unreachable")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except Exception as error:  # noqa: BLE001 - retried below
            last_error = error
            time.sleep(2 * (attempt + 1))
    raise last_error


def provenance_line(source: Source, date: str) -> str:
    parts = [f"as-is copy of {source.url}", f"fetched {date}", source.version]
    if source.license:
        parts.append(source.license)
    return " | ".join(parts)


def apply_header(source: Source, body: str, date: str) -> str:
    """Prepend provenance to an upstream body without altering the body."""
    if source.is_json:
        body = body.lstrip()
        if not body.startswith("{"):
            raise ValueError(f"unexpected JSON shape at {source.url}")
        entry = "  " + json.dumps(JSON_PROVENANCE_KEY) + ": " \
            + json.dumps(provenance_line(source, date)) + ",\n"
        decorated = "{\n" + entry + body[1:].lstrip("\n")
        json.loads(decorated)  # fail loudly rather than commit invalid JSON
        return decorated
    lines = [
        "<!--",
        f"Source:  {source.url}",
        f"Fetched: {date}",
        f"Version: {source.version}",
    ]
    if source.license:
        lines.append(f"License: {source.license}")
    lines += ["", "-->", "", ""]
    return "\n".join(lines) + body


def strip_header(source: Source, text: str) -> str:
    """Remove our provenance, returning the upstream body for comparison."""
    if source.is_json:
        return JSON_PROVENANCE_RE.sub("", text, count=1)
    return TEXT_HEADER_RE.sub("", text, count=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="report upstream drift without writing")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date().isoformat()
    had_error = False
    has_drift = False

    for source in SOURCES:
        label = source.path
        try:
            remote = fetch(source.url)
        except Exception as error:  # noqa: BLE001 - report and continue
            print(f"ERROR    {label}: {error}")
            had_error = True
            continue

        try:
            remote_body = strip_header(source, remote)
        except ValueError as error:
            print(f"ERROR    {label}: {error}")
            had_error = True
            continue

        local_path = SKILL_ROOT / source.path
        local = local_path.read_text(encoding="utf-8") if local_path.exists() else None

        if args.check:
            if local is None:
                print(f"MISSING  {label}")
                has_drift = True
            elif strip_header(source, local) != remote_body:
                print(f"DRIFT    {label}")
                has_drift = True
            else:
                print(f"current  {label}")
            continue

        # Keep the existing fetch date when the upstream body is unchanged,
        # so casual re-runs do not churn the files.
        if local is not None and strip_header(source, local) == remote_body:
            print(f"current  {label}")
            continue

        local_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = local_path.with_suffix(local_path.suffix + ".tmp")
        tmp_path.write_text(apply_header(source, remote_body, today), encoding="utf-8")
        tmp_path.replace(local_path)
        print(f"updated  {label}")

    if had_error:
        return 2
    return 1 if args.check and has_drift else 0


if __name__ == "__main__":
    sys.exit(main())
