---
name: plugin-creator
description: Create, update, and validate plugins for this plugins-hub repository, following the bundled Agent Plugins 1.0.0 spec and Agent Skills spec. Use whenever the user asks to create a new plugin, add or edit a skill, command, agent, hook, or .mcp.json inside plugins/, write or fix a plugin.json manifest, register a plugin in .claude-plugin/marketplace.json, or debug a failing `claude plugin validate` — even if they just say "add a plugin for X" and never name the spec.
allowed-tools: Skill(skill-creator:skill-creator)
compatibility: "Requires Anthropic's skill-creator skill for SKILL.md authoring: Claude Code — skill-creator plugin from claude-plugins-official (skill-creator:skill-creator); Codex/OpenCode — the skill-creator skill from github.com/anthropics/skills. If unavailable, refuse skill creation/edits and give install instructions; manifest, marketplace, and validation tasks work without it."
---

# plugin-creator

The repo-root `AGENTS.md` is authoritative for hub rules (naming, secrets,
language, commit habits). This skill covers what it leaves out — the
specification details, shaped by one consumer constraint: the repo is the
marketplace, consumed by Claude Code locally and by Codex from a versioned
git-URL cache, so a content change without a version bump and push leaves
Codex users on the stale copy.

## Workflow: new plugin

1. Read repo-root `AGENTS.md`, then check `plugins/` for a plugin that
   already covers the capability — extending one usually beats creating a
   near-duplicate.
2. Pick a `kebab-case` English name; it must equal the directory name under
   `plugins/`.
3. Scaffold only the component types the capability needs — empty
   placeholder directories fail validation and confuse consumers:

   ```
   plugins/<name>/
   ├── plugin.json                    # required
   ├── skills/<skill-name>/SKILL.md   # optional
   ├── commands/  agents/  hooks/     # optional
   └── .mcp.json                      # optional
   ```

4. Write `plugin.json` (`name`, `version`, `description`); field constraints:
   `references/agent-plugins/schemas/1.0.0/plugin.schema.json`.
5. Author each `SKILL.md` by delegating to `skill-creator`, never
   hand-authoring it as a workaround. First check your available skills for
   it: missing → refuse skill creation or edits on this path and give install
   instructions (Claude Code: the `skill-creator` plugin from
   `claude-plugins-official`; Codex/OpenCode: the `skill-creator` skill from
   `github.com/anthropics/skills`); manifest, marketplace, and validation
   steps don't need it and may proceed. Present → invoke it (Skill tool; on
   Claude Code `skill-creator:skill-creator`) with the skill's requirements
   and target path; it owns the frontmatter per
   `references/skills/specification.md` (fields, naming limits). When the
   created skill is substantial or the user asks, also delegate an A/B
   evaluation pass to `skill-creator` before validation. Hub convention when
   authoring: a skill's own tooling or environment requirements go in its
   `compatibility` frontmatter field (free text, ≤ 500 chars);
   plugin→plugin dependencies go in `plugin.json` `dependencies`.
6. Register the plugin in `.claude-plugin/marketplace.json` and add a row to
   the plugin table in `README.md`.
7. `claude plugin validate .` — must pass before committing.
8. Commit. On any later content change, bump the patch `version` in
   `plugin.json` and the marketplace entry in the same commit, then push.

## Which reference to read when

Read on demand — these are full upstream specs, and loading them all up
front wastes context.

| Need | Read |
| --- | --- |
| Package model, component discovery, env var expansion, versioning | `references/agent-plugins/spec/1.0.0.md` |
| Exact manifest field constraints | `references/agent-plugins/schemas/1.0.0/plugin.schema.json` |
| `.mcp.json` inside a plugin | same spec §8 + `references/agent-plugins/schemas/1.0.0/mcp.schema.json` |
| `SKILL.md` format for a skill inside the plugin | `references/skills/specification.md` |
| Skill quality: scoping, bundled scripts, triggering, testing | `references/skills/skill-creation/` — `best-practices.md`, `using-scripts.md`, `optimizing-descriptions.md`, `evaluating-skills.md` |

## Updating the bundled specs

Files under `references/` are as-is upstream copies with a provenance header
(source, fetch date, version, license; JSON schemas carry the same in a
top-level `x-spec-provenance` key to stay valid JSON). Never edit the copied
bodies — the updater overwrites them:

```bash
python3 scripts/update-specs.py           # fetch and rewrite changed files
python3 scripts/update-specs.py --check   # report drift only, exit 1 on drift
```

The Agent Plugins spec text is CC BY 4.0; `references/agent-plugins/LICENSE.md`
and each file's header carry the attribution — keep both when regenerating
or quoting excerpts.
