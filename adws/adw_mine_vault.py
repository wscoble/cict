#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "psycopg[binary]"]
# ///
"""ADW Mine Vault — harvest the Obsidian vault into the experiment backlog.

The seed of the nonstop engine. Walks the vault for testable prompt-engineering
principles, operationalizes each into a runnable experiment thought, and appends
them to the shared `experiment_backlog` table — the queue the operator drains.

    request -> harvest(miner, read-only walk of the vault)
            -> operationalize(scientist, candidates -> ExperimentThoughts)
            -> emit(code, insert into experiment_backlog, dedup by id)

The vault is read-only to the miner (writes=[]). Only the *assertions* leave
the vault — the raw personal notes stay in the vault; the backlog carries the
testable principle, its source path, and the prediction, not the note content.

Usage:
    uv run adws/adw_mine_vault.py "[focus or vault path]" [--vault ~/Projects/vault]

If no prompt is given, defaults to mining ~/Projects/vault for principles
applicable to the factory's build agents.
"""

import argparse
import sys

from adw_modules import agents, gates, session, utils
from adw_modules.data_types import (AgentCall, BacklogEmitOutput, BacklogPlanOutput,
                                    PhaseParams, VaultHarvestOutput)

REQUIRED_AGENTS = ["miner", "scientist"]
DEFAULT_VAULT = "~/Projects/vault"
DEFAULT_FOCUS = (f"Mine the Obsidian vault at {DEFAULT_VAULT} for testable "
                 "prompt-engineering principles applicable to the SSSF factory's "
                 "build agents (planner/builder/reviewer/scout/documenter). "
                 "Prefer principles that could be added as a one-paragraph "
                 "directive to an agent's system prompt and measured by the "
                 "prompt_ab harness across the session_summary and normalize_csv "
                 "fixture tasks.")


# ── phase directives ──────────────────────────────────────────────────────────

HARVEST_DIRECTIVE = f"""

--- PHASE: HARVEST ---
Walk the Obsidian vault named above. Harvest 3-8 testable prompt-engineering
principles — assertions that could be added to an agent's system prompt and
measured. Each candidate MUST cite the vault note it came from (source_path) and
include a verbatim source_quote. No source, no candidate. Prefer principles
with a clear predicted triad effect (which leg of quality-floor/mean, rubric,
speed, cost it moves).

Emit ONLY valid JSON matching `VaultHarvestOutput` (a list of CandidatePrinciple).
The user.md template shows the exact shape.
"""

OPERATIONALIZE_DIRECTIVE = """

--- PHASE: OPERATIONALIZE ---
Your `previous_envelope` is a VaultHarvestOutput — candidate principles mined
from the vault, each with provenance (source_path + source_quote). Turn EACH
candidate into a runnable experiment thought (ExperimentThought):

- id: a unique, stable kebab-case slug (derived from the candidate's id, maybe
  suffixed if it would collide).
- thought: the FULL text that an adw_experiment scientist can design from. It
  must contain: (1) the observation / structural fact, (2) the open triad-level
  question, (3) the candidate-prompt text VERBATIM — the one-paragraph directive
  to be appended to the agent's system prompt, written out in full so the next
  scientist can copy it into the candidate file. Without the candidate text the
  thought is not runnable.
- source: the vault note path (from the candidate's source_path).
- prediction: one falsifiable sentence — which axis, which direction.
- agent_under_test: which agent role's prompt changes (e.g. "builder").
- tasks: list of task names under tools/prompt_ab/tasks/ (use >=2 of different
  difficulty if you can: "session_summary" is easy, "normalize_csv" is hard).
- n: reps per condition. >= 5. Prefer 12.

Drop a candidate only if it genuinely cannot be operationalized (it isn't a
prompt change, or it can't be measured). Most should survive.

Emit ONLY valid JSON matching `BacklogPlanOutput` (a list of ExperimentThought):

{
  "status": "success",
  "summary": "one line on what you operationalized",
  "artifacts": [],
  "experiments": [
    {
      "id": "stable-kebab-slug",
      "thought": "Observation: ... Question: ... Candidate prompt to test (verbatim): \\\"...\\\"",
      "source": "vault/note/path.md",
      "prediction": "floor up, cost <= +15%",
      "agent_under_test": "builder",
      "tasks": ["session_summary", "normalize_csv"],
      "n": 12
    }
  ]
}

INHERITED FIELDS (every envelope also requires these — the harness REJECTS JSON
missing them):
- status: "success"
- summary: one-line plain-text description
- artifacts: [] (you write no files this phase)

Emit the JSON only.
"""


# ── the ADW ───────────────────────────────────────────────────────────────────

def main(prompt: str, config: str = "adws/adw_sssf_config/sssf.config.yaml",
         adw_id: str | None = None) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Capture the mining focus and the vault path")) as ph:
        ph.log(input=prompt)

    with run.phase(PhaseParams(name="harvest", kind="agent", owner="miner",
                               description="Read-only walk of the vault — extract testable principles with provenance")) as ph:
        harvest = ph.call(AgentCall(output_type=VaultHarvestOutput,
                                     prompt=prompt + HARVEST_DIRECTIVE))

    with run.phase(PhaseParams(name="operationalize", kind="agent", owner="scientist", retries=1,
                               description="Turn each candidate into a runnable experiment thought")) as ph:
        plan = ph.call(AgentCall(output_type=BacklogPlanOutput,
                                  prompt=prompt + OPERATIONALIZE_DIRECTIVE,
                                  previous=harvest))

    with run.phase(PhaseParams(name="emit", kind="code", owner="backlog",
                               description="Insert the experiment thoughts into the shared backlog (dedup by id)")) as ph:
        emitted, skipped = [], []
        for t in plan.experiments:
            try:
                if run.tracer.backlog_add(t):
                    emitted.append(t.id)
                else:
                    skipped.append(t.id)
            except Exception as e:  # noqa: BLE001 — one bad row must not kill the emit
                skipped.append(f"{t.id}:!{e}")
        ph.log(emitted=emitted, skipped=skipped,
               candidates=len(harvest.candidates), operationalized=len(plan.experiments))
        print(f"[adw_mine_vault] emitted {len(emitted)} new, skipped {len(skipped)} "
              f"(of {len(plan.experiments)} operationalized from {len(harvest.candidates)} candidates)")

    return run.finish(accepted=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default=DEFAULT_FOCUS,
                        help="mining focus / vault path (defaults to mining ~/Projects/vault)")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.config, args.adw_id))