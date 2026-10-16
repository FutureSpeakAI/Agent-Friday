"""The first-run conversation's opening steps, answered the ordinary way.

Tests that care about a later stage walk through these first. Each answer is
the plain one: type rather than talk, no offer, keep Friday, skip what is
skippable, give the name "Sam", and leave the posture unanswered so the
routing mode the test began with stands.
"""

PREAMBLE = (
    ("welcome", "type", ""),
    ("offer", "none", ""),
    ("agent_name", "Friday", ""),
    ("voice", "later", ""),
    ("pronouns", "skip", ""),
    ("framing", "skip", ""),
    ("about_name", None, "Sam"),
    ("about_city", "skip", ""),
    ("about_tz", "skip", ""),
    ("basics", {"minor_mode": False, "distribution": "default"}, ""),
    ("posture", "skip", ""),
    ("never_send", "none", ""),
)

#: After the style step, the steps before the room-voice question.
AFTER_STYLE = (
    ("routines", "none", ""),
    ("memory_rules", "ok", ""),
    ("household", "skip", ""),
)


def answer_through(sc, steps, stop_at=None):
    """Answer each step in order, stopping (without answering) at `stop_at`."""
    for stage, value, text in steps:
        if stop_at and sc.load_state()["stage"] == stop_at:
            return
        if sc.load_state()["stage"] != stage:
            continue
        sc.answer(stage, value, text)
