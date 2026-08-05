#!/usr/bin/env -S uv run
# /// script
# dependencies = ["psycopg[binary]"]
# ///
"""List leads from the lead_backlog, or print one lead's MVP scope.

Usage:
    uv run tools/leads.py [status]          # list leads (optional status filter)
    uv run tools/leads.py --scope <id>       # print just the mvp_scope for build-lead
"""
import os, sys

def _load_env():
    if os.path.exists(".env"):
        for line in open(".env"):
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                os.environ.setdefault("DATABASE_URL", line.split("=", 1)[1])
                break

def main():
    _load_env()
    sys.path.insert(0, "adws")
    from adw_modules.tracer import Tracer
    t = Tracer("", "")
    args = sys.argv[1:]
    if args and args[0] == "--scope" and len(args) > 1:
        rows = t.lead_list()
        r = next((x for x in rows if x["id"] == args[1]), None)
        if r:
            print(r.get("mvp_scope") or "", end="")
        sys.exit(0)
    status = args[0] if args and not args[0].startswith("-") else None
    rows = t.lead_list(status)
    if not rows:
        print(f"  (no leads{f' with status={status}' if status else ''})")
        return
    print(f"{'score':>5}  {'id':42}  {'status':10}  {'who':16}  problem")
    for r in rows:
        print(f"  {r.get('qual_score','?')}/4  {r['id']:42}  {r.get('status','?'):10}  "
              f"{(r.get('who') or '-'):16}  {(r.get('problem') or '')[:60]}")

if __name__ == "__main__":
    main()