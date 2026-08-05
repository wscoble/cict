#!/usr/bin/env python3
"""Grader for the normalize_csv task. Invoked by prompt_ab as:
    uv run python3 <task_dir>/grade.py <artifact_path> <task_dir>
Prints exactly:  behav=X/12 rubric=Y/8 [details]
"""
import csv, io, os, re, subprocess, sys

artifact = sys.argv[1]
task_dir = sys.argv[2]
FIX = os.path.join(task_dir, "fixture.csv")
FIXS = os.path.join(task_dir, "fixture_strict.csv")
FIXE = os.path.join(task_dir, "fixture_empty.csv")
OUT = "/tmp/_ab_nc_out.csv"

def run(args):
    return subprocess.run(["uv", "run", "python3", artifact] + args,
                          capture_output=True, text=True)

def parse(text):
    return list(csv.reader(io.StringIO(text)))

bh = 0; rb = 0; det = []

# ---- behavioral (12) ----
# B1 missing required args -> non-zero + stderr
r = run([]);  ok = r.returncode != 0 and r.stderr.strip() != ""
if ok: bh += 1
else: det.append("B1")
# B2 nonexistent input -> exit 2 + stderr
r = run(["--input", "/no/such/file.csv", "--key", "Email"])
if r.returncode == 2 and r.stderr.strip(): bh += 1
else: det.append(f"B2:rc={r.returncode}")
# B3 key not in headers -> exit 3 + stderr
r = run(["--input", FIX, "--key", "Nope"])
if r.returncode == 3 and r.stderr.strip(): bh += 1
else: det.append(f"B3:rc={r.returncode}")
# B4 normal run -> exit 0, 4 headers, 3 data rows
r = run(["--input", FIX, "--key", "email"])
rows = parse(r.stdout) if r.returncode == 0 else []
ok = r.returncode == 0 and len(rows) >= 1 and len(rows[0]) == 4 and len(rows) - 1 == 3
if ok: bh += 1
else: det.append(f"B4:rc={r.returncode},rows={len(rows)}")
# B5 headers lowercased
if rows and all(c == c.lower() and c == c.strip() for c in rows[0]): bh += 1
else: det.append("B5")
# B6 cells stripped (no leading/trailing space in any cell)
if rows and all(c == c.strip() for row in rows for c in row): bh += 1
else: det.append("B6")
# B7 blank row skipped (3 data rows, not 4)
if len(rows) - 1 == 3: bh += 1
else: det.append(f"B7:datarows={len(rows)-1}")
# B8 dedup last-wins: Alice once, age 29
hdr = rows[0] if rows else []
ai = hdr.index("email") if "email" in hdr else -1
agi = hdr.index("age") if "age" in hdr else -1
data = rows[1:] if rows else []
alices = [row for row in data if ai >= 0 and row[ai] == "alice@x.com"]
if len(alices) == 1 and agi >= 0 and alices[0][agi] == "29": bh += 1
else: det.append(f"B8:alices={len(alices)}")
# B9 type coercion: age int-like, score float-like
sci = hdr.index("score") if "score" in hdr else -1
ages = [row[agi] for row in data if agi >= 0 and row[agi]]
scores = [row[sci] for row in data if sci >= 0 and row[sci]]
if ages and all(re.fullmatch(r"\d+", a) for a in ages) and scores and all(re.fullmatch(r"\d+\.\d+", s) for s in scores): bh += 1
else: det.append("B9")
# B10 --output writes file equal to stdout
r1 = run(["--input", FIX, "--key", "email"])
r2 = run(["--input", FIX, "--key", "email", "--output", OUT])
file_ok = os.path.exists(OUT) and open(OUT).read() == r1.stdout
if r2.returncode == 0 and file_ok: bh += 1
else: det.append("B10")
if os.path.exists(OUT): os.remove(OUT)
# B11 --strict with bad value -> exit 4
r = run(["--input", FIXS, "--key", "id", "--strict"])
if r.returncode == 4 and r.stderr.strip(): bh += 1
else: det.append(f"B11:rc={r.returncode}")
# B12 empty file -> exit 0, empty output
r = run(["--input", FIXE, "--key", "id"])
if r.returncode == 0 and r.stdout.strip() == "": bh += 1
else: det.append(f"B12:rc={r.returncode},out={repr(r.stdout[:20])}")

# ---- rubric (8, static source checks) ----
src = open(artifact).read() if os.path.exists(artifact) else ""
if re.search(r"\bargparse\b", src): rb += 1
if re.search(r"\bcsv\b", src): rb += 1
if re.search(r"exit\(2\)", src): rb += 1
if re.search(r"exit\(3\)", src): rb += 1
if re.search(r"exit\(4\)", src): rb += 1
if re.search(r"\.strip\(\)", src): rb += 1
if re.search(r"\.lower\(\)", src): rb += 1
if re.search(r"def main|if __name__", src): rb += 1

print(f"behav={bh}/12 rubric={rb}/8" + (f" {' '.join(det)}" if det else ""))