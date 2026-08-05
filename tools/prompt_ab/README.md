# prompt_ab — quantitative A/B regression harness for agent prompts

Treats an agent's prompt (`system.md`) as a **versioned artifact under quantitative
test**. A candidate "improvement" is run head-to-head against the current baseline
across a fixture-based task suite, every run is graded on the **triad**
(speed / cost / quality), and the result is recorded into an append-only ledger
keyed by the prompt's sha256. Over time the ledger is the provenance of every
prompt change — you can no longer ship a prompt edit on vibes; you ship it
because the triad moved.

## The triad (per run)
| axis | measure | source |
|---|---|---|
| **speed** | wall-clock of the agent call | `✓ <phase> Xs` line from the trace |
| **cost** | tokens consumed by the agent | `used N tokens` line from the trace |
| **quality** | behavioral test pass + static rubric | the task's `grade.py` |

## Commands
```bash
# Run a candidate prompt against the baseline across a task suite (n reps, interleaved).
uv run tools/prompt_ab/prompt_ab.py run \
  --agent builder \
  --prompt-path adws/adw_data/prompt_engineering/builder/system.md \
  --candidate /path/to/system_axiom.md \
  --tasks normalize_csv \
  --n 12 \
  --label "axiom: only-trust-what-can-fail"

# See the A/B verdict for an experiment (or all experiments for an agent).
uv run tools/prompt_ab/prompt_ab.py report --exp <exp_id>
uv run tools/prompt_ab/prompt_ab.py report --agent builder

# Trajectory of every prompt version ever tested, ranked by quality.
uv run tools/prompt_ab/prompt_ab.py history --agent builder
```

## Decision gate (validated improvement)
A candidate is a **VALIDATED IMPROVEMENT** only if, across the task suite:
- quality: mean(B) > mean(A) **and** floor(B) ≥ floor(A)  — better and never worse at the bottom
- cost: mean tokens(B) ≤ mean tokens(A) × 1.15            — no >15% cost regression
- speed: mean time(B) ≤ mean time(A) × 1.20               — no >20% speed regression

Quality regression → `REGRESSION`. Anything else → `INCONCLUSIVE`.
A bootstrap 95% CI on the quality delta is printed so you can see whether the
difference is real or noise. Thresholds live in `verdict()` and are tunable.

## Over-time workflow (the point of the tool)
1. Snapshot the current prompt as the baseline (the tool reads `--prompt-path`).
2. Run a candidate. If the verdict is VALIDATED, **adopt** the candidate: copy it
   over `--prompt-path`, commit it. The candidate's sha becomes the new baseline.
3. The next candidate is run against the new baseline. The ledger accumulates
   every version, so `history` shows the quality trajectory of the agent's prompt
   over months — the regression test suite for prompts.

## Adding a task
A task is a directory under `tasks/<name>/`:
- `task.json` — `{ "artifact": "scripts/foo.py", "spec": "spec.md", "grader": "grade.py", "behav_max": 12, "rubric_max": 8 }`
- `spec.md` — the task prompt fed to the agent (must be falsifiable: explicit behaviors + exit codes)
- `grade.py` — `uv run python3 grade.py <artifact> <task_dir>` → prints `behav=X/Y rubric=Z/W [details]`
- any fixture files the grader needs

Rules for a good task: **falsifiable spec** (behaviors a test can disprove), **fixture-based**
(no dependency on live/mutable state, so re-runs are deterministic), **enough behavioral
surface that both a weak and strong prompt usually don't both max it** (a task everyone
aces is uninformative — raise difficulty until the baseline slips sometimes).

## What it measures (and doesn't)
- Measures: one agent role, one prompt file, isolated (no planner/reviewer variance).
- Doesn't measure: end-to-end factory outcomes, prompt interactions across roles,
  or anything the task suite doesn't exercise. A task suite is only as good as the
  behaviors it falsifies. Grow the suite to cover the behaviors you care about.