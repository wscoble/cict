#!/usr/bin/env python3
"""prompt_ab — quantitative A/B regression harness for agent prompts.

Treats an agent's prompt (system.md) as a versioned artifact under test. Runs a
candidate prompt against a baseline across a task suite (n repetitions each,
interleaved), grades every run on the triad (speed / cost / quality), records
every run into an append-only ledger keyed by prompt sha256, and reports whether
the candidate is a validated improvement.

Usage:
  prompt_ab run --agent builder \
    --prompt-path adws/adw_data/prompt_engineering/builder/system.md \
    --candidate /tmp/system_axiom.md --tasks normalize_csv --n 12 \
    --label "axiom: only-trust-what-can-fail"
  prompt_ab report --exp <exp_id>
  prompt_ab history --agent builder
"""
import argparse, csv, fcntl, hashlib, io, json, os, random, re, statistics, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]   # .../super-simple-software-factory
TOOL = REPO / "tools/prompt_ab"
# PVC-backed in k8s (the controller sets PROMPT_AB_LEDGER to a path on a shared
# nfs-csi PVC so the ledger survives pod death and is safe under concurrency —
# append_ledger holds fcntl.LOCK_EX across the write). Defaults to the in-repo
# path for host/dev runs. adw_experiment.py's LEDGER honors the same env.
_hist_env = os.environ.get("PROMPT_AB_LEDGER")
HISTORY = Path(_hist_env) if _hist_env else TOOL / "history.jsonl"
TASKS = TOOL / "tasks"


def sha(text): return hashlib.sha256(text.encode()).hexdigest()[:12]


def now_iso(): return datetime.now(timezone.utc).isoformat()


def load_task(name):
    d = TASKS / name
    meta = json.load(open(d / "task.json"))
    meta["dir"] = str(d)
    meta["spec"] = (d / meta["spec"]).read_text()
    return meta


def reset_repo(ref):
    subprocess.run(["git", "reset", "--hard", ref], cwd=REPO, capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=REPO, capture_output=True)


def write_prompt(path, content):
    p = REPO / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def run_agent(agent, spec):
    """Run adw_prompt --agent <agent> '<spec>'; return (time_s, tokens, rc)."""
    log = subprocess.run(
        ["uv", "run", "adws/adw_prompt.py", "--agent", agent, spec],
        cwd=REPO, capture_output=True, text=True)
    out = log.stdout + log.stderr
    tok = 0
    m = re.findall(r"used ([\d,]+) tokens", out)
    if m: tok = int(m[-1].replace(",", ""))
    t = 0.0
    m = re.findall(r"✓ \w+ ([\d.]+)s", out)
    if m: t = float(m[-1])
    return t, tok, log.returncode


def grade(task, artifact_rel):
    art = REPO / artifact_rel
    if not art.exists():
        return 0, task["behav_max"], 0, task["rubric_max"], "no_artifact"
    r = subprocess.run(["uv", "run", "python3", task["dir"] + "/grade.py",
                        str(art), task["dir"]], cwd=REPO, capture_output=True, text=True)
    line = r.stdout.strip() or "behav=0/? rubric=0/?"
    bh = int((re.search(r"behav=(\d+)", line) or [0, 0])[1])
    rb = int((re.search(r"rubric=(\d+)", line) or [0, 0])[1])
    det = re.sub(r"behav=\d+/\d+ rubric=\d+/\d+\s*", "", line)
    return bh, task["behav_max"], rb, task["rubric_max"], det


def append_ledger(row):
    # flock'd append: safe under concurrency (e.g. k8s pods sharing a PVC-backed
    # ledger). LOCK_EX on the fd; the lock is released on close(). One row per
    # write+flush so a reader never sees a half line.
    with open(HISTORY, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(row) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def read_ledger():
    if not HISTORY.exists(): return []
    return [json.loads(l) for l in open(HISTORY) if l.strip()]


def bootstrap_ci(a, b, n=4000):
    """95% CI on mean(b)-mean(a) via bootstrap (pure stdlib)."""
    if not a or not b: return None
    rng = random.Random(7)
    deltas = []
    for _ in range(n):
        da = statistics.fmean(rng.choice(a) for _ in range(len(a)))
        db = statistics.fmean(rng.choice(b) for _ in range(len(b)))
        deltas.append(db - da)
    deltas.sort()
    return deltas[int(0.025 * n)], deltas[int(0.975 * n)]


def aggregate(rows):
    def col(name): return [r[name] for r in rows]
    return {
        "n": len(rows),
        "time": statistics.fmean(col("time_s")),
        "time_sd": statistics.pstdev(col("time_s")) if len(rows) > 1 else 0,
        "tokens": statistics.fmean(col("tokens")),
        "tokens_sd": statistics.pstdev(col("tokens")) if len(rows) > 1 else 0,
        "q": statistics.fmean(col("q_ratio")),                       # behav ratio
        "q_sd": statistics.pstdev(col("q_ratio")) if len(rows) > 1 else 0,
        "q_min": min(col("q_ratio")),
        "rubric": statistics.fmean(col("rubric_ratio")),
        "rubric_min": min(col("rubric_ratio")),
    }


def verdict(A, B):
    q_up = B["q"] > A["q"] and B["q_min"] >= A["q_min"]
    cost_ok = B["tokens"] <= A["tokens"] * 1.15
    speed_ok = B["time"] <= A["time"] * 1.20
    if B["q"] < A["q"] - 1e-9: return "REGRESSION"
    if q_up and cost_ok and speed_ok: return "VALIDATED IMPROVEMENT"
    return "INCONCLUSIVE"


def cmd_run(a):
    base_content = (REPO / a.prompt_path).read_text()
    cand_content = Path(a.candidate).read_text()
    base_sha, cand_sha = sha(base_content), sha(cand_content)
    exp_id = "%08x" % random.randrange(16**8)
    ref = a.baseline_ref
    tasks = [load_task(t) for t in a.tasks.split(",")]
    print(f"exp_id={exp_id}  agent={a.agent}  n={a.n}  tasks={a.tasks}")
    print(f"  baseline  sha={base_sha}  <- {a.prompt_path}")
    print(f"  candidate sha={cand_sha}  <- {a.candidate}   label={a.label!r}")
    order = []
    for i in range(1, a.n + 1):
        order += [("A", i), ("B", i)]
    for task in tasks:
        print(f"\n== task: {Path(task['dir']).name}  (artifact {task['artifact']}) ==")
        for cond, idx in order:
            reset_repo(ref)
            content = base_content if cond == "A" else cand_content
            write_prompt(a.prompt_path, content)
            t0 = time.time()
            t, tok, rc = run_agent(a.agent, task["spec"])
            wall = time.time() - t0
            bh, bmax, rb, rmax, det = grade(task, task["artifact"])
            qr = bh / bmax; rr = rb / rmax
            row = dict(ts=now_iso(), exp_id=exp_id, agent=a.agent,
                       task=Path(task["dir"]).name, cond=cond, idx=idx,
                       prompt_sha=(base_sha if cond == "A" else cand_sha),
                       label=a.label, time_s=round(t, 1), wall_s=round(wall, 1),
                       tokens=tok, behav=bh, behav_max=bmax, q_ratio=round(qr, 4),
                       rubric=rb, rubric_max=rmax, rubric_ratio=round(rr, 4),
                       details=det)
            append_ledger(row)
            print(f"  {cond}{idx}  time={t:.1f}s tok={tok} behav={bh}/{bmax} rubric={rb}/{rmax} {det}")
    reset_repo(ref)
    # ---- aggregate + verdict ----
    led = [r for r in read_ledger() if r["exp_id"] == exp_id]
    A = aggregate([r for r in led if r["cond"] == "A"])
    B = aggregate([r for r in led if r["cond"] == "B"])
    ci = bootstrap_ci([r["q_ratio"] for r in led if r["cond"] == "A"],
                      [r["q_ratio"] for r in led if r["cond"] == "B"])
    print("\n" + "=" * 70)
    print(f"{'cond':5} {'n':>3} {'time(s)':>9} {'±sd':>6} {'tokens':>9} {'±sd':>8} "
          f"{'behav%':>8} {'min':>6} {'rubric%':>8} {'min':>6}")
    for name, g in [("A", A), ("B", B)]:
        print(f"{name:5} {g['n']:3} {g['time']:9.1f} {g['time_sd']:6.1f} {g['tokens']:9.0f} "
              f"{g['tokens_sd']:8.0f} {g['q']*100:8.1f} {g['q_min']*100:6.1f} "
              f"{g['rubric']*100:8.1f} {g['rubric_min']*100:6.1f}")
    print(f"\nΔ(B-A):  time {B['time']-A['time']:+.1f}s ({(B['time']-A['time'])/A['time']*100:+.0f}%)  "
          f"tokens {B['tokens']-A['tokens']:+.0f} ({(B['tokens']-A['tokens'])/A['tokens']*100:+.0f}%)  "
          f"behav {(B['q']-A['q'])*100:+.1f}pp  rubric {(B['rubric']-A['rubric'])*100:+.1f}pp")
    if ci:
        print(f"bootstrap 95% CI on behav delta: [{ci[0]*100:+.1f}pp, {ci[1]*100:+.1f}pp]  "
              f"({'significant' if ci[0] > 0 else 'not significant' if ci[0] <= 0 <= ci[1] else 'negative'})")
    print(f"\nVERDICT: {verdict(A, B)}")
    print(f"ledger: {HISTORY}  (exp_id={exp_id})")


def cmd_report(a):
    led = read_ledger()
    if a.exp: led = [r for r in led if r["exp_id"] == a.exp]
    elif a.agent: led = [r for r in led if r["agent"] == a.agent]
    if not led: print("no runs"); return
    exps = {}
    for r in led: exps.setdefault(r["exp_id"], []).append(r)
    for eid, rows in exps.items():
        A = aggregate([r for r in rows if r["cond"] == "A"])
        B = aggregate([r for r in rows if r["cond"] == "B"])
        label = next((r["label"] for r in rows if r.get("label")), "")
        print(f"exp {eid}  agent={rows[0]['agent']}  tasks={set(r['task'] for r in rows)}  n={A['n']}v{B['n']}  {label}")
        print(f"  A: behav {A['q']*100:.1f}% (min {A['q_min']*100:.1f})  tok {A['tokens']:.0f}  t {A['time']:.1f}s")
        print(f"  B: behav {B['q']*100:.1f}% (min {B['q_min']*100:.1f})  tok {B['tokens']:.0f}  t {B['time']:.1f}s")
        print(f"  -> {verdict(A, B)}")


def cmd_history(a):
    led = read_ledger()
    if a.agent: led = [r for r in led if r["agent"] == a.agent]
    # group by prompt_sha -> show trajectory of each prompt version
    by_sha = {}
    for r in led: by_sha.setdefault(r["prompt_sha"], []).append(r)
    print(f"{'sha':12} {'cond':4} {'n':>3} {'behav%':>7} {'min':>6} {'tokens':>8} {'time':>6} {'label'}")
    for s, rows in sorted(by_sha.items(), key=lambda kv: -statistics.fmean(r["q_ratio"] for r in kv[1])):
        g = aggregate(rows)
        label = next((r["label"] for r in rows if r.get("label")), "")
        print(f"{s:12} {rows[0]['cond']:4} {g['n']:3} {g['q']*100:7.1f} {g['q_min']*100:6.1f} "
              f"{g['tokens']:8.0f} {g['time']:6.1f} {label}")


def main():
    p = argparse.ArgumentParser(prog="prompt_ab")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run an A/B experiment")
    r.add_argument("--agent", required=True)
    r.add_argument("--prompt-path", required=True, help="repo-relative path to the agent system.md to swap")
    r.add_argument("--candidate", required=True, help="path to the candidate prompt file")
    r.add_argument("--tasks", required=True, help="comma list of task names under tools/prompt_ab/tasks/")
    r.add_argument("--n", type=int, default=10)
    r.add_argument("--baseline-ref", default="HEAD")
    r.add_argument("--label", default="")
    r.set_defaults(func=cmd_run)
    rp = sub.add_parser("report", help="show A/B results for an experiment or agent")
    rp.add_argument("--exp"); rp.add_argument("--agent")
    rp.set_defaults(func=cmd_report)
    h = sub.add_parser("history", help="trajectory of every prompt version by sha")
    h.add_argument("--agent")
    h.set_defaults(func=cmd_history)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()