"""Every notice Friday raises, declared once: its kind, where it lives, its key.

The census (82 emitters, notifications_census_2026-10-10) found 60 emitters that
go through `notifications_engine.push` and 22 that wrote straight to the
approval popup, the desktop bus, the chat transcript, a live voice call, the
tray balloon or SMS. This module is the one place all 82 are named, and the one
path the 22 now take. The emitters live has gained since (visible jobs, the Local
AI queue, the cloud-spill offer, the in-call voice frames) are L01-L06, and the
one-time local-voice switch offer is LV7.

An emitter declares:

* `kind`     what it is called in the activity ledger (a fixed vocabulary, never
             a caller's free text);
* `place`    where it lives: needs_you, activity, updates or receipts;
* `key`      its stable dedupe key template, built only from ids and kinds
             (no time, no title);
* `safety_critical`  spend halt, credential problem, budget alert, seat change
             and a cLaw block always reach the owner and are never held.

Receipts and traces live in the `receipts` place and are PULLED, never pushed:
`notifications_engine.push` refuses them (they go to the ledger, not the tray).

Every notice writes one activity-ledger line: kind, place, delivered or held.
Counts only, no content; the ledger stays on this PC.

The raw writers (`desktop_bus.send/push/broadcast`, a transcript insert from
background code, a voice delivery, a tray balloon) live in the helpers below
and nowhere else, apart from the navigation the owner asked for
(`tests/unit/test_notice_routing_guard.py` holds the allow-list).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

_log = logging.getLogger("friday.notices")

NEEDS_YOU, ACTIVITY, UPDATES, RECEIPTS = "needs_you", "activity", "updates", "receipts"
PLACES = (NEEDS_YOU, ACTIVITY, UPDATES, RECEIPTS)

DELIVERED, HELD = "delivered", "held"

#: Kinds that always reach the owner, in any mood of the policy. A cLaw block
#: has no emitter today; the kind is reserved so the first one is exempt by name.
SAFETY_CRITICAL_KINDS = frozenset({"spend_halt", "credential_problem", "budget_alert",
                                   "seat_change", "claw_block", "security"})

#: Kinds that are a trace or a receipt. They are pulled (from the item, from
#: Trust), never pushed as a card or a toast.
RECEIPT_KINDS = frozenset({"trace", "receipt", "router_receipt", "egress_receipt",
                           "egress_notice", "turn_receipt", "reasoning_trace",
                           "crew_receipt", "stream_trace", "seat_receipt"})

#: Kinds too general to name an emitter without a source (info, warning, error).
GENERIC_KINDS = frozenset({"info", "warning", "error", "status"})

#: Routes: how an emitter reaches the owner.
PUSH, POPUP, BUS, TRANSCRIPT, VOICE, TRAY, SMS, AMBIENT, STREAM, DERIVED = (
    "push", "popup", "bus", "transcript", "voice", "tray", "sms", "ambient", "stream",
    "derived")


@dataclass(frozen=True)
class Emitter:
    id: str
    where: str                  # file:function, as the census names it
    kind: str                   # ledger vocabulary
    place: str
    key: str                    # dedupe key template ("" = none: ambient or pulled)
    safety_critical: bool = False
    route: str = PUSH
    source: str = ""            # the push source, when the push names one
    ledger: bool = True         # False for ambient frames that are not notices
    push_kind: str = ""         # the `kind=` the push passes, when it is not `kind`
    burst: bool = False         # a burst of these within five minutes is ONE card with a count


def _e(id_, where, kind, place, key, route=PUSH, source="", safety=False, ledger=True, pk="", burst=False):
    return Emitter(id_, where, kind, place, key, bool(safety), route, source, ledger, pk, bool(burst))


_ALL = [
    # ── 3a. through notifications_engine.push (E01-E60) ──────────────────────
    _e("E01", "services/approvals.py:_notify_pending", "approval_pending", NEEDS_YOU,
       "approval:{approval_id}", source="approvals"),
    _e("E02", "services/agent.py:_report_task_completion", "task_result", UPDATES,
       "task-done:{task_id}"),
    _e("E03", "services/agent.py:_report_workflow_completion", "workflow_result", UPDATES,
       "workflow-run:{run_id}"),
    _e("E04", "services/scheduler.py:_notify_run:failed", "scheduled_failure", NEEDS_YOU,
       "sched-fail:{schedule_id}", source="scheduler"),
    _e("E05", "services/scheduler.py:_notify_run:waiting", "scheduled_wait", ACTIVITY,
       "sched-wait:{schedule_id}", source="scheduler", pk="scheduled_task", burst=True),
    _e("E06", "services/scheduler.py:_notify_run:retired", "schedule_retired", ACTIVITY,
       "sched-retired:{schedule_id}", source="scheduler"),
    _e("E07", "services/scheduler.py:_notify_run:ran", "scheduled_ran", UPDATES,
       "sched-ok:{schedule_id}", source="scheduler", pk="scheduled_task", burst=True),
    _e("E08", "services/scheduler.py:_notify_status", "scheduled_status", UPDATES,
       "sched-status:{schedule_id}", source="scheduler"),
    _e("E09", "services/scheduler.py:_announce_drain", "queue_drained", UPDATES,
       "queue-drained"),
    _e("E10", "services/scheduled_cloud.py:notify_paused", "scheduled_status", UPDATES,
       "scheduled-cloud-paused", source="scheduler"),
    _e("E11", "services/news_engine.py:_notify_front_page", "front_page", UPDATES,
       "front-page:{edition}", source="front-page"),
    _e("E12", "services/news_engine.py:_notify_weekly_digest", "weekly_digest", UPDATES,
       "weekly-digest:{week}", source="front-page"),
    _e("E13", "services/news_engine.py:_notify_weekly_editorial", "weekly_editorial", UPDATES,
       "weekly-editorial:{week}", source="front-page"),
    _e("E14", "services/news_engine.py:_notify_briefing", "briefing", UPDATES,
       "briefing:{day}", source="briefing"),
    _e("E15", "services/podcast_engine.py:_announce", "podcast_ready", UPDATES,
       "podcast:{episode_id}", source="podcasts", pk="info"),
    _e("E16", "services/creations.py:_record_pending_short_production", "creation", UPDATES,
       "daily-creation-pending:{run_id}", source="daily-creation"),
    _e("E17", "services/creations.py:generate_daily_creation", "creation", UPDATES,
       "daily-creation:{day}", source="daily-creation"),
    _e("E18", "services/creations.py:_notify_creation", "creation", UPDATES,
       "creation:{filename}", source="studio"),
    _e("E19", "services/notifications.py:_trigger_skill_promotions", "skill_improvement", UPDATES,
       "skill_promoted:{skill}", source="skillopt"),
    _e("E20", "services/notifications.py:_trigger_gmail_signals:reply", "job_response", UPDATES,
       "job_reply:{message_id}", source="gmail"),
    _e("E21", "services/notifications.py:_trigger_gmail_signals:stale", "stale_email", UPDATES,
       "stale:{message_id}", source="gmail"),
    _e("E22", "services/notifications.py:_notify_self_improvement", "self_improvement", UPDATES,
       "self-improvement:{day}", source="self-improvement"),
    _e("E23", "services/notifications.py:_flush_offline_queue", "network_log", RECEIPTS,
       "offline-flush:{day}", source="network", pk="info"),
    _e("E24", "services/notifications.py:_offline_dispatch", "offline_delivered", UPDATES,
       "offline-queue:{queue_kind}", source="offline-queue", pk="info"),
    _e("E25", "services/notifications.py:_on_network_transition", "network_offline", ACTIVITY,
       "network-offline", source="network", pk="warning"),
    _e("E26", "services/notifications.py:_compute_derived_notifications", "derived_card", UPDATES,
       "{card_id}", route=DERIVED),
    _e("E27", "services/task_journal.py:_on_write_failure", "task_unrecorded", NEEDS_YOU,
       "task_unrecorded:{task_id}", source="task-journal"),
    _e("E28", "services/task_journal.py:announce_interrupted", "tasks_interrupted", NEEDS_YOU,
       "tasks_interrupted", source="task-journal"),
    _e("E29", "services/task_resume.py:announce", "tasks_resumable", NEEDS_YOU,
       "tasks_resumable", source="task-resume"),
    _e("E30", "services/seat_transparency.py:observe_seats", "seat_change", NEEDS_YOU,
       "seat_change:{seat_state}", safety=True),
    _e("E31", "routes/chat.py:_announce_seat_notice", "seat_notice", NEEDS_YOU,
       "seat_notice:{conversation_id}", source="routing"),
    _e("E32", "services/judgment_gate.py:_notify_probe_failure", "privacy_probe_failed", NEEDS_YOU,
       "judgment-probe-failed"),
    _e("E33", "governance/behavioral_monitor.py:_notify", "security", NEEDS_YOU,
       "bmon-{session_id}", source="behavioral_monitor", safety=True),
    _e("E34", "services/budget_enforcer.py:_maybe_warn", "budget_warning", NEEDS_YOU,
       "budget_warning:{workspace}:{month}", source="budget"),
    _e("E35", "services/cost_meter.py:_push_budget_alert", "budget_alert", NEEDS_YOU,
       "budget_alert:{scope}:{band}", source="cost-meter", safety=True),
    _e("E36", "services/prompt_cache.py:_notify", "budget_advisory", ACTIVITY,
       "budget-advisory:{budget_label}:{limit}", source="task-budget"),
    _e("E37", "services/spend_guard.py:_push", "spend_halt", NEEDS_YOU,
       "spend_halt:{scope}", source="spend-guard", safety=True),
    _e("E38", "services/credential_sweep.py:_push", "credential_problem", NEEDS_YOU,
       "credential:{provider}:{state}", source="credentials", safety=True),
    _e("E39", "services/connectors.py:_notif_push", "connector_down", NEEDS_YOU,
       "connector_down:{connector}:{status}"),
    _e("E40", "services/embedder_cache.py:_notify", "model_download", ACTIVITY,
       "embedder-download:{state}", source="memory-model"),
    _e("E41", "services/file_grants.py:_push", "file_grant_problem", NEEDS_YOU,
       "file_grants:{event}:{ident}"),
    _e("E42", "phone/service.py:_notify", "phone", UPDATES, "phone:{event}", source="phone"),
    _e("E43", "services/gmail_send.py:_notify", "gmail_send", UPDATES,
       "gmail_send:{event}", source="gmail_send"),
    _e("E44", "services/mail_proposals.py:_notify", "mail_proposal", UPDATES,
       "mail:{event}", source="mail"),
    _e("E45", "services/career_ops.py:_notify", "career", UPDATES, "career:{event}",
       source="career"),
    _e("E46", "services/browser_session.py:_notify", "browser_session", UPDATES,
       "browser:{event}", source="browser"),
    _e("E47", "services/pdf_signing.py:_notify", "pdf_signing", UPDATES,
       "pdf_signing:{event}", source="pdf_signing"),
    _e("E48", "services/publisher.py:_notify", "content_publish", UPDATES,
       "content_publish:{event}", source="content"),
    _e("E49", "services/item_actions.py:_notify", "item_action", UPDATES,
       "actions:{event}", source="actions"),
    _e("E50", "services/studio_files.py:_on_decision", "file_change_failed", NEEDS_YOU,
       "studio-file-failed", source="studio_files", pk="warning"),
    _e("E51", "services/meeting_capture.py:_notify", "meeting_stopped", NEEDS_YOU,
       "meeting-stop:{meeting_id}", source="meetings", pk="meeting"),
    _e("E52", "services/memory_proposals.py:_notify_seat_refusal", "memory_seat_refusal", NEEDS_YOU,
       "memory_keeper_seat_refusal", source="memory_proposals", pk="warning"),
    _e("E53", "services/sensitivity_classifier.py:_record_layer3_hold", "privacy_hold", UPDATES,
       "layer3-hold", source="privacy", pk="warning"),
    _e("E54", "services/avatar_growth.py:_notify", "avatar_growth", UPDATES,
       "avatar:{event}", source="avatar"),
    _e("E55", "services/relationship_memory.py:_push", "relationship", UPDATES,
       "relationship:{event}:{contact}"),
    _e("E56", "services/update_check.py:_announce", "update_available", UPDATES,
       "update-available-{version}"),
    _e("E57", "server.py:_enforce_blueprint_policy", "blueprint_offline", ACTIVITY,
       "blueprint-offline:{modules}", source="startup", pk="degradation"),
    _e("E58", "services/research/deliver.py:announce", "research_done", UPDATES,
       "research-done:{run_id}"),
    _e("E59", "services/research/deliver.py:announce_failure", "research_failed", UPDATES,
       "research-failed:{run_id}"),
    _e("E60", "routes/notifications.py:push_notification_endpoint", "external", UPDATES,
       "external:{kind}:{source}"),
    # ── 3b. the 22 that bypassed the policy (E61-E82) ────────────────────────
    _e("E61", "services/approvals.py:_feed", "approval_pending", NEEDS_YOU,
       "approval:{approval_id}", route=POPUP),
    _e("E62", "services/local_context.py:approval_feed.card_pending", "approval_pending",
       NEEDS_YOU, "approval:{approval_id}", route=POPUP),
    _e("E63", "services/presence.py:emit", "presence", ACTIVITY, "", route=AMBIENT, ledger=False),
    _e("E64", "phone/service.py:sms_approvals", "approval_sms", NEEDS_YOU,
       "approval:{approval_id}", route=SMS),
    _e("E65", "phone/service.py:text_owner(reply)", "owner_sms", UPDATES,
       "owner-sms:{reason}", route=SMS),
    _e("E66", "friday_tray.py:crash_notice", "server_died", NEEDS_YOU,
       "tray-server-died", route=TRAY),
    _e("E67", "friday_tray.py:_heal_wedged", "wedge_restart", ACTIVITY,
       "tray-wedge-restart", route=TRAY),
    _e("E68", "friday_tray.py:_notify", "ptt_registration", ACTIVITY,
       "tray-ptt-registration", route=TRAY),
    _e("E69", "services/push_to_talk.py:_notify", "ptt_state", ACTIVITY, "", route=AMBIENT,
       ledger=False),
    _e("E70", "services/media_convert.py:_notify", "media_job", ACTIVITY,
       "media-job:{card_id}", route=BUS),
    _e("E71", "services/item_actions.py:_tell_held", "item_held", NEEDS_YOU,
       "item-held:{card_id}", route=BUS),
    # E72's module (slice_head) is not in every tree; the entry is kept so the
    # first one that carries it is already registered.
    _e("E72", "services/slice_head.py:maybe_offer", "slicing_offer", NEEDS_YOU,
       "slicing-offer", route=BUS),
    _e("E73", "services/codebases.py:_announce", "codebase_step", ACTIVITY,
       "codebase:{codebase_id}:{step}", route=BUS),
    _e("E74", "services/artifacts.py:_announce", "state_sync", ACTIVITY,
       "sync:{domain}", route=BUS, ledger=False),
    _e("E75", "services/approval_executor.py:_post_back", "approval_result", UPDATES,
       "approval-result:{approval_id}", route=TRANSCRIPT),
    _e("E76", "routes/artifacts.py:approve_plan", "plan_approved", UPDATES,
       "plan-approved:{plan_id}", route=TRANSCRIPT),
    _e("E77", "services/agent.py:_post_task_result_to_conversation", "task_result", UPDATES,
       "task-done:{task_id}", route=TRANSCRIPT),
    _e("E78", "services/local_context.py:deliver", "context_result", UPDATES,
       "context-result:{approval_id}", route=VOICE),
    _e("E79", "services/crew_voice.py:_emit", "crew_receipt", RECEIPTS, "", route=TRANSCRIPT,
       ledger=False),
    _e("E80", "services/voice_session.py:notify_once", "egress_receipt", RECEIPTS, "",
       route=STREAM, ledger=False),
    _e("E81", "routes/chat.py:stream", "stream_trace", RECEIPTS, "", route=STREAM, ledger=False),
    _e("E82", "core.py:process_register", "process_orb", ACTIVITY, "", route=AMBIENT,
       ledger=False),
    # A model swap on the same side of the cloud line: a receipt, pulled not pushed.
    _e("X02", "services/seat_transparency.py:observe_seats(same side)", "seat_receipt", RECEIPTS,
       "seat_receipt:{seat_state}"),
    # The offer to switch cloud voice back to local voice once it is measured capable
    # (services/local_voice_readiness: maybe_offer_switch for an owner with no recorded choice, once,
    # declined means never again; maybe_offer_switch_back after an "always", once until readiness has
    # changed). Both go through this emitter and this key, so there is one card at a time.
    _e("LV7", "services/local_voice_readiness.py:maybe_offer_switch+maybe_offer_switch_back", "local_voice_switch_offer",
       NEEDS_YOU, "local-voice-switch-offer", source="local-voice"),
    # Cloud voice is asked, never assumed: the question "just this call, or always?" when a switch
    # to cloud voice starts from the voice or after a local voice failure (the screen card; the
    # Settings row already has the panel in front of the owner), and the line that says a "just this
    # call" has ended and voice is back on local. The switch-back offer is LV7 above.
    _e("LV8", "services/local_voice_readiness.py:ask_cloud_switch", "cloud_voice_scope_question",
       NEEDS_YOU, "cloud-voice-scope", source="local-voice"),
    _e("LV9", "services/local_voice_readiness.py:_revert_call_scope", "cloud_voice_call_ended",
       UPDATES, "cloud-voice-call-ended", source="local-voice"),
    # Laya's personal learning (services/laya_personal.notify): the one-time "Laya learns from your
    # corrections" notice, and the confirmations of forget / reset / go back / wipe. Never carries the
    # owner's words.
    _e("LP1", "services/laya_personal.py:notify", "laya_learning", UPDATES,
       "laya-learning:{what}", source="laya"),
    # ── live-only emitters (L01-L06), gained since the census ────────────────
    _e("L01", "services/visible_jobs.py:visible_job", "visible_job", ACTIVITY, "",
       route=AMBIENT, ledger=False),
    _e("L02", "services/local_scheduler.py:status", "local_queue_status", ACTIVITY, "",
       route=AMBIENT, ledger=False),
    _e("L03", "services/cloud_spill.py:offer", "cloud_spill", NEEDS_YOU,
       "cloud-spill:{task_id}", route=POPUP, source="cloud_spill"),
    _e("L04", "routes/voice.py:_send(status)", "voice_status", ACTIVITY, "",
       route=STREAM, ledger=False),
    _e("L05", "routes/voice.py:_send(error)", "voice_error", ACTIVITY, "",
       route=STREAM, ledger=False),
    _e("L06", "routes/voice.py:_run_routed_front_turn", "router_receipt", RECEIPTS, "",
       route=STREAM, ledger=False),
    # Quiet turned on by the owner (voice, chat or the page): a visible line in Updates that never
    # merges with another, so every activation can be seen.
    _e("NQ1", "services/notification_tools.py:_quiet", "quiet_on", UPDATES, "quiet-on:{surface}:{seq}",
       source="interruptions"),
    # An approval whose time ran out while quiet hours, the limit or Focus Assist held it back.
    _e("NQ3", "services/approvals.py:_notify_expired_while_held", "approval_expired_held", NEEDS_YOU,
       "approval-expired:{approval_id}", source="approvals"),
    # A scheduled run that was due in quiet hours and waits for them to end.
    _e("NQ2", "services/scheduler.py:_defer_for_quiet", "scheduled_deferred", UPDATES,
       "sched-deferred:{schedule_id}:{day}", source="scheduler"),
    # ── cloud work orders (services/work_orders): local text only ────────────
    _e("WO1", "services/work_orders.py:enforce_switch", "work_order_switched_off", NEEDS_YOU,
       "work_orders:switch_off", source="work_orders", pk="warning"),
    _e("WO2", "services/work_orders.py:_worker", "work_order_started", ACTIVITY,
       "work_orders:start:{order_id}", source="work_orders", pk="work_order"),
    _e("WO3", "services/work_orders.py:_report_end:failed", "work_order_failed", NEEDS_YOU,
       "work_orders:failed:{order_id}", source="work_orders", pk="warning"),
    _e("WO4", "services/work_orders.py:_report_end:cancelled", "work_order_cancelled", NEEDS_YOU,
       "work_orders:cancelled:{order_id}", source="work_orders", pk="warning"),
    # ── meetings: the approved summary written to the calendar event ─────────
    _e("MC1", "services/meeting_calendar.py:_notify:added", "meeting_calendar_added", UPDATES,
       "meeting-calendar:info", source="meetings", pk="info"),
    _e("MC2", "services/meeting_calendar.py:_notify:held", "meeting_calendar_failed", NEEDS_YOU,
       "meeting-calendar:warning", source="meetings", pk="warning"),
    # ── calendar: a reminder Google has for an event, raised in Friday's tray ──
    _e("CR1", "services/calendar_reminders.py:tick", "calendar_reminder", UPDATES,
       "calendar-reminder:{event_id}:{minutes}", source="calendar"),
    # ── reserved: a kind with no emitter yet, exempt by name ─────────────────
    _e("X01", "(reserved) a cLaw block", "claw_block", NEEDS_YOU, "claw_block:{claw}",
       safety=True),
]

REGISTRY: dict = {e.id: e for e in _ALL}
CENSUS_IDS = tuple("E%02d" % i for i in range(1, 83))
#: Emitters live has gained since the 82-emitter census, registered by the same rule.
LIVE_IDS = tuple("L%02d" % i for i in range(1, 7))

_BY_KIND_SOURCE: dict = {}
_BY_KIND: dict = {}
for _em in _ALL:
    if _em.route not in (PUSH, DERIVED) or _em.id.startswith("X"):
        continue
    _pk = _em.push_kind or _em.kind
    _BY_KIND_SOURCE.setdefault((_pk, _em.source), _em)
    if _pk not in GENERIC_KINDS:
        _BY_KIND.setdefault(_pk, _em)
del _em


def get(emitter_id: str) -> Optional[Emitter]:
    return REGISTRY.get(str(emitter_id or ""))


def resolve(kind: str = "", source: str = "", emitter: Optional[str] = None) -> Optional[Emitter]:
    """The emitter a push belongs to: the one it names, else the one that owns
    its (kind, source), else the one that owns its kind alone. None when the
    registry does not know it (a new module that has not declared itself)."""
    if emitter:
        return REGISTRY.get(str(emitter))
    kind, source = str(kind or ""), str(source or "")
    hit = _BY_KIND_SOURCE.get((kind, source)) or _BY_KIND.get(kind)
    if hit is None and kind in SAFETY_CRITICAL_KINDS:
        hit = REGISTRY.get("X01") if kind == "claw_block" else None
    return hit


def is_safety_critical(kind: str = "", emitter: Optional[str] = None,
                       source: str = "") -> bool:
    if str(kind or "") in SAFETY_CRITICAL_KINDS:
        return True
    em = resolve(kind, source, emitter)
    return bool(em and em.safety_critical)


def is_receipt(kind: str = "", emitter: Optional[str] = None, source: str = "") -> bool:
    """A trace or a receipt: pulled, never pushed."""
    if str(kind or "") in RECEIPT_KINDS:
        return True
    em = resolve(kind, source, emitter) if emitter else None
    return bool(em and em.place == RECEIPTS)


def place_of(kind: str = "", source: str = "", emitter: Optional[str] = None,
             tier: Optional[str] = None) -> str:
    """Where a notice lives. A registered emitter names its place; an unknown
    one gets one from its tier (needs-you -> needs_you, the rest -> updates)."""
    if str(kind or "") in RECEIPT_KINDS:
        return RECEIPTS
    em = resolve(kind, source, emitter)
    if em is not None:
        return em.place
    return NEEDS_YOU if tier == "needs_you" else UPDATES


# ── The ledger line: one per notice ──────────────────────────────────────────

def note(emitter_id: str, delivered: bool = True, *, kind: Optional[str] = None,
         place: Optional[str] = None) -> bool:
    """Write the ledger line for a notice: kind, place, delivered or held. Counts
    only; the words of the notice never reach it. Never raises."""
    try:
        em = REGISTRY.get(str(emitter_id or ""))
        if em is None or not em.ledger:
            return False
        return record_line(kind or em.kind, place or em.place, delivered,
                           safety=em.safety_critical, emitter=em.id)
    except Exception:
        return False


def record_line(kind: str, place: str, delivered: bool, *, safety: bool = False,
                emitter: str = "") -> bool:
    try:
        from agent_friday.services import activity_ledger
        return activity_ledger.record(
            "notice", notice_kind=str(kind or "")[:48], place=place if place in PLACES else UPDATES,
            outcome=DELIVERED if delivered else HELD, safety=bool(safety),
            emitter=str(emitter or "")[:8])
    except Exception:
        return False


def _landed(result: Any) -> bool:
    """Did a writer reach anyone? A writer that answers with a count, a flag or
    a {"delivered": ...} dict says; one that answers nothing is taken to have."""
    if isinstance(result, dict):
        return bool(result.get("delivered", True))
    if result is None:
        return True
    return bool(result)


# ── The routed writers: the only home of the raw calls ───────────────────────

def bus_send(emitter_id: str, actions: Any, **kw: Any) -> Any:
    from agent_friday.services import desktop_bus
    out = desktop_bus.send(actions, **kw)
    note(emitter_id, _landed(out))
    return out


def bus_push(emitter_id: str, actions: Any) -> Any:
    from agent_friday.services import desktop_bus
    out = desktop_bus.push(actions)
    note(emitter_id, _landed(out))
    return out


def bus_broadcast(emitter_id: str, event: dict, kind: str = "chat") -> int:
    from agent_friday.services import desktop_bus
    out = desktop_bus.broadcast(event, kind=kind)
    note(emitter_id, _landed(out))
    return out


def transcript_add(emitter_id: str, conversation_id: str, message: dict, **kw: Any) -> Any:
    """A line in a conversation's transcript, written by background code."""
    from agent_friday.services import conversations
    out = conversations.append(conversation_id, message, **kw)
    note(emitter_id, _landed(out))
    return out


def voice_say(emitter_id: str, conversation_id: str, text: str, *, kind: str = "result") -> bool:
    """Hand a result to a live voice call on the conversation, if one is up."""
    from agent_friday.services import voice_live_channel
    out = voice_live_channel.deliver(conversation_id, text, kind=kind)
    note(emitter_id, bool(out))
    return bool(out)


def tray_balloon(emitter_id: str, icon: Any, message: str, title: str) -> bool:
    """A Windows tray balloon."""
    icon.notify(message, title)
    note(emitter_id, True)
    return True


# ── One delivery per completion ──────────────────────────────────────────────

SPOKEN, CHAT, PANEL = "spoken", "chat", "panel"


def completion_target(conversation_id: Optional[str]) -> str:
    """Where a finished task or workflow tells the owner, chosen by where the
    owner is: in a live call -> spoken; the chat open -> one chat line;
    otherwise -> the panel (Updates). Never more than one."""
    cid = str(conversation_id or "").strip()
    if cid:
        try:
            from agent_friday.services import voice_live_channel
            if voice_live_channel.is_live(cid):
                return SPOKEN
        except Exception:
            pass
        try:
            from agent_friday.services import desktop_bus
            if desktop_bus.connected("chat"):
                return CHAT
        except Exception:
            pass
    return PANEL


def deliver_completion(conversation_id: Optional[str], *, text: str, panel: Callable[[], Any],
                       chat: Callable[[], Any], emitter_id: str = "E77") -> str:
    """Deliver one completion in the one place the owner is. `panel` and `chat`
    do today's write (a card; a transcript line); the spoken line goes through
    the live call. A call that has gone by the time we speak falls back to the
    chat line or the card, so the result is never only said into a dropped
    call. Returns where it went."""
    target = completion_target(conversation_id)
    if target == SPOKEN:
        if voice_say(emitter_id, str(conversation_id), text, kind="task_result"):
            return SPOKEN
        target = CHAT if _chat_open() else PANEL
    if target == CHAT:
        chat()
        return CHAT
    panel()
    return PANEL


def _chat_open() -> bool:
    try:
        from agent_friday.services import desktop_bus
        return bool(desktop_bus.connected("chat"))
    except Exception:
        return False
