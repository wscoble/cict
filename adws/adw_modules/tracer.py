"""Tracer: every event lands in JSONL and a queryable DB AS IT HAPPENS.

Files are the raw record; the trace DB is the queryable mirror the UI polls.

Two backends, one interface, picked at construction time:

  * SQLite  — the host/dev default. A file on disk (config `observability.db`,
              default adws/adw_data/sssf.db). WAL mode so the UI reads while a
              run writes. No extra deps.
  * Postgres — the cluster/pod mode. Selected when `DATABASE_URL` is set or the
              db arg is a postgres:// URL. One cluster-wide DB kills the
              concurrent-write corruption SQLite had under parallel pods, and
              the schema self-heals on every connect (CREATE TABLE IF NOT
              EXISTS + additive ALTER) so a pod needs no init Job.

psycopg (v3) is imported lazily so a host running SQLite-only never needs it
installed; the Postgres path raises a clear error if it is missing.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .data_types import AgentConfig, EventRecord, GateReport, Phase
from .utils import ensure_dir, new_id, now_iso

# ── shared schema (dialect-agnostic column names/types; backend owns the id + json col) ─
# The two backends differ only in: (1) the auto-id column for gate_results and
# processes, (2) JSON storage (TEXT both — kept uniform so json.dumps works for
# both and the visualizer reads a string in both), and (3) connection pragmas.
# int-as-bool columns (valid/passed/archived) are kept INTEGER everywhere so the
# same `int(bool)` call sites work in both backends.

_MIGRATIONS = [("agent_sessions", "color", "TEXT"),
               ("gate_results", "checks_json", "TEXT"),
               ("sessions", "adw_name", "TEXT"),
               ("agent_sessions", "context_tokens", "INTEGER"),
               ("agent_sessions", "context_window", "INTEGER"),
               ("sessions", "archived", "INTEGER DEFAULT 0")]


# ═══════════════════════════════════════════════════════════════════════════
# SQLite backend (host/dev) — the original implementation, unchanged.
# ═══════════════════════════════════════════════════════════════════════════

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  adw_id        TEXT PRIMARY KEY,
  adw_name      TEXT,
  request       TEXT,
  status        TEXT,
  engineer      TEXT,
  started_at    TEXT, ended_at TEXT,
  total_tokens  INTEGER DEFAULT 0, total_cost REAL DEFAULT 0,
  archived      INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS phases (
  phase_id      TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  seq           INTEGER,
  name TEXT, kind TEXT, owner TEXT, description TEXT,
  status        TEXT DEFAULT 'fail',
  attempt       INTEGER DEFAULT 0, retries INTEGER DEFAULT 0,
  error         TEXT,
  started_at    TEXT, ended_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_id      TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,
  parent_id     TEXT,
  type          TEXT,
  name          TEXT,
  payload_json  TEXT,
  tokens        INTEGER,
  started_at    TEXT, ended_at TEXT
);
CREATE TABLE IF NOT EXISTS envelopes (
  envelope_id   TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,
  agent         TEXT,
  output_type   TEXT,
  payload_json  TEXT,
  valid         INTEGER,
  attempt       INTEGER,
  created_at    TEXT
);
CREATE TABLE IF NOT EXISTS gate_results (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,
  attempt       INTEGER,
  gate          TEXT,
  passed        INTEGER,
  violations_json TEXT,
  checks_json   TEXT,
  created_at    TEXT
);
CREATE TABLE IF NOT EXISTS processes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  adw_id        TEXT REFERENCES sessions,
  kind          TEXT,
  name          TEXT,
  pid           INTEGER,
  command       TEXT,
  started_at    TEXT, ended_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_sessions (
  adw_id        TEXT REFERENCES sessions,
  agent         TEXT,
  coding_agent  TEXT, model TEXT, color TEXT,
  session_id    TEXT,
  context_tokens INTEGER,
  context_window INTEGER,
  created_at    TEXT, last_used_at TEXT,
  PRIMARY KEY (adw_id, agent)
);
CREATE TABLE IF NOT EXISTS experiment_backlog (
  id              TEXT PRIMARY KEY,
  thought         TEXT NOT NULL,
  source          TEXT,
  prediction      TEXT,
  agent_under_test TEXT,
  tasks           TEXT,
  n               INTEGER,
  status          TEXT NOT NULL DEFAULT 'pending',
  exp_adw_id      TEXT,
  added_at        TEXT NOT NULL,
  started_at      TEXT,
  finished_at     TEXT
);
CREATE TABLE IF NOT EXISTS lead_backlog (
  id              TEXT PRIMARY KEY,
  source          TEXT,
  source_url      TEXT,
  who             TEXT,
  problem         TEXT NOT NULL,
  mvp_scope        TEXT,
  qual_score      INTEGER,
  qual_notes      TEXT,
  status          TEXT NOT NULL DEFAULT 'new',
  built_sha       TEXT,
  built_url       TEXT,
  outreached_at   TEXT,
  converted_at    TEXT,
  added_at        TEXT NOT NULL
);
"""


class _SqliteTracer:
    def __init__(self, db_path: str | Path, events_jsonl: str | Path):
        ensure_dir(Path(db_path).parent)
        self.db_path = str(db_path)
        self.events_jsonl = Path(events_jsonl)
        ensure_dir(self.events_jsonl.parent)
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        self.conn.executescript(SQLITE_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive column migrations, so a db from an older SSSF still opens."""
        for table, column, decl in _MIGRATIONS:
            columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # ── events ──────────────────────────────────────────────────────────────
    def event(self, record: EventRecord) -> str:
        event_id = f"evt_{new_id(12)}"
        ts = now_iso()
        line = {"event_id": event_id, "ts": ts, **record.model_dump()}
        with self.events_jsonl.open("a") as f:
            f.write(json.dumps(line) + "\n")
        self.conn.execute(
            "INSERT INTO events (event_id, adw_id, phase_id, parent_id, type, name,"
            " payload_json, tokens, started_at, ended_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event_id, record.adw_id, record.phase_id, record.parent_id, record.type,
             record.name, json.dumps(record.payload), record.tokens,
             record.started_at or ts, record.ended_at),
        )
        return event_id

    # ── sessions ────────────────────────────────────────────────────────────
    def session_start(self, adw_id: str, engineer: str, adw_name: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO sessions (adw_id, status, engineer, started_at) VALUES (?,?,?,?) "
            "ON CONFLICT(adw_id) DO UPDATE SET status='running'",
            (adw_id, "running", engineer, now_iso()),
        )
        if not adw_name:
            return
        row = self.conn.execute("SELECT adw_name FROM sessions WHERE adw_id=?",
                                (adw_id,)).fetchone()
        names = row[0].split(" + ") if row and row[0] else []
        if adw_name not in names:
            names.append(adw_name)
            self.conn.execute("UPDATE sessions SET adw_name=? WHERE adw_id=?",
                              (" + ".join(names), adw_id))

    def session_request(self, adw_id: str, request: str) -> None:
        self.conn.execute("UPDATE sessions SET request=? WHERE adw_id=?",
                          (request[:500], adw_id))

    def session_finish(self, adw_id: str, ok: bool) -> None:
        self.conn.execute(
            "UPDATE sessions SET status=?, ended_at=? WHERE adw_id=?",
            ("success" if ok else "fail", now_iso(), adw_id),
        )
        self.processes_end_all(adw_id)

    def session_add_usage(self, adw_id: str, tokens: int, cost: float) -> None:
        self.conn.execute(
            "UPDATE sessions SET total_tokens=total_tokens+?, total_cost=total_cost+? WHERE adw_id=?",
            (tokens, cost, adw_id),
        )

    # ── processes ──────────────────────────────────────────────────────────
    def process_start(self, adw_id: str, kind: str, name: str, pid: int,
                      command: str) -> None:
        self.conn.execute(
            "INSERT INTO processes (adw_id, kind, name, pid, command, started_at)"
            " VALUES (?,?,?,?,?,?)",
            (adw_id, kind, name, pid, command[:500], now_iso()),
        )

    def process_end(self, adw_id: str, pid: int) -> None:
        self.conn.execute(
            "UPDATE processes SET ended_at=? WHERE id = ("
            "  SELECT id FROM processes WHERE adw_id=? AND pid=? AND ended_at IS NULL"
            "  ORDER BY id DESC LIMIT 1)",
            (now_iso(), adw_id, pid),
        )

    def processes_end_all(self, adw_id: str) -> None:
        self.conn.execute(
            "UPDATE processes SET ended_at=? WHERE adw_id=? AND ended_at IS NULL",
            (now_iso(), adw_id),
        )

    # ── phases ──────────────────────────────────────────────────────────────
    def max_phase_seq(self, adw_id: str) -> int:
        row = self.conn.execute("SELECT MAX(seq) FROM phases WHERE adw_id = ?",
                                (adw_id,)).fetchone()
        return row[0] if row and row[0] is not None else 0

    def phase_upsert(self, phase: Phase) -> None:
        p = phase.params
        self.conn.execute(
            "INSERT INTO phases (phase_id, adw_id, seq, name, kind, owner, description,"
            " status, attempt, retries, error, started_at, ended_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(phase_id) DO UPDATE SET status=excluded.status,"
            " attempt=excluded.attempt, error=excluded.error, ended_at=excluded.ended_at",
            (phase.phase_id, phase.adw_id, phase.seq, p.name, p.kind, p.owner,
             p.description, phase.status, phase.attempt, p.retries, phase.error,
             phase.started_at, phase.ended_at),
        )

    # ── envelopes / gates / agent sessions ──────────────────────────────────
    def envelope_row(self, phase: Phase, agent: str, output_type: str,
                     payload_json: str, valid: bool, attempt: int) -> None:
        self.conn.execute(
            "INSERT INTO envelopes (envelope_id, adw_id, phase_id, agent, output_type,"
            " payload_json, valid, attempt, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"env_{new_id(12)}", phase.adw_id, phase.phase_id, agent, output_type,
             payload_json, int(valid), attempt, now_iso()),
        )

    def gate_row(self, phase: Phase, gate: str, report: GateReport, attempt: int) -> None:
        self.conn.execute(
            "INSERT INTO gate_results (adw_id, phase_id, attempt, gate, passed,"
            " violations_json, checks_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (phase.adw_id, phase.phase_id, attempt, gate, int(report.passed),
             json.dumps(report.violations),
             json.dumps([c.model_dump() for c in report.checks]), now_iso()),
        )

    def agent_session_row(self, adw_id: str, agent: AgentConfig, session_id: str,
                          context_tokens: int = 0, context_window: int = 0) -> None:
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO agent_sessions (adw_id, agent, coding_agent, model, color,"
            " session_id, context_tokens, context_window, created_at, last_used_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(adw_id, agent) DO UPDATE SET model=excluded.model,"
            " color=excluded.color, session_id=excluded.session_id,"
            " context_tokens=excluded.context_tokens,"
            " context_window=excluded.context_window,"
            " last_used_at=excluded.last_used_at",
            (adw_id, agent.name, agent.coding_agent, agent.model, agent.color,
             session_id, context_tokens, context_window, ts, ts),
        )

    # ── experiment backlog (the nonstop-engine queue) ─────────────────────
    # The backlog is the shared state the operator drains (feeder) and the
    # factory fills (vault mine + reflection follow-ups). Host-side factory
    # runs go through the Tracer; the controller uses its own raw _pg queries
    # against the same table. Both honor this schema.
    def backlog_add(self, t) -> bool:
        """Insert one ExperimentThought if its id is new. Returns True on insert."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO experiment_backlog"
            " (id, thought, source, prediction, agent_under_test, tasks, n, status, added_at)"
            " VALUES (?,?,?,?,?,?,?, 'pending', ?)",
            (t.id, t.thought, t.source, t.prediction, t.agent_under_test,
             ",".join(t.tasks), t.n, now_iso()))
        return cur.rowcount > 0

    def backlog_count_running(self) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM experiment_backlog WHERE status='running'").fetchone()
        return int(row[0])

    def backlog_set_running(self, backlog_id: str, exp_adw_id: str) -> None:
        self.conn.execute(
            "UPDATE experiment_backlog SET status='running', exp_adw_id=?, started_at=? WHERE id=?",
            (exp_adw_id, now_iso(), backlog_id))

    def backlog_set_terminal(self, exp_adw_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE experiment_backlog SET status=?, finished_at=? WHERE exp_adw_id=?",
            (status, now_iso(), exp_adw_id))

    # ── lead backlog (the lead-gen pipeline queue) ───────────────────────
    def lead_add(self, lead) -> bool:
        """Insert one LeadCandidate if its id is new. Returns True on insert."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO lead_backlog"
            " (id, source, source_url, who, problem, mvp_scope, qual_score, qual_notes, status, added_at)"
            " VALUES (?,?,?,?,?,?,?,?, 'new', ?)",
            (lead.id, lead.source, lead.source_url, lead.who, lead.problem,
             lead.mvp_scope, lead.qual_score, lead.qual_notes, now_iso()))
        return cur.rowcount > 0

    def lead_count_new(self) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM lead_backlog WHERE status='new'").fetchone()
        return int(row[0])

    def lead_set_status(self, lead_id: str, status: str,
                        built_sha: str | None = None, built_url: str | None = None) -> None:
        if built_sha is not None:
            self.conn.execute(
                "UPDATE lead_backlog SET status=?, built_sha=?, built_url=? WHERE id=?",
                (status, built_sha, built_url, lead_id))
        else:
            self.conn.execute(
                "UPDATE lead_backlog SET status=? WHERE id=?", (status, lead_id))

    def lead_list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM lead_backlog WHERE status=? ORDER BY added_at DESC LIMIT ?",
                (status, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM lead_backlog ORDER BY added_at DESC LIMIT ?",
                (limit,)).fetchall()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM lead_backlog LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]


# ═════════════════════════════════════════════════════════════════════════════════
# Postgres backend (cluster/pod) — psycopg3, autocommit, self-healing schema.
# ═══════════════════════════════════════════════════════════════════════════

PG_SCHEMA = """
-- FK constraints deliberately omitted: the SQLite original declared them but
-- never enforced them (sqlite has FKs off by default), and the factory emits
-- rows with empty phase_id/adw_id by design (console/log events outside any
-- phase). Postgres would enforce them and reject those legitimate rows, so the
-- faithful port keeps the columns but drops the REFERENCES. Integrity is ordered
-- in code, as it always was.
CREATE TABLE IF NOT EXISTS sessions (
  adw_id        TEXT PRIMARY KEY,
  adw_name      TEXT,
  request       TEXT,
  status        TEXT,
  engineer      TEXT,
  started_at    TEXT, ended_at TEXT,
  total_tokens  INTEGER DEFAULT 0, total_cost DOUBLE PRECISION DEFAULT 0,
  archived      INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS phases (
  phase_id      TEXT PRIMARY KEY,
  adw_id        TEXT,
  seq           INTEGER,
  name TEXT, kind TEXT, owner TEXT, description TEXT,
  status        TEXT DEFAULT 'fail',
  attempt       INTEGER DEFAULT 0, retries INTEGER DEFAULT 0,
  error         TEXT,
  started_at    TEXT, ended_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_id      TEXT PRIMARY KEY,
  adw_id        TEXT,
  phase_id      TEXT,
  parent_id     TEXT,
  type          TEXT,
  name          TEXT,
  payload_json  TEXT,
  tokens        INTEGER,
  started_at    TEXT, ended_at TEXT
);
CREATE TABLE IF NOT EXISTS envelopes (
  envelope_id   TEXT PRIMARY KEY,
  adw_id        TEXT,
  phase_id      TEXT,
  agent         TEXT,
  output_type   TEXT,
  payload_json  TEXT,
  valid         INTEGER,
  attempt       INTEGER,
  created_at    TEXT
);
CREATE TABLE IF NOT EXISTS gate_results (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  adw_id        TEXT,
  phase_id      TEXT,
  attempt       INTEGER,
  gate          TEXT,
  passed        INTEGER,
  violations_json TEXT,
  checks_json   TEXT,
  created_at    TEXT
);
CREATE TABLE IF NOT EXISTS processes (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  adw_id        TEXT,
  kind          TEXT,
  name          TEXT,
  pid           BIGINT,
  command       TEXT,
  started_at    TEXT, ended_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_sessions (
  adw_id        TEXT,
  agent         TEXT,
  coding_agent  TEXT, model TEXT, color TEXT,
  session_id    TEXT,
  context_tokens INTEGER,
  context_window INTEGER,
  created_at    TEXT, last_used_at TEXT,
  PRIMARY KEY (adw_id, agent)
);
CREATE TABLE IF NOT EXISTS experiment_backlog (
  id              TEXT PRIMARY KEY,
  thought         TEXT NOT NULL,
  source          TEXT,
  prediction      TEXT,
  agent_under_test TEXT,
  tasks           TEXT,
  n               INTEGER,
  status          TEXT NOT NULL DEFAULT 'pending',
  exp_adw_id      TEXT,
  added_at        TEXT NOT NULL,
  started_at      TEXT,
  finished_at     TEXT
);
CREATE TABLE IF NOT EXISTS lead_backlog (
  id              TEXT PRIMARY KEY,
  source          TEXT,
  source_url      TEXT,
  who             TEXT,
  problem         TEXT NOT NULL,
  mvp_scope        TEXT,
  qual_score      INTEGER,
  qual_notes      TEXT,
  status          TEXT NOT NULL DEFAULT 'new',
  built_sha       TEXT,
  built_url       TEXT,
  outreached_at   TEXT,
  converted_at    TEXT,
  added_at        TEXT NOT NULL
);
"""


class _PgTracer:
    """Postgres trace mirror. psycopg3, autocommit, schema self-heals on connect.

    `%s` placeholders (psycopg3 style). JSON stored as TEXT via json.dumps so the
    same call sites and the visualizer's string-parse path work in both modes.
    A concurrent pod run gets its own adw_id, so there is no cross-pod row
    collision; Postgres MVCC handles the row-level writes atomically — exactly
    the concurrency fix that motivated the migration. FK constraints are
    intentionally omitted (see PG_SCHEMA) to match the SQLite original's
    non-enforcing behaviour, so legitimate no-phase events (phase_id='') land.
    """

    def __init__(self, database_url: str, events_jsonl: str | Path):
        import psycopg  # lazy: SQLite-only hosts never need psycopg installed
        self.database_url = database_url
        self.events_jsonl = Path(events_jsonl)
        ensure_dir(self.events_jsonl.parent)
        self.conn = psycopg.connect(database_url, autocommit=True)
        self.conn.execute(PG_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive column migrations, mirroring the SQLite path via
        information_schema instead of PRAGMA table_info."""
        for table, column, decl in _MIGRATIONS:
            exists = self.conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, column),
            ).fetchone()
            if not exists:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # ── events ──────────────────────────────────────────────────────────────
    def event(self, record: EventRecord) -> str:
        event_id = f"evt_{new_id(12)}"
        ts = now_iso()
        line = {"event_id": event_id, "ts": ts, **record.model_dump()}
        with self.events_jsonl.open("a") as f:
            f.write(json.dumps(line) + "\n")
        self.conn.execute(
            "INSERT INTO events (event_id, adw_id, phase_id, parent_id, type, name,"
            " payload_json, tokens, started_at, ended_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (event_id, record.adw_id, record.phase_id, record.parent_id, record.type,
             record.name, json.dumps(record.payload), record.tokens,
             record.started_at or ts, record.ended_at),
        )
        return event_id

    # ── sessions ────────────────────────────────────────────────────────────
    def session_start(self, adw_id: str, engineer: str, adw_name: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO sessions (adw_id, status, engineer, started_at) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT(adw_id) DO UPDATE SET status='running'",
            (adw_id, "running", engineer, now_iso()),
        )
        if not adw_name:
            return
        row = self.conn.execute("SELECT adw_name FROM sessions WHERE adw_id=%s",
                                 (adw_id,)).fetchone()
        names = row[0].split(" + ") if row and row[0] else []
        if adw_name not in names:
            names.append(adw_name)
            self.conn.execute("UPDATE sessions SET adw_name=%s WHERE adw_id=%s",
                              (" + ".join(names), adw_id))

    def session_request(self, adw_id: str, request: str) -> None:
        self.conn.execute("UPDATE sessions SET request=%s WHERE adw_id=%s",
                          (request[:500], adw_id))

    def session_finish(self, adw_id: str, ok: bool) -> None:
        self.conn.execute(
            "UPDATE sessions SET status=%s, ended_at=%s WHERE adw_id=%s",
            ("success" if ok else "fail", now_iso(), adw_id),
        )
        self.processes_end_all(adw_id)

    def session_add_usage(self, adw_id: str, tokens: int, cost: float) -> None:
        self.conn.execute(
            "UPDATE sessions SET total_tokens=total_tokens+%s, total_cost=total_cost+%s WHERE adw_id=%s",
            (tokens, cost, adw_id),
        )

    # ── processes ──────────────────────────────────────────────────────────
    def process_start(self, adw_id: str, kind: str, name: str, pid: int,
                      command: str) -> None:
        self.conn.execute(
            "INSERT INTO processes (adw_id, kind, name, pid, command, started_at)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (adw_id, kind, name, pid, command[:500], now_iso()),
        )

    def process_end(self, adw_id: str, pid: int) -> None:
        self.conn.execute(
            "UPDATE processes SET ended_at=%s WHERE id = ("
            "  SELECT id FROM processes WHERE adw_id=%s AND pid=%s AND ended_at IS NULL"
            "  ORDER BY id DESC LIMIT 1)",
            (now_iso(), adw_id, pid),
        )

    def processes_end_all(self, adw_id: str) -> None:
        self.conn.execute(
            "UPDATE processes SET ended_at=%s WHERE adw_id=%s AND ended_at IS NULL",
            (now_iso(), adw_id),
        )

    # ── phases ──────────────────────────────────────────────────────────────
    def max_phase_seq(self, adw_id: str) -> int:
        row = self.conn.execute("SELECT MAX(seq) FROM phases WHERE adw_id = %s",
                                (adw_id,)).fetchone()
        return row[0] if row and row[0] is not None else 0

    def phase_upsert(self, phase: Phase) -> None:
        p = phase.params
        self.conn.execute(
            "INSERT INTO phases (phase_id, adw_id, seq, name, kind, owner, description,"
            " status, attempt, retries, error, started_at, ended_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT(phase_id) DO UPDATE SET status=EXCLUDED.status,"
            " attempt=EXCLUDED.attempt, error=EXCLUDED.error, ended_at=EXCLUDED.ended_at",
            (phase.phase_id, phase.adw_id, phase.seq, p.name, p.kind, p.owner,
             p.description, phase.status, phase.attempt, p.retries, phase.error,
             phase.started_at, phase.ended_at),
        )

    # ── envelopes / gates / agent sessions ──────────────────────────────────
    def envelope_row(self, phase: Phase, agent: str, output_type: str,
                     payload_json: str, valid: bool, attempt: int) -> None:
        self.conn.execute(
            "INSERT INTO envelopes (envelope_id, adw_id, phase_id, agent, output_type,"
            " payload_json, valid, attempt, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"env_{new_id(12)}", phase.adw_id, phase.phase_id, agent, output_type,
             payload_json, int(valid), attempt, now_iso()),
        )

    def gate_row(self, phase: Phase, gate: str, report: GateReport, attempt: int) -> None:
        self.conn.execute(
            "INSERT INTO gate_results (adw_id, phase_id, attempt, gate, passed,"
            " violations_json, checks_json, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (phase.adw_id, phase.phase_id, attempt, gate, int(report.passed),
             json.dumps(report.violations),
             json.dumps([c.model_dump() for c in report.checks]), now_iso()),
        )

    def agent_session_row(self, adw_id: str, agent: AgentConfig, session_id: str,
                          context_tokens: int = 0, context_window: int = 0) -> None:
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO agent_sessions (adw_id, agent, coding_agent, model, color,"
            " session_id, context_tokens, context_window, created_at, last_used_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT(adw_id, agent) DO UPDATE SET model=EXCLUDED.model,"
            " color=EXCLUDED.color, session_id=EXCLUDED.session_id,"
            " context_tokens=EXCLUDED.context_tokens,"
            " context_window=EXCLUDED.context_window,"
            " last_used_at=EXCLUDED.last_used_at",
            (adw_id, agent.name, agent.coding_agent, agent.model, agent.color,
             session_id, context_tokens, context_window, ts, ts),
        )

    # ── experiment backlog (the nonstop-engine queue) ─────────────────────
    def backlog_add(self, t) -> bool:
        """Insert one ExperimentThought if its id is new. Returns True on insert."""
        cur = self.conn.execute(
            "INSERT INTO experiment_backlog"
            " (id, thought, source, prediction, agent_under_test, tasks, n, status, added_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',%s)"
            " ON CONFLICT (id) DO NOTHING",
            (t.id, t.thought, t.source, t.prediction, t.agent_under_test,
             ",".join(t.tasks), t.n, now_iso()))
        return cur.rowcount > 0

    def backlog_count_running(self) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM experiment_backlog WHERE status='running'").fetchone()
        return int(row[0])

    def backlog_set_running(self, backlog_id: str, exp_adw_id: str) -> None:
        self.conn.execute(
            "UPDATE experiment_backlog SET status='running', exp_adw_id=%s, started_at=%s WHERE id=%s",
            (exp_adw_id, now_iso(), backlog_id))

    def backlog_set_terminal(self, exp_adw_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE experiment_backlog SET status=%s, finished_at=%s WHERE exp_adw_id=%s",
            (status, now_iso(), exp_adw_id))

    # -- lead backlog (the lead-gen pipeline queue) ----------------------
    def lead_add(self, lead) -> bool:
        """Insert one LeadCandidate if its id is new. Returns True on insert."""
        cur = self.conn.execute(
            "INSERT INTO lead_backlog"
            " (id, source, source_url, who, problem, mvp_scope, qual_score, qual_notes, status, added_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'new',%s)"
            " ON CONFLICT (id) DO NOTHING",
            (lead.id, lead.source, lead.source_url, lead.who, lead.problem,
             lead.mvp_scope, lead.qual_score, lead.qual_notes, now_iso()))
        return cur.rowcount > 0

    def lead_count_new(self) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM lead_backlog WHERE status='new'").fetchone()
        return int(row[0])

    def lead_set_status(self, lead_id: str, status: str,
                        built_sha=None, built_url=None) -> None:
        if built_sha is not None:
            self.conn.execute(
                "UPDATE lead_backlog SET status=%s, built_sha=%s, built_url=%s WHERE id=%s",
                (status, built_sha, built_url, lead_id))
        else:
            self.conn.execute(
                "UPDATE lead_backlog SET status=%s WHERE id=%s", (status, lead_id))

    def lead_list(self, status=None, limit=50) -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM lead_backlog WHERE status=%s ORDER BY added_at DESC LIMIT %s",
                (status, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM lead_backlog ORDER BY added_at DESC LIMIT %s",
                (limit,)).fetchall()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM lead_backlog LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# Factory: pick the backend. DATABASE_URL (or a postgres:// db arg) → Postgres;
# otherwise SQLite at the configured path. Callers pass `cfg.observability.db`.
# ═══════════════════════════════════════════════════════════════════════════

def _looks_like_pg(s: str) -> bool:
    return s.startswith(("postgres://", "postgresql://"))


def Tracer(db_path: str | Path, events_jsonl: str | Path):
    """Construct the trace backend.

    Selection order:
      1. `DATABASE_URL` env var set → Postgres (psycopg3) at that URL.
      2. `db_path` is a postgres:// URL → Postgres at that URL.
      3. otherwise → SQLite at `db_path` (host/dev default, no deps beyond stdlib).
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return _PgTracer(url, events_jsonl)
    if _looks_like_pg(str(db_path)):
        return _PgTracer(str(db_path), events_jsonl)
    return _SqliteTracer(db_path, events_jsonl)