"""Who is changing the mailbox, and the checkpoint each change passes.

The Messages page, its label menu, Unsubscribe, Save to Gmail Drafts and the
inbox clean-up board change Gmail (and Friday's record of it) for the owner.
Until 2026-10-21 the owner was whoever the request body said it was
(`requested_by: "ui:..."`), and the change ran with no checkpoint. Now, as on
the Calendar page:

* The owner is the owner's press in Friday's page on this PC
  (services/owner_press: a same-origin page carrying the page's session token
  and, where this tree has the desk-press check, this launch's desk key). A
  body that says "ui:..." without that press changes nothing, and the answer
  says so and says where the change can be made.
* Every change the owner makes that reaches Gmail or a mailing list passes the
  action checkpoint first (governance/action_gate.record_external: the cLaws
  are intact and a signed receipt of this exact change is written). When the
  checkpoint holds it, nothing is sent to Google and the answer says so.
  Changes that stay in Friday (snooze, mute, Friday's own archive flag for an
  account that has not allowed changes) are the owner's press alone.
* A request that names someone other than the owner, or names no one and is
  not the owner's press, is Friday (or a program) asking. The changes Friday
  may ask for (archive, Trash, spam, mute, unsubscribe) become an approval
  card that says what will happen (services/mail_proposals); any other change
  is answered with what Friday can ask for instead. Nothing is refused
  silently.
"""
from __future__ import annotations

#: The checkpoint receipt's surface for every mail change made from Friday's page.
SURFACE = "messages_workspace"

OWNER = "owner"
CLAIMED = "claimed"
FRIDAY = "friday"

ONLY_FROM_THE_PAGE = ("mail changes are made from Friday's Messages page on this PC; Friday "
                      "itself can ask to archive, delete, mute, report spam or unsubscribe, "
                      "and that asks you first")


def claims_owner(actor) -> bool:
    """The body says the owner's click in Friday's page made it ("ui" / "ui:<surface>").
    A label, not proof: only `who` decides."""
    actor = str(actor or "").strip().lower()
    return actor == "ui" or actor.startswith("ui:")


def pressed(request) -> bool:
    """This request is the owner's press in Friday's page on this PC."""
    try:
        from agent_friday.services import screen_click
        return bool(screen_click.screen_session(request))
    except Exception:  # noqa: BLE001 - a check that cannot run is not a press
        return False


def who(request, actor) -> str:
    """OWNER for the owner's press; CLAIMED for a body that says it is the owner without the
    press; FRIDAY for a request that names someone else, or names no one and is no press."""
    if str(actor or "").strip() and not claims_owner(actor):
        return FRIDAY
    if pressed(request):
        return OWNER
    return CLAIMED if claims_owner(actor) else FRIDAY


def not_the_owners_press(request, nothing_done: str):
    """None for the owner's press, else the 403 to answer. Nothing has changed when it is
    returned. Where the tree has the desk-press check, the answer is its coded refusal, so
    Friday's page fetches this launch's desk key and presses once more."""
    from agent_friday.services import owner_press
    return owner_press.refusal(request, "%s: %s." % (nothing_done, ONLY_FROM_THE_PAGE))


def friday_cannot_ask(nothing_done: str):
    """The answer for a change Friday asked for that has no approval card."""
    from flask import jsonify
    return jsonify({"status": "error", "code": "owner_press_required",
                    "message": "%s: %s." % (nothing_done, ONLY_FROM_THE_PAGE)}), 403


def held(action: str, target: str, nothing_done: str):
    """None when the action checkpoint lets this mail change through, else the 403 to answer.

    Runs before anything is sent to Google or a mailing list: the cLaws are intact and a
    signed receipt of this exact change exists first. `nothing_done` starts the answer when
    it is held ("Nothing was changed", ...), so the page never reads a hold as a success."""
    from flask import jsonify
    from agent_friday.governance import action_gate as _gate
    try:
        _gate.record_external(action, surface=SURFACE, target=str(target or "")[:300])
    except _gate.Held as e:
        print(f"  [mail] {action} held by the action gate: {type(e).__name__}")
        return jsonify({"status": "error", "held": True,
                        "message": nothing_done + ": the action checkpoint held this change (its "
                                                  "integrity check or signed receipt failed), so "
                                                  "nothing reached Gmail. Try again."}), 403
    return None


def gmail_target(account_id, thread_ids=(), extra: str = "") -> str:
    """What a receipt names: the account and the conversations (ids only), and the change."""
    tids = [str(t) for t in (thread_ids or [])][:20]
    more = len(list(thread_ids or [])) - len(tids)
    out = "%s|%s%s" % (account_id or "", ",".join(tids), (" +%d more" % more) if more > 0 else "")
    return (out + (" " + extra if extra else ""))[:300]
