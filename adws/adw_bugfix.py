#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "psycopg[binary]"]
# ///
"""ADW Bugfix — reproduce before you fix; the repro becomes the regression test.

Usage:
    uv run adws/adw_bugfix.py "<bug report or path/to/bug.md>" [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases:
    engineer(request)
      -> scout(diagnose, read-only: root cause + repro strategy; change nothing)
      -> builder(repro: write a self-contained repro script at repro/repro_<adw_id>.sh
                 that exits NON-ZERO while the bug is present)
      -> code(confirm_bug: run the repro, ASSERT it FAILS — if it passes, the bug
              was not reproduced; loop back to repro, bounded)
      -> builder(fix) -> code(test: run the repro, ASSERT it now PASSES)
      -> code(verify_fix: DIFFERENTIAL TEST — revert the fix, assert the repro
              FAILS again, re-apply, assert PASS. Proves the repro tracks the
              code diff, not a side-effect. Loops fix->test->verify, bounded.)
      -> git(commit the fix + the repro together)

The discipline is structural, not prose: you CANNOT fix a bug you cannot
demonstrate, and you CANNOT ship a fix the repro doesn't actually track.

Two guards (learned from the reaper-bug proof run):

  1. DB ISOLATION — the builder runs against an ephemeral DB clone ($REPRO_DB_URL),
     not production ($DATABASE_URL). The builder is told to use $REPRO_DB_URL;
     mutating production is out of scope. The ephemeral clone has the same schema
     (same column types) so type-specific bugs reproduce; it's discarded after.
     [Finding 1: fix_1 mutated the production schema via ALTER TABLE.]

  2. DIFFERENTIAL TEST — after the fix passes the repro, the fix is reverted
     (git stash, keeping the staged repro); the repro MUST fail again; the fix is
     re-applied; the repro MUST pass. If the repro passes with the fix reverted,
     it's tracking a side-effect (a DB mutation, a stale comment, external state),
     not the code — the fix is rejected and the builder is sent back.
     [Finding 2: the repro's regex matched a stale `#`-comment instead of the fix,
     and a DB mutation made the no-cast query work — test_2 passed for the wrong
     reason. The differential test catches both: revert the code fix → the DB
     mutation persists → repro still passes → differential fails → rejected.]

This is the factory's self-repair loop. When a bug surfaces, dispatch adw_bugfix
against it instead of hand-editing — the factory reproduces, fixes, proves the
fix is load-bearing, and leaves a regression test behind.
"""

import argparse
import os
import subprocess
import sys
import tempfile

from adw_modules import agents, gates, git_helper, quality, session, utils
from adw_modules.data_types import (AgentCall, BuildOutput, PhaseParams,
                                    QualityResult, ScoutOutput)
from adw_modules.tracer import SQLITE_SCHEMA

REQUIRED_AGENTS = ["scout", "builder"]
MAX_REPRO_LOOPS = 2      # how many times to try to write a repro that fails
MAX_FIX_LOOPS = 3       # how many times to try to fix before giving up

# Factory tables cloned into the ephemeral repro schema (shape only, no data).
FACTORY_TABLES = ["sessions", "phases", "events", "envelopes", "gate_results",
                  "processes", "agent_sessions", "experiment_backlog"]

DB_SCOPE_NOTE = (
    "\n\n--- DB SCOPE (read carefully) ---\n"
    "Use `$REPRO_DB_URL` (an isolated ephemeral DB clone with the SAME schema as "
    "production) for ALL database access. Do NOT connect to `$DATABASE_URL` — "
    "production is out of scope and mutating it is a defect. You may mutate the "
    "repo working tree and the ephemeral DB freely; the ephemeral DB is discarded "
    "after the run. NOTE: a DB mutation alone is NOT a valid fix — the regression "
    "test (repro) must pass because of your CODE change, not a schema mutation. "
    "A differential test reverts your code and re-runs the repro; if it still "
    "passes, your fix is rejected."
)


def _repro_argv(run) -> list[str]:
    """The single command that both proves the bug and guards its return."""
    return ["bash", str(run.repo_root / "repro" / f"repro_{run.adw_id}.sh")]


def _repro_result(check) -> QualityResult:
    """Wrap one repro QualityCheckResult so it can ride back to the builder as
    an envelope through quality.as_envelope — the same door an agent's report
    uses, so the repair loop is unchanged from adw_build_test."""
    failures = ([] if check.passed else
                [f"{check.name}: `{check.command}` exited {check.returncode}\n"
                 f"{check.output_tail}".rstrip()])
    return QualityResult(passed=check.passed, checks=[check],
                          failures=failures, artifacts=[check.output_artifact])


# ── guard 1: ephemeral DB isolation ───────────────────────────────────────────

def _provision_repro_db(run):
    """Provision an isolated ephemeral DB for the builder's repro + fix. Returns
    (repro_db_url, teardown_fn). If DATABASE_URL is postgres, creates an isolated
    schema in the same instance (same type — so postgres-specific bugs reproduce)
    and clones the factory table shapes into it. If sqlite (or no DB), creates an
    ephemeral sqlite file with the factory schema. teardown drops/removes it."""
    prod = os.environ.get("DATABASE_URL")
    schema_name = f"sssf_repro_{run.adw_id.replace('-', '_')}"
    if prod and prod.startswith("postgres"):
        import psycopg
        conn = psycopg.connect(prod, autocommit=True)
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        conn.execute(f'CREATE SCHEMA "{schema_name}"')
        for tbl in FACTORY_TABLES:
            try:
                conn.execute(f'CREATE TABLE "{schema_name}"."{tbl}" '
                             f'(LIKE public."{tbl}" INCLUDING ALL)')
            except psycopg.Error:
                pass  # table not present in public yet — skip
        conn.close()
        sep = "&" if "?" in prod else "?"
        # search_path: temp schema first (unqualified tables resolve there), then
        # public + pg_catalog so psql meta-commands still work. URL-encode the
        # inner '=' and ',' so libpq parses the options value correctly.
        opt = f"-csearch_path={schema_name},public,pg_catalog"
        opt_enc = opt.replace("=", "%3D").replace(",", "%2C")
        url = f"{prod}{sep}options={opt_enc}"
        def teardown():
            try:
                c = psycopg.connect(prod, autocommit=True)
                c.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                c.close()
            except Exception:
                pass
        return url, teardown
    else:
        import sqlite3
        fd, path = tempfile.mkstemp(suffix=".db", prefix="sssf_repro_")
        os.close(fd)
        c = sqlite3.connect(path)
        c.executescript(SQLITE_SCHEMA)
        c.commit(); c.close()
        def teardown():
            try: os.unlink(path)
            except OSError: pass
        return path, teardown


# ── guard 2: differential test (the hard enforcement) ─────────────────────────

def _differential_check(run, repro_argv, repro_path) -> tuple[bool, str]:
    """Prove the repro's pass is caused by the CODE diff, not a side-effect (a
    DB schema mutation, a stale comment, external state). Stash the fix (keep the
    staged repro), run the repro — it MUST fail (the bug is back). Pop, run — it
    MUST pass. If the repro passes with the fix reverted, it's tracking a side-
    effect, not the code — reject and send the builder back."""
    repo = run.repo_root
    def git(*a):
        return subprocess.run(["git", "-C", str(repo), "-c", "safe.directory=*", *a],
                              capture_output=True, text=True)
    rel = "repro/" + repro_path.name
    # Unstage everything, then stage ONLY the repro so it survives the stash
    # (--keep-index keeps staged files in the working tree while the unstaged
    # fix gets stashed).
    git("reset", "HEAD", "--", ".")
    git("add", "--", rel)
    stashed = False
    try:
        stash = git("stash", "push", "-u", "--keep-index", "-m", "sssf-diff-check")
        stashed = (stash.returncode == 0
                   and "No local changes to save" not in stash.stdout)
        if not stashed:
            # No code changes outside the repro — the "fix" IS only the repro.
            # confirm_bug already proved the repro fails without a fix (there
            # was no fix at that point), so there's nothing to differentially
            # test. Accept.
            return True, "no code changes outside the repro to differentially test"
        # With the fix reverted: the bug must be back → repro MUST fail.
        r_no_fix = quality.run_command(run, repro_argv, name="repro-no-fix")
        if r_no_fix.passed:
            return False, (
                "DIFFERENTIAL TEST FAILED: the repro PASSES with the fix reverted. "
                "It is tracking a side-effect (a DB schema mutation, a stale comment, "
                "or external state), NOT the code diff. The fix must not rely on "
                "state mutated beyond the repo working tree. Scope your writes to "
                "the repo + $REPRO_DB_URL (the ephemeral clone), and make sure the "
                "repro extracts/reads the ACTUAL changed code, not a stale comment "
                "or a different line.")
        # Re-apply the fix: repro MUST pass again.
        git("stash", "pop"); stashed = False
        r_with_fix = quality.run_command(run, repro_argv, name="repro-with-fix")
        if not r_with_fix.passed:
            return False, (
                "DIFFERENTIAL TEST FAILED: the repro did not pass after re-applying "
                "the fix (stash pop) — the working tree is inconsistent. Re-apply "
                "your fix cleanly.")
        return True, "differential: repro tracks the diff (fails without fix, passes with)"
    finally:
        if stashed:
            git("stash", "pop")
        git("reset", "HEAD", "--", rel)


# ── the ADW ───────────────────────────────────────────────────────────────────

def main(prompt: str, config: str = "adws/adw_sssf_config/sssf.config.yaml",
         adw_id: str | None = None) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)

    # Guard 1: provision an isolated ephemeral DB for the builder's repro + fix.
    repro_db_url, teardown_repro_db = _provision_repro_db(run)
    os.environ["REPRO_DB_URL"] = repro_db_url
    try:
        with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                                   description="Capture the bug report")) as ph:
            ph.log(input=prompt)

        # 1. Diagnose — read-only recon. Find the root cause; propose a repro
        #    strategy. Change nothing.
        with run.phase(PhaseParams(name="diagnose", kind="agent", owner="scout",
                                   description="Find the root cause and a repro strategy; change nothing")) as ph:
            diagnosis = ph.call(AgentCall(
                output_type=ScoutOutput,
                prompt=(f"Find the root cause of this bug and a strategy to reproduce it "
                        f"in a self-contained script. Cite exact file:line for the faulty code. "
                        f"Do NOT fix it — only locate and explain.\n\nBUG:\n{prompt}"),
                gates=[gates.artifacts_exist]))

        # 2. Repro — write a failing repro script. Bounded.
        repro_path = run.repo_root / "repro" / f"repro_{run.adw_id}.sh"
        repro_ref = diagnosis
        check = None
        for r in range(1, MAX_REPRO_LOOPS + 1):
            with run.phase(PhaseParams(name=f"repro_{r}", kind="agent", owner="builder",
                                       description="Write a self-contained repro script that exits non-zero "
                                                   "while the bug is present")) as ph:
                repro_build = ph.call(AgentCall(
                    output_type=BuildOutput,
                    prompt=(f"Write a self-contained repro script at `repro/repro_{run.adw_id}.sh` "
                            f"that exits NON-ZERO when this bug is present and ZERO when it is fixed. "
                            f"Do NOT fix the bug — only write the repro. Run it with "
                            f"`bash repro/repro_{run.adw_id}.sh` and confirm it fails (non-zero exit) "
                            f"before reporting.{DB_SCOPE_NOTE}\n\nBUG:\n{prompt}"),
                    previous=repro_ref,
                    gates=[gates.artifacts_exist]))

            with run.phase(PhaseParams(name=f"confirm_bug_{r}", kind="code", owner="quality",
                                       description="Run the repro; assert it FAILS (non-zero) — the bug must "
                                                   "be demonstrated before any fix is attempted")) as ph:
                check = quality.run_command(run, _repro_argv(run), name="repro")
                ph.log(reproduced=not check.passed, exit=check.returncode,
                       artifacts=[check.output_artifact])

            if not check.passed:
                break  # bug reproduced (repro exited non-zero) — proceed to the fix
            repro_ref = quality.as_envelope(_repro_result(check), "repro")
        else:
            return run.finish(accepted=False,
                              reason=(f"could not reproduce the bug after {MAX_REPRO_LOOPS} repro "
                                     f"attempt(s) — the repro at {repro_path} never failed (exit 0). "
                                     f"Either the bug is already fixed, or it is not deterministically "
                                     f"reproducible by a script."))

        # 3. Fix -> test -> verify(differential) loop. The repro is the gate.
        previous = diagnosis
        last_reason = ""
        for i in range(1, MAX_FIX_LOOPS + 1):
            with run.phase(PhaseParams(name=f"fix_{i}", kind="agent", owner="builder", retries=1,
                                       description="Fix the root cause; do not touch the repro script")) as ph:
                previous = ph.call(AgentCall(
                    output_type=BuildOutput,
                    prompt=(f"Fix the root cause described in the diagnosis (previous_envelope). Do NOT "
                            f"modify the repro script at `repro/repro_{run.adw_id}.sh` — it is the "
                            f"regression test and must pass (exit 0) after your fix.{DB_SCOPE_NOTE}\n\n"
                            f"BUG:\n{prompt}"),
                    previous=previous,
                    gates=[gates.artifacts_exist]))

            with run.phase(PhaseParams(name=f"test_{i}", kind="code", owner="quality",
                                       description="Run the repro; assert it now PASSES (exit 0) — the bug is fixed")) as ph:
                test_check = quality.run_command(run, _repro_argv(run), name="repro")
                ph.log(fixed=test_check.passed, exit=test_check.returncode,
                       artifacts=[test_check.output_artifact])

            if not test_check.passed:
                last_reason = (f"the repro still failed after fix attempt {i} "
                               f"(exit {test_check.returncode})")
                previous = quality.as_envelope(_repro_result(test_check), "repro")
                continue

            # Guard 2: differential test — the repro must track the diff, not a
            # side-effect. Revert the fix, assert the repro fails, re-apply,
            # assert it passes.
            with run.phase(PhaseParams(name=f"verify_fix_{i}", kind="code", owner="quality",
                                       description="Differential test: revert the fix, assert the repro FAILS, "
                                                   "re-apply, assert PASS — proves the repro tracks the diff, "
                                                   "not a side-effect")) as ph:
                diff_ok, diff_reason = _differential_check(run, _repro_argv(run), repro_path)
                ph.log(differential=diff_ok, reason=diff_reason)

            if diff_ok:
                break

            last_reason = diff_reason
            # Hand the differential failure back to the builder as a failed test.
            diff_fail = QualityResult(passed=False, checks=[], failures=[diff_reason],
                                      artifacts=[])
            previous = quality.as_envelope(diff_fail, "repro")
        else:
            return run.finish(accepted=False,
                              reason=(f"could not ship a fix the repro tracks after "
                                     f"{MAX_FIX_LOOPS} attempt(s). Last: {last_reason}"))

        # 4. Commit the fix + the repro (the regression test) together.
        with run.phase(PhaseParams(name="commit", kind="code", owner="git",
                                   description="Land the fix and the repro script together")) as ph:
            message = previous.commit_message or f"fix({run.adw_id}): {prompt[:72]}"
            ph.log(sha=git_helper.commit_all(message), message=message)

        return run.finish(accepted=True)
    finally:
        teardown_repro_db()
        os.environ.pop("REPRO_DB_URL", None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="bug report (inline text or path to a .md file)")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.config, args.adw_id))