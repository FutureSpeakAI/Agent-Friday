"""Write today's briefing into a SCRATCH Friday home the way the generator does, minus the model call.

Used by tests/app/specs/start_my_day_frame.spec.ts. The model's draft is a fixed synthetic text; everything after it
is the real finishing step (news_engine._finish_briefing: link validator, section skills with their "Didn't run"
lines, the generation stamp) and the real writer (briefing_files.write). It refuses to run without FRIDAY_HOME set
to a temporary folder, so it can never touch the owner's home.

    FRIDAY_HOME=<scratch> python tests/app/helpers/seed_start_my_day.py
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

home = os.environ.get("FRIDAY_HOME", "")
if not home or not Path(home).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
    sys.exit("FRIDAY_HOME must be a scratch folder under the temp directory")
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from agent_friday.services import briefing_files  # noqa: E402
from agent_friday.services import news_engine as ne  # noqa: E402

DRAFT = """# Briefing for today
## Today's Calendar
- Planning meeting with the design team at ten. [C1]
- A meeting the model made up, with no id.
## News
- The council passed the transit budget, 7 to 2. [N1]
- A story nobody sourced.
## Email
- Dana Reyes asks whether the budget review can move to Thursday. [E1]
- Sam Lee wrote, but the message id did not come back. [E2]
"""

ne._start_briefing_sources()
ne._keep_briefing_sources(
    calendar=[{"id": "C1", "title": "Planning meeting", "start_time": "", "end_time": "", "location": "",
               "url": "https://calendar.example.test/event?eid=ev1"}],
    email=[{"id": "E1", "message_id": "18f3a1b2c3d4e5f6", "account": "owner@example.test",
            "sender": "Dana Reyes", "subject": "Budget review"},
           {"id": "E2", "message_id": "", "account": "", "sender": "Sam Lee", "subject": "No id"}],
    news=[{"id": "N1", "title": "Council passes the transit budget", "source": "examplewire.com",
           "url": "https://examplewire.com/2031/transit"}])
ne._skill_blocks()
content = ne._finish_briefing(DRAFT)
path = briefing_files.write(datetime.now().strftime("%Y-%m-%d"), content)
print(path)
