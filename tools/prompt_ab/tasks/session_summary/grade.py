#!/usr/bin/env python3
"""Grader for the session_summary task. Invoked by prompt_ab as:
    uv run python3 <task_dir>/grade.py <artifact_path> <task_dir>
Prints exactly:  behav=X/12 rubric=Y/8 [details]
"""
import json, os, re, subprocess, sqlite3, sys

artifact = sys.argv[1]
task_dir = sys.argv[2]
FIX   = os.path.join(task_dir, "fixture.db")
FIXE  = os.path.join(task_dir, "fixture_empty.db")
FIXN  = os.path.join(task_dir, "fixture_notrace.db")

def run(args):
    return subprocess.run(["uv", "run", "python3", artifact] + args,
                          capture_output=True, text=True)

# ---- ground truth from the fixture (the grader is self-consistent with it) ----
db = sqlite3.connect(FIX)
truth = []
for adw_id, status, tokens, cost, started in db.execute(
        "select adw_id, status, total_tokens, total_cost, started_at "
        "from sessions order by started_at"):
    total = db.execute("select count(*) from phases where adw_id=?", (adw_id,)).fetchone()[0]
    passed = db.execute("select count(*) from phases where adw_id=? and status='success'",
                        (adw_id,)).fetchone()[0]
    truth.append({"adw_id": adw_id, "status": status,
                  "passed": passed, "total": total,
                  "tokens": tokens or 0, "cost": cost or 0.0})

bh = 0; rb = 0; det = []

# ---- behavioral (12) ----
# B1 no args -> non-zero + stderr
r = run([])
if r.returncode != 0 and r.stderr.strip() != "": bh += 1
else: det.append("B1")
# B2 nonexistent db -> exit 2 + stderr
r = run(["--db", "/no/such/trace.db"])
if r.returncode == 2 and r.stderr.strip(): bh += 1
else: det.append(f"B2:rc={r.returncode}")
# B3 not a trace db (no sessions table) -> exit 3 + stderr
r = run(["--db", FIXN])
if r.returncode == 3 and r.stderr.strip(): bh += 1
else: det.append(f"B3:rc={r.returncode}")
# B4 valid db -> exit 0, non-empty output
r = run(["--db", FIX])
if r.returncode == 0 and r.stdout.strip() != "": bh += 1
else: det.append(f"B4:rc={r.returncode}")
# parse the table output
lines = r.stdout.strip().splitlines() if r.returncode == 0 else []
header = lines[0].split("|") if lines else []
data_rows = [ln.split("|") for ln in lines[1:]] if len(lines) > 1 else []
# B5 header: lowercased, 5 cols named adw_id|status|phases|tokens|cost
if header and len(header) == 5 and header == ["adw_id", "status", "phases", "tokens", "cost"]:
    bh += 1
else: det.append(f"B5:hdr={header}")
# B6 row count matches session count
if len(data_rows) == len(truth): bh += 1
else: det.append(f"B6:rows={len(data_rows)}/exp={len(truth)}")
# map rows by adw_id for per-session checks
rows_by_id = {}
for row in data_rows:
    if len(row) >= 5: rows_by_id[row[0]] = row
# B7 phases "P/T" matches per session
ok7 = all(str(t["passed"]) + "/" + str(t["total"]) == rows_by_id.get(t["adw_id"], ["", "", "", "", ""])[2]
          for t in truth)
if ok7 and rows_by_id: bh += 1
else: det.append("B7")
# B8 tokens matches per session
ok8 = all(str(t["tokens"]) == rows_by_id.get(t["adw_id"], ["", "", "", "", ""])[3] for t in truth)
if ok8 and rows_by_id: bh += 1
else: det.append("B8")
# B9 status matches per session
ok9 = all(t["status"] == rows_by_id.get(t["adw_id"], ["", "", "", "", ""])[1] for t in truth)
if ok9 and rows_by_id: bh += 1
else: det.append("B9")
# B10 adw_id column in started_at order
ok10 = [t["adw_id"] for t in truth] == [row[0] for row in data_rows if len(row) >= 5]
if ok10 and data_rows: bh += 1
else: det.append("B10")
# B11 --format json -> valid JSON array, one obj per session, expected keys
rj = run(["--db", FIX, "--format", "json"])
ok11 = False
if rj.returncode == 0:
    try:
        arr = json.loads(rj.stdout)
        keys = {"adw_id", "status", "phases_passed", "phases_total", "tokens", "cost"}
        ok11 = isinstance(arr, list) and len(arr) == len(truth) and \
               all(keys <= set(o.keys()) for o in arr)
    except Exception:
        ok11 = False
if ok11: bh += 1
else: det.append("B11")
# B12 empty db -> exit 0, table = header only (1 line), json = "[]"
re_t = run(["--db", FIXE])
re_j = run(["--db", FIXE, "--format", "json"])
ok12 = re_t.returncode == 0 and len(re_t.stdout.strip().splitlines()) == 1 and \
       re_j.returncode == 0 and re_j.stdout.strip() == "[]"
if ok12: bh += 1
else: det.append(f"B12:t={len(re_t.stdout.splitlines())},j={re_j.stdout.strip()[:10]}")

# ---- rubric (8, static source checks) ----
src = open(artifact).read() if os.path.exists(artifact) else ""
if re.search(r"\bargparse\b", src): rb += 1
if re.search(r"\bsqlite3\b", src): rb += 1
if re.search(r"exit\(2\)", src): rb += 1
if re.search(r"exit\(3\)", src): rb += 1
if re.search(r"\bphases\b", src): rb += 1                       # references the phases table (the JOIN)
if re.search(r"\bcount\s*\(", src, re.I): rb += 1               # aggregate
if re.search(r"\b(coalesce|ifnull|total_tokens)\b", src, re.I): rb += 1   # NULL handling
if re.search(r"def main|if __name__", src): rb += 1

print(f"behav={bh}/12 rubric={rb}/8" + (f" {' '.join(det)}" if det else ""))