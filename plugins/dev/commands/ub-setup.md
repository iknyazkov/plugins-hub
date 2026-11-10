---
description: Set up the external analysis tools the ub skill uses (crapper, dryer, mutator)
---

Set up the three code-health tools the ub skill's Then Make it Right
sections use: crapper (CRAP scores), dryer (duplication), mutator (mutation
testing). All three are Python projects (>= 3.11) run from git clones via
self-bootstrapping launchers — no compilation step. Perform the steps in
order, stopping to report if one fails.

If $ARGUMENTS contains `update`, refresh the installed tools (pull +
re-warm) instead of only installing what is missing.

1. **Hub root.** If the current working directory contains
   `.claude-plugin/marketplace.json`, use it as the hub root; otherwise use
   `~/Documents/plugins-hub`. Report and stop if neither exists. The tools
   live at `<hub>/plugins/dev/tools/<name>` — cloned as siblings (mutator
   requires crapper checked out at `../crapper`). The directory is
   gitignored and resolved live through the plugin root, so no version bump
   or reload is needed after setup.

2. **Prerequisite: Python >= 3.11.** Check `python3 --version`. If it is
   missing or older than 3.11, run `brew install python@3.13` and re-check.
   If Homebrew itself is missing, stop and tell the user to install
   Python 3.11+.

3. **Install each tool: crapper, dryer, mutator.** A tool is installed when
   `<hub>/plugins/dev/tools/<name>` exists and contains `.venv/`.

   - Installed and no `update` argument: report `skipped`, move to the next
     tool.
   - Directory missing: `git clone https://github.com/unclebob/<name>.git`
     into the tools directory.
   - With `update`: `git -C <tool dir> pull --ff-only`.
   - Warm the venv by running the launcher once:

     ```bash
     (cd "<hub>/plugins/dev/tools/<name>" && ./<name> --help)
     ```

     On first run the launcher creates `.venv` and runs `pip install -e .`;
     exit code 0 means the tool is ready.

   - On failure: report the tool, the failed step and the exact command,
     continue with the remaining tools, and finish with a failure summary.

4. **Report.** Print a table: tool | status (installed / skipped / updated
   / failed) | path. Remind the user how the tools are invoked: the ub
   skill runs the launchers `tools/crapper/crapper`, `tools/dryer/dryer`,
   and `tools/mutator/mutator` under the plugin root; where the tools are
   unavailable, those sections degrade to manual passes.
