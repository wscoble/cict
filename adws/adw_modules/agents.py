"""Config loading/validation and agent execution.

Every ADW validates its agents before running (fail fast, nothing spawns
against a half-valid config). Every agent call parses against a concrete
output type; parse failures and gate violations re-prompt the SAME session
with a correction — context intact, bounded retries. Agent proposes, code
disposes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml

from . import agent_pi, permissions, prompts
from .data_types import (AgentCall, AgentConfig, EnvelopeBase, EventRecord,
                         GateCheck, GateReport, Phase, PiRequest, ReviewEscalationDelta,
                         SSSFConfig, UsageBreakdown)
from .utils import new_id

JSON_FIX_ATTEMPTS = 2      # continue-with-correction attempts for malformed JSON


class GateFailure(RuntimeError):
    pass


# ── config ───────────────────────────────────────────────────────────────────

def load_config(path: str = "adws/adw_sssf_config/sssf.config.yaml") -> SSSFConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    defaults = raw.get("defaults", {}) or {}
    for agent in raw.get("agents", []) or []:
        for key in ("coding_agent", "model", "thinking", "color", "tools", "writes"):
            if key in defaults:
                agent.setdefault(key, defaults[key])
        agent.setdefault("harness_engineering", defaults.get("harness_engineering", []))
    return SSSFConfig(**raw)


def resolve(cfg: SSSFConfig, name: str) -> AgentConfig:
    for agent in cfg.agents:
        if agent.name == name:
            return agent
    raise SystemExit(f"agent {name!r} is not defined in the config — "
                     f"available: {[a.name for a in cfg.agents]}")


def validate(cfg: SSSFConfig, required: list[str]) -> None:
    """Fail fast: every required name must resolve to a usable agent."""
    problems = []
    for name in required:
        try:
            agent = resolve(cfg, name)
        except SystemExit as e:
            problems.append(str(e))
            continue
        if agent.coding_agent != "pi":
            problems.append(f"agent {name!r}: coding_agent {agent.coding_agent!r} "
                            f"is not implemented in v1 (pi only)")
        for label, ref in (("system", agent.prompt_engineering.system),
                           ("user", agent.prompt_engineering.user)):
            if not Path(ref).is_file():
                problems.append(f"agent {name!r}: {label} prompt not found: {ref}")
        try:
            agent_pi.resolve_model(agent.model)
        except ValueError as e:
            problems.append(f"agent {name!r}: {e}")
        if agent.escalate_to:
            try:
                agent_pi.resolve_model(agent.escalate_to)
            except ValueError as e:
                problems.append(f"agent {name!r}: escalate_to {e}")
    if problems:
        raise SystemExit("config validation failed:\n- " + "\n- ".join(problems))


# ── execution ────────────────────────────────────────────────────────────────

def execute(run, phase: Phase, call: AgentCall, *,
            model_override: Optional[str] = None,
            thinking_override: Optional[str] = None,
            signals: Optional[dict] = None) -> EnvelopeBase:
    """One agent call: render prompts -> pi run -> typed parse -> gates -> envelope.

    `model_override`/`thinking_override` re-run the SAME agent on a different
    model — used by execute_with_escalation to take a second swing on cloud when
    the local run struggled. `signals` collects the parse/gate attempt counts so
    the caller can decide whether to escalate.
    """
    agent = resolve(run.cfg, phase.params.owner)
    model = model_override or agent.model
    thinking = thinking_override or agent.thinking
    agent_dir = run.session_dir / agent.name
    agent_dir.mkdir(parents=True, exist_ok=True)

    variables = {
        "prompt": call.prompt,
        "previous_envelope": call.previous.model_dump_json(indent=2) if call.previous else "(none)",
        "context_handoff_dir": str(run.context_handoff_dir),
        "adw_id": run.adw_id,
    }
    system_text = prompts.render(agent.prompt_engineering.system, variables)
    user_text = prompts.render(agent.prompt_engineering.user, variables)
    prompts.save(agent_dir / "prompts", "system.md", system_text)
    prompts.save(agent_dir / "prompts", "user.md", user_text)

    session_id = _agent_session_id(run, agent, model)
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                 type="agent_start", name=agent.name,
                                 payload={"model": model, "thinking": thinking,
                                          "color": agent.color,
                                          "session_id": session_id,
                                          "coding_agent": agent.coding_agent,
                                          "purpose": agent.purpose,
                                          "tools": agent.tools,  # None = all tools
                                          "harness_engineering": agent.harness_engineering}))
    run.console.agent_started(agent.name, model, session_id)

    # Parse retries and gate corrections re-enter the SAME pi session, so the
    # last send is the one whose context occupancy is current — while spend is
    # the opposite: every send costs, so usage accumulates across all of them.
    latest: agent_pi.PiResult | None = None
    spent = UsageBreakdown()

    def send(prompt_text: str) -> agent_pi.PiResult:
        nonlocal latest
        request = PiRequest(
            prompt=prompt_text,
            system_prompt=system_text,
            model=model,
            thinking=thinking,
            session_id=session_id,
            # absolute: these are read by the pi subprocess, which runs in repo_root
            session_dir=str((agent_dir / "pi_sessions").resolve()),
            raw_output_path=str((agent_dir / "raw_output.jsonl").resolve()),
            tools=agent.tools,
            extensions=agent.harness_engineering,
            cwd=str(run.repo_root),
        )
        result = agent_pi.run(
            request,
            on_event=_event_forwarder(run, phase, agent.name),
            on_spawn=lambda pid: run.tracer.process_start(
                run.adw_id, "agent", agent.name, pid,
                f"{agent.coding_agent} {agent.name} {model}"),
            on_exit=lambda pid: run.tracer.process_end(run.adw_id, pid))
        run.add_usage(result.tokens, result.cost)
        spent.merge(result.usage)
        latest = result
        return result

    # What the tree looked like before this agent got its hands on it. Every
    # send in this phase — first prompt, JSON retries, gate corrections — is
    # measured against this one baseline.
    tree_before = permissions.snapshot(run)

    result = send(user_text)
    envelope, attempt = _parse_with_retries(run, phase, call, result, send)

    # claim gates — violations flow back into the SAME session as corrections
    for gate_attempt in range(1, max(1, phase.params.retries + 1) + 1):
        violations = []
        for gate in call.gates:
            report = _as_report(gate(envelope, run))
            found = report.violations
            run.tracer.gate_row(phase, gate.__name__, report, gate_attempt)
            run.tracer.event(EventRecord(
                adw_id=run.adw_id, phase_id=phase.phase_id,
                type="gate_fail" if found else "gate_pass", name=gate.__name__,
                payload={"attempt": gate_attempt, "violations": found,
                         "checks": [c.model_dump() for c in report.checks]}))
            run.console.gate_result(gate.__name__, report)
            violations.extend(found)
        if not violations:
            break
        if gate_attempt > phase.params.retries:
            raise GateFailure(f"{agent.name} failed gates after {gate_attempt} attempt(s):\n- "
                              + "\n- ".join(violations))
        phase.attempt = gate_attempt
        run.console.retry(agent.name, gate_attempt, phase.params.retries,
                          f"{len(violations)} gate violation(s)")
        correction = ("Your previous response failed validation:\n- "
                      + "\n- ".join(violations)
                      + "\n\nFix these problems, then re-emit ONLY your Report JSON.")
        result = send(correction)
        envelope, attempt = _parse_with_retries(run, phase, call, result, send)

    # Permission is checked after every send is done, and before the envelope is
    # accepted: an agent does not get to report success on a phase in which it
    # wrote somewhere it was not allowed to.
    try:
        touched = permissions.enforce(run, phase, agent, tree_before)
    except permissions.PermissionBreach as breach:
        run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                     type="error", name="permission_breach",
                                     payload={"agent": agent.name, "error": str(breach),
                                              "writes": agent.writes,
                                              "protected_files": run.cfg.defaults.protected_files}))
        raise
    if touched:
        run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                     type="log", name="paths_touched",
                                     payload={"agent": agent.name, "paths": touched}))

    _persist_envelope(run, phase, agent.name, call, envelope, attempt, valid=True)
    run.console.envelope_summary(envelope)
    context = latest or result
    run.tracer.agent_session_row(run.adw_id, agent, session_id,
                                 context_tokens=context.context_tokens,
                                 context_window=context.context_window)
    run.save_agent_map(agent.name, {"session_id": session_id, "model": model,
                                    "coding_agent": agent.coding_agent})
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                 type="handoff", name=agent.name,
                                 payload={"artifacts": envelope.artifacts,
                                          "summary": envelope.summary}))
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                 type="agent_end", name=agent.name,
                                 # Phase totals, not the last send's: a retried
                                 # phase paid for every attempt.
                                 tokens=spent.total_tokens,
                                 payload={"cost": spent.total_cost,
                                          "usage": spent.model_dump(),
                                          "context_tokens": context.context_tokens,
                                          "context_window": context.context_window}))
    run.console.agent_finished(agent.name, spent.total_tokens, spent.total_cost)
    if signals is not None:
        signals["parse_attempt"] = attempt
        signals["gate_attempts"] = gate_attempt
    if envelope.status != "success":
        raise RuntimeError(f"{agent.name} reported status={envelope.status!r}: {envelope.summary}")
    return envelope


# ── escalation ───────────────────────────────────────────────────────────────

def execute_with_escalation(run, phase: Phase, call: AgentCall) -> EnvelopeBase:
    """Run the agent; if a trigger fires, re-run the SAME agent on escalate_to.

    Local-primary + cloud-escalate: the cheap layer runs first; cloud tokens are
    spent only when the local run struggled (parse/gate retry), came back shallow
    (a field below threshold), or rejected the work. One level only — the
    escalated run calls execute() directly, so it never escalates again.

    For a cloud-primary agent (e.g. the builder), `on_fail` flips this into a
    fallback: if the primary raises, re-run on escalate_to instead of aborting.
    """
    agent = resolve(run.cfg, phase.params.owner)
    triggers = [t for t in (agent.escalate_when or []) if t and t != "never"]
    if not agent.escalate_to or not triggers:
        return execute(run, phase, call)

    signals: dict = {}
    try:
        envelope = execute(run, phase, call, signals=signals)
    except Exception as exc:
        # on_fail: any failure -> fall back to the escalate_to model.
        # parse_retry / gate_retry: a TOTAL failure (all retries exhausted,
        # execute() raises) is the strongest form of parse/gate trouble - the
        # local run never recovered. Treat it the same as on_fail and let cloud
        # rescue it, instead of letting the whole phase die. Without this, an
        # agent with only parse_retry would re-raise here and the workflow aborts
        # even though a cloud model was configured to catch exactly this.
        rescue = [t for t in triggers
                  if t in ("on_fail", "parse_retry", "gate_retry", "always")]
        if rescue:
            trig = "on_fail" if "on_fail" in rescue else rescue[0]
            _log_escalation(run, phase, agent, agent.escalate_to,
                            trigger=trig,
                            reason=f"primary raised ({type(exc).__name__}); falling back")
            return execute(run, phase, call,
                           model_override=agent.escalate_to,
                           thinking_override=agent.escalate_thinking)
        raise

    fired = [t for t in triggers
             if t != "on_fail" and _evaluate_escalation(t, envelope, signals)]
    if not fired:
        return envelope
    # Global signals (parse_retry/gate_retry/always) mean the whole output is
    # untrustworthy -> re-run the agent on cloud. Specific-part signals
    # (field_lt/field_eq) mean only pieces are uncertain -> surgical: carve out
    # the ambiguous parts, route each to the right model, merge the delta back.
    if any(t in _GLOBAL_TRIGGERS for t in fired):
        _log_escalation(run, phase, agent, agent.escalate_to,
                        trigger=",".join(fired), reason="global signal; whole-agent re-run")
        return execute(run, phase, call,
                       model_override=agent.escalate_to,
                       thinking_override=agent.escalate_thinking)
    return surgical_escalate(run, phase, agent, envelope, call, signals, fired)


def _log_escalation(run, phase, agent, to_model, trigger, reason):
    run.tracer.event(EventRecord(
        adw_id=run.adw_id, phase_id=phase.phase_id,
        type="escalation", name=agent.name,
        payload={"from": agent.model, "to": to_model, "trigger": trigger,
                 "reason": reason}))
    run.console.note(f"↻ escalate {agent.name}: {agent.model} → {to_model} ({trigger})")


def _evaluate_escalation(spec: str, envelope, signals: dict) -> bool:
    """One trigger spec against the accepted envelope + attempt counts. OR'd by the caller."""
    parse_attempt = signals.get("parse_attempt", 1)
    gate_attempts = signals.get("gate_attempts", 1)
    if spec == "always":
        return True
    if spec == "parse_retry":
        return parse_attempt > 1
    if spec == "gate_retry":
        return gate_attempts > 1
    if spec.startswith("field_lt:"):
        _, field, n = spec.split(":", 2)
        val = getattr(envelope, field, None)
        if val is None:
            return False
        try:
            nval = int(n)
        except ValueError:
            return False
        if isinstance(val, bool):
            return False            # bools have no meaningful < ; use field_eq
        if isinstance(val, (list, str)):
            return len(val) < nval
        if isinstance(val, (int, float)):
            return val < nval
        return False
    if spec.startswith("field_eq:"):
        _, field, raw = spec.split(":", 2)
        val = getattr(envelope, field, None)
        if isinstance(val, bool):
            return val == (raw.strip().lower() == "true")
        return str(val) == raw.strip()
    return False


# ── surgical escalation ──────────────────────────────────────────────────────

_GLOBAL_TRIGGERS = {"parse_retry", "gate_retry", "always"}


def surgical_escalate(run, phase, agent, envelope, call, signals, triggers):
    """Route specific-part triggers to a focused cloud call per ambiguity class.

    Unlike whole-agent re-escalation, this keeps the local envelope and amends it
    with a focused cloud fill for only the ambiguous parts. If the cloud chain is
    unavailable, the local envelope stands (graceful degradation)."""
    cls = agent.escalate_class
    target = run.cfg.escalation.targets.get(cls) if cls else None
    if not target:
        _log_escalation(run, phase, agent, "(no target)", ",".join(triggers),
                        f"no escalation.targets for class {cls!r}; keeping local result")
        return envelope
    if cls == "verification":
        return _surgical_reviewer(run, phase, agent, envelope, call, target, triggers)
    # classes without a surgical impl yet fall back to a whole-agent cloud re-run
    _log_escalation(run, phase, agent, target.primary, ",".join(triggers),
                    f"no surgical impl for class {cls!r}; whole-agent re-run")
    return execute(run, phase, call, model_override=target.primary,
                   thinking_override=target.thinking)


def focused_call(run, phase, agent_name, target, system_prompt, user_prompt,
                 delta_type, raw_stem):
    """One focused cloud question, walking the fallback chain on detectable failure.

    Read-only (read/grep/find/ls only), no permission gate, no repo writes — the
    focused prompt carries the ambiguous part + minimal context inline. Returns
    (delta, model_used, usage). Raises only if the whole chain is exhausted."""
    chain = [target.primary] + list(target.fallback)
    spent = UsageBreakdown()
    agent_dir = run.session_dir / agent_name / "escalation"
    agent_dir.mkdir(parents=True, exist_ok=True)
    last_error = None
    for idx, model in enumerate(chain):
        session_id = f"sssf-{run.adw_id}-{agent_name}-escal-{new_id(4)}"
        request = PiRequest(
            prompt=user_prompt, system_prompt=system_prompt, model=model,
            thinking=target.thinking, session_id=session_id,
            session_dir=str((agent_dir / "pi_sessions").resolve()),
            raw_output_path=str((agent_dir / f"{raw_stem}-{idx}.jsonl").resolve()),
            tools=["read", "grep", "find", "ls"],   # read-only — a focused call never mutates
            extensions=[], cwd=str(run.repo_root),
        )
        try:
            result = agent_pi.run(request, on_event=_event_forwarder(run, phase, agent_name))
        except Exception as e:                     # pi failed to launch/run -> next in chain
            last_error = f"{model}: {e}"; continue
        run.add_usage(result.tokens, result.cost); spent.merge(result.usage)
        if not result.text or result.tokens == 0 or result.returncode != 0:
            last_error = f"{model}: empty/0-token response (retired or errored)"; continue
        try:
            delta = delta_type.model_validate(_extract_json(result.text))
        except Exception as e:
            last_error = f"{model}: unparseable {delta_type.__name__} ({e})"; continue
        run.tracer.event(EventRecord(
            adw_id=run.adw_id, phase_id=phase.phase_id, type="escalation",
            name=agent_name, tokens=result.tokens,
            payload={"focused": True, "to": model, "chain_idx": idx, "ok": True}))
        run.console.note(f"↻ focused escalate {agent_name} → {model}: {result.tokens} tok")
        return delta, model, spent
    run.tracer.event(EventRecord(
        adw_id=run.adw_id, phase_id=phase.phase_id, type="escalation", name=agent_name,
        payload={"focused": True, "ok": False, "error": str(last_error),
                 "chain": chain}))
    raise RuntimeError(f"focused escalation exhausted chain {chain}: {last_error}")


def _surgical_reviewer(run, phase, agent, envelope, call, target, triggers):
    """Fill a shallow/rejected first-pass review: a stronger model checks the
    requirements the local review did NOT, and gives the final verdict over all
    findings. The local envelope is amended in place — local work survives."""
    build = call.previous
    plan_text = _read_plan_text(run)
    existing = json.dumps([f.model_dump() for f in envelope.findings], indent=2)
    changed = (", ".join(build.changed_files)
               if (build and getattr(build, "changed_files", None)) else "(unknown)")
    build_summary = getattr(build, "summary", "") if build else ""
    system = (
        "You are a senior code reviewer filling the gaps a first-pass review missed. "
        "The first pass was by a smaller model and may have checked too few requirements. "
        "Read the changed files as needed. Then: (1) identify requirements in the plan the "
        "first-pass review did NOT check; (2) for each, judge whether the build meets it, "
        "with concrete evidence; (3) give your final approve/reject verdict over ALL findings "
        "(existing + yours). Emit ONLY a JSON object with these fields: "
        '{"additional_findings":[{"requirement":str,"met":bool,"evidence":str}],'
        '"additional_blocking":[str],"approved":bool,"rationale":str}. No prose, no code fences.')
    user = (f"## Plan\n{plan_text}\n\n## Changed files in the build\n{changed}\n\n"
            f"## Build summary\n{build_summary}\n\n"
            f"## Existing first-pass findings\n{existing}\n\n"
            f"## Triggers for this escalation\n{', '.join(triggers)}\n\n"
            "## Your task\nIdentify the unchecked requirements, judge each, and verdict.")
    try:
        delta, model, spent = focused_call(run, phase, agent.name, target,
                                           system, user, ReviewEscalationDelta, "review")
    except Exception as e:
        run.console.note(f"↻ surgical reviewer: cloud chain unavailable ({e}); keeping local review")
        return envelope
    envelope.findings = list(envelope.findings) + list(delta.additional_findings)
    envelope.blocking = list(envelope.blocking) + list(delta.additional_blocking)
    if delta.additional_findings or delta.rationale:
        envelope.approved = delta.approved
    if delta.rationale:
        envelope.summary = f"{envelope.summary} | escalation ({model}): {delta.rationale}".strip(" |")
    _persist_envelope(run, phase, agent.name, call, envelope, 1, valid=True)
    run.console.note(f"↻ surgical reviewer → {model}: +{len(delta.additional_findings)} findings, "
                     f"approved={envelope.approved} (+{spent.total_tokens} tok)")
    return envelope


def _read_plan_text(run) -> str:
    """The plan body from the planner's persisted envelope artifact, for the focused
    reviewer call. Capped — the focused prompt carries excerpts, not the whole repo."""
    try:
        pe = json.loads((run.session_dir / "planner" / "envelope.json").read_text())
        arts = pe.get("artifacts") or []
        if arts:
            return Path(arts[0]).read_text()[:8000]
    except Exception:
        pass
    return "(plan not available)"


# ── internals ────────────────────────────────────────────────────────────────

def _as_report(result) -> GateReport:
    """Accept a GateReport, or a legacy gate that returned a violations list."""
    if isinstance(result, GateReport):
        return result
    return GateReport(checks=[GateCheck(item=str(v), ok=False) for v in (result or [])])


def _agent_session_id(run, agent: AgentConfig, model: str) -> str:
    entry = run.agent_map.get(agent.name)
    if entry and entry.get("model") == model:
        return entry["session_id"]           # rejoin the existing context window
    return f"sssf-{run.adw_id}-{agent.name}-{new_id(4)}"


def _event_forwarder(run, phase: Phase, agent_name: str):
    """One tool_call event per real tool call, with its exact args and result."""
    tracker = agent_pi.ToolCallTracker()

    def forward(event: dict) -> None:
        record = tracker.observe(event)
        if record is None:
            return
        # The call's span rides the columns; duration_ms stays in the payload as
        # pi's own authoritative number.
        run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                     type="tool_call", name=record.pop("label"),
                                     started_at=record.pop("started_at", None),
                                     ended_at=record.pop("ended_at", None),
                                     payload={**record, "agent": agent_name}))
    return forward


def _extract_json(text: str) -> dict:
    candidate = text
    if "```" in text:
        for block in text.split("```")[1::2]:
            block = block.removeprefix("json").strip()
            if block.startswith("{"):
                candidate = block
                break
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the response")
    return json.loads(candidate[start:end + 1])


def _parse_with_retries(run, phase: Phase, call: AgentCall, result, send):
    """Parse the final response against the declared output type; on failure,
    continue the SAME session with a correction (bounded)."""
    for attempt in range(1, JSON_FIX_ATTEMPTS + 2):
        try:
            payload = _extract_json(result.text)
            return call.output_type.model_validate(payload), attempt
        except Exception as error:
            _persist_envelope(run, phase, phase.params.owner, call, None, attempt,
                              valid=False, raw=result.text)
            if attempt > JSON_FIX_ATTEMPTS:
                raise RuntimeError(
                    f"{phase.params.owner} never produced valid "
                    f"{call.output_type.__name__} JSON: {error}") from error
            run.console.retry(phase.params.owner, attempt, JSON_FIX_ATTEMPTS,
                              f"invalid {call.output_type.__name__} JSON: {error}")
            fields = ", ".join(call.output_type.model_fields.keys())
            result = send(
                f"Your response was not valid JSON for the required structure "
                f"({error}). Respond again with ONLY a JSON object with these "
                f"fields: {fields}. No prose, no code fences.")


def _persist_envelope(run, phase: Phase, agent_name: str, call: AgentCall,
                      envelope: Optional[EnvelopeBase], attempt: int,
                      valid: bool, raw: str = "") -> None:
    payload_json = envelope.model_dump_json(indent=2) if envelope else json.dumps({"raw": raw[-2000:]})
    run.tracer.envelope_row(phase, agent_name, call.output_type.__name__,
                            payload_json, valid, attempt)
    if envelope:
        record = {"agent_name": agent_name, "purpose": resolve(run.cfg, agent_name).purpose,
                  "output_type": call.output_type.__name__, "attempt": attempt,
                  **envelope.model_dump()}
        (run.session_dir / agent_name / "envelope.json").write_text(json.dumps(record, indent=2))
