Add a scripts/session_summary.py CLI tool that reads an SSSF trace SQLite
database and prints a one-line summary per session. Use only the Python standard
library (sqlite3, argparse, json).

Arguments:
- --db <path>      (required) path to the trace database
- --format <fmt>   (optional) "table" (default) or "json"

The database has (at least) these tables:

  sessions(adw_id TEXT, adw_name TEXT, request TEXT, status TEXT, engineer TEXT,
           started_at TEXT, ended_at TEXT, total_tokens INTEGER, total_cost REAL,
           archived INTEGER)
  phases(phase_id TEXT, adw_id TEXT, seq INTEGER, name TEXT, kind TEXT,
         owner TEXT, status TEXT, ...)

For each session (ordered by sessions.started_at ascending), emit one summary
with these columns:

  adw_id   sessions.adw_id
  status   sessions.status
  phases    "<passed>/<total>" where passed = count of phases with
            status='success' for that adw_id, total = count of all phases for
            that adw_id. A session with no phases is "0/0".
  tokens    sessions.total_tokens (treat NULL as 0)
  cost      sessions.total_cost  (treat NULL as 0.0)

Output formats:

  table (default):
    Line 1 is the header, the five column names lowercased and pipe-delimited:
        adw_id|status|phases|tokens|cost
    Then one line per session, values pipe-delimited in column order. cost is
    formatted to exactly 4 decimal places (e.g. 0.0000). tokens is a plain
    integer. With zero sessions, print ONLY the header line.

  json:
    A JSON array of objects, one per session, in order, each with keys:
    adw_id, status, phases_passed (int), phases_total (int), tokens (int),
    cost (number). With zero sessions, print exactly "[]".

Error handling (write a clear message to stderr and exit with the exact code):
- --db file does not exist              -> exit 2
- --db exists but has no `sessions` table -> exit 3
- --db exists, has sessions table, 0 rows  -> exit 0 (header only / "[]")
- missing required --db argument        -> non-zero exit (argparse default is fine)