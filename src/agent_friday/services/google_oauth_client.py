"""Which Google OAuth client Friday uses, and what to tell the user about it.

Two paths, both first-class (Stephen's decision, 2026-08-26: "accept the
unverified app warning AND bring your own with a walkthru. Let's do both.")

  * BUNDLED — ships with Friday. One click. Shows Google's unverified-app
    warning, and is subject to a cap of 100 NEW USERS over the lifetime of the
    project, which cannot be reset.
  * BYO — the user's own Google Cloud client. No warning, no cap, but they
    have to go and make one. `byo_steps()` is the guided flow.

BYO is not a power-user extra. It is the ESCAPE HATCH: once the bundled client
hits the cap, it is the only way anybody connects. That is why it takes
precedence — someone who went to the trouble of making their own client must
never be silently routed back onto the full one.


WHY THE CLIENT SECRET IS IN A PUBLIC REPO ON PURPOSE
-----------------------------------------------------
Because for an installed application it is not a secret, and Google says so.
RFC 8252 treats native apps as PUBLIC clients that cannot keep secrets, and
Google's own "OAuth 2.0 for iOS & Desktop Apps" guidance is written on that
assumption: the value ships inside every copy of the application and can be
read out of any install.

What it is NOT is a key to anyone's data. It identifies the APPLICATION, not a
user. Possessing it lets someone construct a consent screen that says "Agent
Friday" — it does not read a single mailbox. Every actual grant still requires
that user's interactive Google sign-in, and the resulting token is encrypted
on their own machine and never transits anything Stephen runs.

The real exposure is reputational and quota-shaped, not confidential: someone
could impersonate Friday's consent screen, or burn the project's user cap.
Both are recoverable by rotating the client, and both are the accepted cost of
the installed-app model.

This is documented in THREAT_MODEL.md ("Shipped Google OAuth client") and
allowlisted BY NAME in .githooks/security_scan.py, so that neither a security
researcher nor the scanner has to guess whether it was an accident.


THE CREDENTIAL IS NOT FILLED IN HERE
------------------------------------
The constants below are EMPTY, and everything in this module treats empty as
"there is no bundled client" — Friday falls through to BYO exactly as it did
before. Only Stephen can mint the real values: they belong to his Google Cloud
project and name him as the publisher on the consent screen. The steps are in
docs/design/google-oauth-verification-checklist.md.

Half a client is no client. An id with no secret cannot complete a flow, so
offering it would march someone through the warning screen to reach an error.
"""
from __future__ import annotations

# ── The shipped client ───────────────────────────────────────────────────────
# PASTE THE DESKTOP CLIENT HERE. Public on purpose — see the module docstring
# and THREAT_MODEL.md. Empty means "no bundled client", which is the shipping
# state until the Cloud project exists.
BUNDLED_CLIENT_ID = ""
BUNDLED_CLIENT_SECRET = ""

_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"

# ── Error classifications ────────────────────────────────────────────────────
CAP_REACHED = "cap_reached"
DECLINED_OR_CAPPED = "declined_or_capped"
MISCONFIGURED = "misconfigured"
ADMIN_BLOCKED = "admin_blocked"
UNKNOWN = "unknown"


def bundled_config() -> dict | None:
    """The shipped Desktop client, or None if it was never filled in."""
    cid = (BUNDLED_CLIENT_ID or "").strip()
    sec = (BUNDLED_CLIENT_SECRET or "").strip()
    if not cid or not sec:
        return None
    return {
        "installed": {
            "client_id": cid,
            "client_secret": sec,
            "auth_uri": _AUTH_URI,
            "token_uri": _TOKEN_URI,
            # Desktop/loopback. A Web client here produces
            # redirect_uri_mismatch, which reads to a user as Friday being
            # broken -- calendar_engine._google_client_config makes the same
            # point about preferring an "installed" client.
            "redirect_uris": ["http://localhost"],
        }
    }


def resolve_client(byo_result=(None, None)):
    """(config, source_label, kind) where kind is byo | bundled | none.

    `byo_result` is whatever `calendar_engine._google_client_config()` found:
    a client the user supplied, from a file or from the environment. It WINS.

    Precedence matters more than it looks. Once the bundled client reaches
    Google's lifetime cap, a user's own client is the only one that can
    complete a flow — so quietly preferring the bundled one would break the
    exact people who had already worked around the problem.
    """
    cfg, source = byo_result if byo_result else (None, None)
    if cfg:
        return cfg, source or "your own client", "byo"
    bundled = bundled_config()
    if bundled:
        return bundled, "bundled with Friday", "bundled"
    return None, None, "none"


def classify_error(error: str | None, description: str | None = None) -> str:
    """Turn an OAuth callback error into something we can act on.

    Deliberately refuses to over-read `access_denied`. Google returns it BOTH
    when the project's user cap is exhausted and when the person simply clicks
    Cancel, and it does not always say which. Telling someone their app is
    full when they only changed their mind sends them off to build a Google
    Cloud project they never needed.
    """
    err = (error or "").strip().lower()
    desc = (description or "").strip().lower()
    if not err:
        return UNKNOWN
    if err == "admin_policy_enforced":
        return ADMIN_BLOCKED
    if err in ("invalid_client", "redirect_uri_mismatch", "unauthorized_client"):
        return MISCONFIGURED
    if err == "access_denied":
        # Google sometimes names it outright ("OAuth user cap reached").
        if "cap" in desc or "limit" in desc or "quota" in desc:
            return CAP_REACHED
        return DECLINED_OR_CAPPED
    return UNKNOWN


def explain_error(code: str) -> str:
    """The classification as something a person can act on.

    Never names a file or a directory. That sentence — "place a Desktop OAuth
    client JSON at ~/.friday/credentials.json" — is the wall Janet hit on
    2026-08-26 and the reason this whole module exists.
    """
    if code == CAP_REACHED:
        return (
            "Google has capped how many people can connect through Friday's "
            "shared sign-in, and that cap is now full. Nothing is wrong with "
            "your account. You can still connect by using your own Google "
            "sign-in — it takes about ten minutes, once, and has no cap. "
            "Friday will walk you through it."
        )
    if code == DECLINED_OR_CAPPED:
        return (
            "Google did not complete the sign-in. Either you chose Cancel on "
            "the permission screen, or Friday's shared sign-in has reached the "
            "limit Google puts on how many people may use it. If you meant to "
            "cancel, nothing has changed and you can try again. If you did not "
            "cancel, the limit is full and you can set up your own Google "
            "sign-in instead — Friday will walk you through it."
        )
    if code == MISCONFIGURED:
        return (
            "Google rejected the sign-in details Friday is using. If you set "
            "up your own Google sign-in, the most common cause is choosing "
            "the wrong application type — it has to be Desktop, not Web. You "
            "can walk through the setup again and re-enter it."
        )
    if code == ADMIN_BLOCKED:
        return (
            "Your organisation's Google administrator has blocked apps like "
            "Friday from reaching this account's data. This is a policy on "
            "your workplace account, not something Friday can change — an "
            "administrator has to allow it, or you can connect a personal "
            "Google account instead."
        )
    return (
        "Google did not finish the sign-in and did not say why. Nothing has "
        "been changed and no account was connected. Trying again usually "
        "works; if it keeps happening you can set up your own Google sign-in "
        "instead."
    )


def consent_prebrief(kind: str) -> str:
    """What to say BEFORE sending someone to Google.

    A person who meets "Google hasn't verified this app" with no warning
    assumes they are being phished and abandons. A person who was told it is
    coming, why it is there, and which button to press, continues. This is
    probably the single highest-value piece of copy in the feature.
    """
    if kind == "byo":
        # Their own client shows no warning. Promising one would be a lie and
        # would make a normal screen look like a problem.
        return (
            "Google will open in your browser and ask whether Friday may see "
            "your calendar and mail. This uses your own Google sign-in, so it "
            "will look like any other app you have connected. Friday keeps "
            "the result encrypted on this computer."
        )
    return (
        "Google will open in your browser, and it will show a warning that "
        "says \"Google hasn't verified this app\".\n\n"
        "That is expected, and here is what it means: Google reviews apps "
        "before removing that notice, and Friday has not been through that "
        "review yet. The notice is about the review, not about anything being "
        "wrong on your computer.\n\n"
        "To continue, choose **Advanced**, then **Go to Agent Friday**. Google "
        "will then ask whether Friday may see your calendar and mail. Friday "
        "keeps the result encrypted on this computer and sends it nowhere.\n\n"
        "If you would rather not see that screen at all, you can use your own "
        "Google sign-in instead — it takes about ten minutes, once."
    )


# ── Bring your own: the guided flow ──────────────────────────────────────────
# Ordered, clickable, and ending in a paste field. Explicitly NOT "download
# this JSON and put it in a folder" — the thing that stopped Janet dead.

def byo_scopes() -> list:
    """The scopes to add, so nobody has to guess.

    A missing scope does not fail at setup; it fails later as a 403 the user
    cannot interpret. Listing them is the difference between a flow that works
    and one that half-works a week afterwards.
    """
    try:
        from agent_friday.services.google_accounts import GOOGLE_MULTI_SCOPES
        return list(GOOGLE_MULTI_SCOPES)
    except Exception:
        # Kept in step with services/google_accounts.py; the import above is
        # the source of truth and this is only for an import-time failure.
        return [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/documents.readonly",
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/tasks",
            "https://www.googleapis.com/auth/contacts.readonly",
            "https://www.googleapis.com/auth/userinfo.email",
        ]


def byo_steps() -> list:
    """The walkthrough, as data the UI renders one card at a time."""
    return [
        {
            "n": 1,
            "do": "Open the Google Cloud Console and create a new project",
            "url": "https://console.cloud.google.com/projectcreate",
            "detail": "Call it anything you like — \"Friday\" is fine. It is "
                      "free, and it is yours; nothing about it is shared with "
                      "anyone else.",
        },
        {
            "n": 2,
            "do": "Turn on the Google services you want Friday to use",
            "url": "https://console.cloud.google.com/apis/library",
            "detail": "Search for and enable: Gmail API, Google Calendar API, "
                      "Google Drive API, Google Docs API, Google Sheets API, "
                      "Google Tasks API, People API. Enable only the ones you "
                      "want — Friday will simply say a feature is unavailable "
                      "if you skip one.",
        },
        {
            "n": 3,
            "do": "Fill in the OAuth consent screen and choose External",
            "url": "https://console.cloud.google.com/auth/overview",
            "detail": "Put your own email in the support and contact fields. "
                      "Because the project is yours and you are its only user, "
                      "you will not see an unverified-app warning.",
        },
        {
            "n": 4,
            "do": "Add the permissions Friday asks for",
            "url": "https://console.cloud.google.com/auth/scopes",
            "detail": "Paste the list below into Add or Remove Scopes. Any you "
                      "leave out simply switch that feature off.",
            "scopes": byo_scopes(),
        },
        {
            "n": 5,
            "do": "Publish the app so your sign-in does not expire",
            "url": "https://console.cloud.google.com/auth/audience",
            "detail": "Set Publishing status to \"In production\". Left in "
                      "Testing, Google expires the connection after 7 days and "
                      "you would have to reconnect every week.",
        },
        {
            "n": 6,
            "do": "Create the credentials — choose Desktop app",
            "url": "https://console.cloud.google.com/apis/credentials/oauthclient",
            "detail": "Application type must be Desktop app. Web application "
                      "will not work and produces a redirect error.",
        },
        {
            "n": 7,
            "do": "Copy the Client ID and Client secret, and paste them below",
            "detail": "Google shows both on screen as soon as the client is "
                      "created. Paste them into the two fields here and press "
                      "Save — Friday encrypts them on this computer. There is "
                      "nothing to download and nothing to save anywhere.",
            "paste": True,
        },
    ]


# ── Where a pasted client lives ──────────────────────────────────────────────
# The walkthrough ends in "paste these two values", so they need somewhere to
# go that is not a file the user has to manage. The credential store already
# holds API keys encrypted on this machine; the last step of the walkthrough
# promises exactly that, and a promise the UI makes has to be true.
#
# Note this is a client id and secret for an INSTALLED app, so encryption here
# is hygiene rather than protection (see the module docstring) -- but it keeps
# one storage story for every credential Friday holds, and it means the user
# never learns where a file lives.

_BYO_ID = "google_oauth_client_id"
_BYO_SECRET = "google_oauth_client_secret"  # pragma: allowlist secret


def save_byo(client_id: str, client_secret: str) -> None:
    """Store a client the user pasted. Raises ValueError on half a client.

    Refusing at the door beats storing something that fails later at the
    consent screen, where the cause is invisible and reads as Friday being
    broken.
    """
    cid = (client_id or "").strip()
    sec = (client_secret or "").strip()
    if not cid or not sec:
        raise ValueError("both the Client ID and the Client secret are needed")
    from agent_friday.services import credential_store as cs
    cs.set_provider_key(_BYO_ID, cid)
    cs.set_provider_key(_BYO_SECRET, sec)


def stored_byo_config() -> dict | None:
    """The client the user pasted, or None. Never raises."""
    try:
        from agent_friday.services import credential_store as cs
        cid = (cs.get_provider_key(_BYO_ID) or "").strip()
        sec = (cs.get_provider_key(_BYO_SECRET) or "").strip()
    except Exception:
        return None
    if not cid or not sec:
        return None
    return {
        "installed": {
            "client_id": cid,
            "client_secret": sec,
            "auth_uri": _AUTH_URI,
            "token_uri": _TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }


def clear_byo() -> bool:
    """Forget a pasted client. True if anything was removed."""
    try:
        from agent_friday.services import credential_store as cs
        a = cs.delete_provider_key(_BYO_ID)
        b = cs.delete_provider_key(_BYO_SECRET)
        return bool(a or b)
    except Exception:
        return False


def active_client(discover=None):
    """The client Friday will actually use: (config, source_label, kind).

    Order, and every step of it is deliberate:

      1. a client the user PASTED through the walkthrough
      2. a client already on disk or in the environment (existing installs --
         Stephen has a client_secret*.json today, and adding a new storage
         location must not strand it)
      3. the BUNDLED client
      4. nothing

    BYO before bundled is the escape hatch. Once the shipped client reaches
    Google's lifetime cap it can no longer complete a flow at all, so quietly
    preferring it would break precisely the people who had already worked
    around the problem.

    `discover` defaults to calendar_engine._google_client_config. A failure in
    it is survivable -- fall through to the bundled client rather than taking
    the whole flow down with a disk error.
    """
    pasted = stored_byo_config()
    if pasted:
        return pasted, "your own Google sign-in", "byo"

    if discover is None:
        def discover():
            from agent_friday.services.calendar_engine import _google_client_config
            return _google_client_config()
    try:
        found = discover() or (None, None)
    except Exception:
        found = (None, None)

    return resolve_client(byo_result=found)
