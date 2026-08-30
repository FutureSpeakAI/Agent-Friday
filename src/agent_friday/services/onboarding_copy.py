"""onboarding_copy -- the words the onboarding says, in ONE place.

WHY A MODULE AND NOT TWO SETS OF STRINGS
----------------------------------------
There were three onboarding surfaces and they disagreed with each other about
where secrets go and about what Friday can promise. The terminal wizard told
every user "your private information never leaves your device" while writing
``model_routing.mode: cloud_only``; the app wizard said something different; the
voice-first state machine said a third thing to nobody, because nothing called
it.

Duplicated copy is how that happened, so the copy lives here and every surface
renders THIS. Changing a promise now means changing one string, and the tests in
``tests/unit/test_onboarding_copy.py`` assert the promises against the behaviour
that has to keep them.

PROVENANCE
----------
The text is verbatim from ``docs/design/vault-first-onboarding.md`` §7.3, which
checked every sentence against the code. Two sentences were changed, both
because the code changed underneath them. Each is marked FACT-FIX below with
what it used to say and why it could not stay.
"""
from __future__ import annotations

# Screen order. Screens 1, 2 and 4 are new; 3 replaces the old provider screen;
# 3b is shown only when 3 chose cloud.
SCREEN_ORDER = ("collects", "vault", "routing", "cloud_ack", "third_party")


COLLECTS_TITLE = "Before anything else"

COLLECTS = """\
Friday is meant to be useful the way a person who knows you is useful. That
only works if she remembers things, so she writes things down.

Over the first few weeks she will build a wiki - plain text pages about your
projects, your work and the people you deal with, which you can open and edit
in any editor. Every night she reads those pages and links them together into a
map of how the parts of your life connect. She keeps every conversation you
have with her, and can find something you said months ago. She builds a picture
of how you work: your hours, the tools you reach for, the subjects you know
well.

All of it is a folder on this computer called .friday. You can open it, read
it, back it up, or delete the whole thing.

The rest of this setup is about who else can read it."""


VAULT_TITLE = "A passphrase for the private part"

VAULT = """\
Some of what Friday keeps is more sensitive than the rest. Finance, health,
legal and family records live in a separate place she calls the vault.

A passphrase encrypts those files on this disk, using AES-256-GCM. Without one
they sit there as readable text, and anyone who gets to this computer can open
them - another account on the same machine, a backup service, whoever fixes it
when it breaks."""

VAULT_SCOPE = """\
This covers finance, health, legal and family records. It does not cover your
wiki, your conversation history or the map she builds from them. Those stay
readable on this disk. You can add wiki sections to the encrypted set later, in
Settings.

If you would rather not decide now, you can set this in Settings whenever you
like. Friday encrypts whatever already exists the next time she starts, so
nothing is stranded - but everything written before then will have spent that
time readable."""

VAULT_WARNING = """\
If you lose this passphrase the files cannot be recovered. There is no
reset. Put it in your password manager, or write it down somewhere you would
keep a spare key."""

# Where it goes, said out loud. The old wizard offered to generate a passphrase
# and save it to start.bat, which is the file the installer deletes and which
# sat next to the API keys in plain text. That option is gone, not reworded.
VAULT_LOCATION = """\
Friday stores it in this computer's credential manager, not in any file you
could open. That is deliberate: an earlier version kept it in a startup script
inside her own program folder, which the installer replaces when she updates."""


ROUTING_TITLE = "Where your words go"

ROUTING = """\
Friday needs a language model to think with, and there are two places it can
run.

On this computer, where nothing leaves. That needs a graphics card with about
6.5 GB free for the smallest model that can still use her tools, and more for a
better one.

Or in the cloud, at Anthropic or Google, where your messages are sent over an
encrypted connection and answered on their servers."""

ROUTING_CHOICES = (
    ("cloud_only",
     "Cloud",
     "Friday thinks at Anthropic. Fastest to set up, and the sharpest answers."),
    ("local_only",
     "On this computer only",
     "Nothing is sent anywhere, ever."),
    ("local_preferred",
     "Both",
     "This computer by default, the cloud when it would clearly help."),
)


CLOUD_ACK_TITLE = "What cloud mode changes"

CLOUD_ACK_INTRO = """\
You have chosen to have Friday think at Anthropic. This is a normal choice and
most people will make it - it is the same arrangement you already have with any
assistant you use in a browser. Here is what actually differs, so none of it is
a surprise later."""

# FACT-FIX 1.
#
# The drafted paragraph read, in full:
#
#   "What stays the same. Everything Friday writes down still lives on this
#    computer. The wiki, the conversations, the map, the vault - those are local
#    files either way. Cloud mode changes where the thinking happens, not where
#    the memory lives."
#
# Every clause of that is true about LOCATION and the last sentence is the best
# one in the document. But naming "the map" in a list of things that are the
# same invites the reader to conclude the map is the same, and it is not. The
# knowledge graph has two tiers: Tier A links wiki pages structurally with no
# model at all, and Tier B is the semantic layer that reads the text and works
# out who and what is being talked about. Tier B's indexing_mode defaults to
# local_only, which pins every extraction call to a local model, so on a machine
# without one every call fails and Tier B produces nothing. It used to do that
# silently and report success; indexer.py now says so out loud.
#
# So the "what stays the same" claim keeps its scope and the map moves to the
# paragraph about what is different, where it belongs.
CLOUD_ACK_SAME = """\
What stays the same. Everything Friday writes down still lives on this
computer. The wiki, the conversations, the map, the vault - those are local
files either way. Cloud mode changes where the thinking happens, not where the
memory lives."""

CLOUD_ACK_REFUSE = """\
What she will refuse to do. Friday will not send certain things to
Anthropic. When she recognises money, health, legal or identity details in what
you have written, she holds the whole message back and tells you she has. On a
computer that cannot run a local model, that means those questions do not get
answered at all. Ask her about a bank balance and she will decline rather than
reply. That is the boundary working, not a fault, but it is a real limit and
you should know about it before you meet it."""

# FACT-FIX 1, continued -- the sentence the map needed and did not have.
CLOUD_ACK_MAP = """\
What she builds less of. That nightly map has two layers. The first links your
pages together by what they reference, and works anywhere. The second reads the
text and works out who and what you were talking about, and it only runs on a
model on this computer. Without one you get the first layer and not the second,
so the map is a set of connections rather than an understanding."""

CLOUD_ACK_PROMISE = """\
What we cannot promise. The thing that recognises sensitive material is a
pattern matcher. It knows the shape of an account number, a phone number and an
address, and the vocabulary of finance, medicine and law. It does not know that
a word is a name. If you write "she started sertraline last month", that
sentence goes to Anthropic, because nothing in it looks like a medical record to
a program matching words. Assume that anything you type in cloud mode may be
read by the provider you chose.

You can change this later in Settings, and changing it changes only where
future thinking happens."""


THIRD_PARTY_TITLE = "The part about other people"

THIRD_PARTY_INTRO = """\
Friday will end up holding records about people who are not you. Tell her about
a meeting and she may write a page about the person you met. Every night she
reads her own notes and links names to projects and to each other. After a year
that is a fairly detailed picture of your colleagues, your family, and anyone
you deal with often.

They did not agree to any of this. You are the only person here who was asked.

Two things follow from that, and they are worth a minute."""

THIRD_PARTY_TRAVEL = """\
In cloud mode, their details travel with yours. The limits on the last
screen apply to what you write about other people exactly as they apply to what
you write about yourself - including the gap. A sentence about a friend's
diagnosis is no better protected than one about your own."""

# FACT-FIX 2.
#
# The drafted paragraph read:
#
#   "Friday cannot yet forget a person. She can learn about someone and change
#    her mind about them. She has no way to remove them, and nothing ages out on
#    its own. If someone asks you to delete what you hold about them, today that
#    means editing a file by hand. We think that is the wrong answer and we are
#    working on a better one."
#
# That was true when it was written and is now false: services/forget_person.py
# and the Forget control in Contacts landed with this release. The spec
# anticipated exactly this and supplied the replacement itself (§7.3, "Notes for
# the builder"), which is what follows, extended by one sentence because the
# implementation is honest about a limit the spec's draft did not mention: the
# user's own wiki pages are not rewritten.
THIRD_PARTY_FORGET = """\
Friday can forget a person. Open Contacts, choose someone, and Forget shows you
what she holds about them - her contact record, and the entries and links in her
map - and removes it. She will not build them again. Your own pages and
conversations are left alone, because those are your words; she will show you
which ones still mention that person so you can decide about them yourself."""


def screen(name: str) -> dict:
    """One screen as {title, blocks}. Renderers decide how to draw it."""
    if name == "collects":
        return {"title": COLLECTS_TITLE, "blocks": [COLLECTS]}
    if name == "vault":
        # The warning is separated from the body rather than being its last
        # paragraph. Read the screen aloud and the reader's attention goes to
        # the passphrase field and then to this sentence -- it is the one most
        # likely to make someone reach for a password manager, and it is the
        # only unrecoverable fact on the screen. Rendering it as one more
        # paragraph in a stack of five buries it.
        return {"title": VAULT_TITLE,
                "blocks": [VAULT, VAULT_SCOPE, VAULT_LOCATION],
                "warning": VAULT_WARNING}
    if name == "routing":
        return {"title": ROUTING_TITLE, "blocks": [ROUTING],
                "choices": [{"mode": m, "label": lbl, "detail": det}
                            for m, lbl, det in ROUTING_CHOICES]}
    if name == "cloud_ack":
        return {"title": CLOUD_ACK_TITLE,
                "blocks": [CLOUD_ACK_INTRO, CLOUD_ACK_SAME, CLOUD_ACK_REFUSE,
                           CLOUD_ACK_MAP, CLOUD_ACK_PROMISE]}
    if name == "third_party":
        return {"title": THIRD_PARTY_TITLE,
                "blocks": [THIRD_PARTY_INTRO, THIRD_PARTY_TRAVEL,
                           THIRD_PARTY_FORGET]}
    raise ValueError("unknown onboarding screen: %r" % (name,))


def all_screens() -> list:
    return [dict(screen(n), name=n) for n in SCREEN_ORDER]
