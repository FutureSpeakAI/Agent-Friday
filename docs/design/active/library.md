# The Library: local document answers with footnotes, and the 3D shelves

> **Status:** built on branch `feat/library-shelves`, not on main. The implementation is the
> authority; where this page and the code disagree, the code wins.
> **Credit:** ideas from jevbox and pg-jev (see `CREDITS.md`). jevbox publishes no licence, so no
> code, prompt, wording, icon or asset was used; everything here was written from a description
> of the ideas. The same holds for pg-jev (version stamps and the cloud-text cap).

## What it is

The owner chooses folders and files to add. Friday reads them on this PC, keeps an index of
their structure, and answers questions with footnotes that open the exact page and paragraph,
or the exact second of a recording. Nothing is sent anywhere unless the owner's routing sends
a question to a cloud model, and then only passages of documents that carry their own cloud
permission.

Everything is local: extraction in a limited child process, a 22M-parameter sentence encoder
on the CPU, Laya when it is already loaded, and the local brain to write an answer. There is
no telemetry and the package opens no socket (`test_library_package_opens_no_socket`).

## Where it lives

- A **Library** workspace (dock entry, window, tab) holds Ask, Documents, Couldn't read, the
  vault shelf, Browse this PC, a 2D list and the 3D **Shelves** view, with an inspector.
- Media keeps its own 3D layout for what Friday makes; the 3D file browser and "Browse this PC"
  moved here (this amends Media decision D4).
- Settings, Privacy and Data, Library: search on or off, cloud-written answers (off by default) and
  the most document text one answer may send to a cloud model (default 6,000 characters), reading
  on battery, and whether the knowledge graph learns from Library documents.

## Consent

Nothing is indexed because Friday happened to see it. "Add to the Library" is a signed event in
the file-grants ledger (`library_add`, `library_remove`, `library_shelf`), read by the same
verifier as cloud grants: a tampered line suspends the Library, and a deny mark beats an add.
An add is never a cloud grant. Two doors create one, both by the owner's own act: the
workspace's folder picker, and an approved card Friday raised through `file_access`
(`library_add`, `library_remove`, `library_forget`, screen-only like every file card).

Every request to `/api/library/*` must carry the page's rotating session token and is refused
when a browser says another site caused it. A change (add, remove, forget, move to or from the
vault, re-read) is accepted only as a verified click on the Library page (same origin plus the
token) and then passes `action_gate.authorize`, where that click is classed internal and
receipted; the same action from anywhere else is held. The screen-only approval cards take their
identity from the verified request, never from a field in the body. A ledger that cannot be
verified pauses the Library: the index is frozen, never emptied.

## The index

One SQLite file per principal (`library/<principal>/library.sqlite`), isolation by separate
files, never a WHERE clause. With the optional SQLCipher binding the whole file is encrypted
at rest with a random key held under the Windows account's protection; without it the file is
plain and the workspace says so.

- **Extraction** (child process, 120 s wall clock, 1.5 GB cap): PDF text layer with page and
  box per paragraph, local OCR for scanned pages, DOCX heading styles, Markdown, plain text,
  code, HTML as text, CSV, XLSX, caption files and recordings through a transcript.
  Caps: 200 MB file, 2,000 pages, 50,000 blocks, 5,000 sections, 10 MB text, zip bombs and
  XML entities refused. A document that breaks a cap is listed as "couldn't read", never skipped.
  A build that cannot limit the child (a frozen build today) refuses to read documents rather than
  read them inside the server.
- **Structure:** blocks, a heading tree (synthetic sections when there are no headings),
  passages of at most 900 characters, tables split only between rows with the header repeated.
- **Credentials are never indexed.** A private key or a vendor token pasted inside a document is
  cut out of the extracted text (`credential_paths.redact_secrets`, the call `read_file` uses)
  before anything is made from it, across block boundaries, so no passage, vector, full-text row,
  profile, title or evidence block holds it, only the marker where it was; both shelves, since the
  classifier reads the text as extracted and the vault shelf stores the withheld text. A document
  whose key cannot be cut out cleanly (armour chunked to defeat the patterns, or nothing but
  credentials) is listed as skipped, and a check that cannot run fails the document. Password and
  `key = value` forms are not cut (as with `read_file`: they gate egress, not reading). An index
  built by an older version is read again.
- **Profiles** (title, heading outline, first sentence) are deterministic. No model writes any
  text in the index.
- **Shelves:** the sensitivity classifier tiers each document. Private ones go on the vault
  shelf: text, headings and profiles sealed with the vault key, kept out of full-text search,
  searchable only while the vault is unlocked. With no vault key they are not indexed at all.

## Search

A question is embedded once. Menus of at most eight children plus "none of these" walk
folder, document, section: the encoder answers every menu by similarity, Laya answers only the
ones the encoder is unsure of, only when already loaded, at most four times. A beam keeps the
best routes; weak routes widen, then every passage is searched, then the brain gets the best
snippets with an instruction to say what it could not find. A passage that names none of the
question's distinctive words is scored down. Evidence is bounded (12 passages, 6,000 characters
on the floor tier) and arrives before any answer is written.

**Versions.** Every search record and every cited answer carries a stamp: the encoder's id and
file hash, a hash of the menu wording, a hash of the calibration, the index version, and Laya's
id only when Laya actually answered a menu. An old citation can be traced to the versions that
made it.

**Cloud cap.** When the owner has chosen a cloud model and allowed cloud-written answers, only
passages of documents with their own cloud permission go, never the vault shelf, and at most
`library_cloud_char_cap` characters per turn in all (default 6,000). When that trims, the tool
result says so and the answer says so. Unless cloud answers are allowed, earlier Library answers are replaced
by a one-line stand-in before conversation history goes to a cloud model.

## Footnotes

The model sees labels (`[1.2]`); the server turns them into `[lib:doc#block]`. A token is live
only when this turn's `search_library` returned that block, and is checked again before the
reply is shown, saved or spoken: still added, not forgotten, same principal, file unchanged.
Otherwise it is inert or plain words. A chip shows the title and `p. 14 ¶3` (or the time);
click opens the Reader, Shift-click the 3D shelves.

## The Reader

A PDF page is drawn by a vendored, hardened pdf.js (eval, XFA and system fonts off, no
annotation layer so no link is ever followed, every resource from this server) with the cited
paragraph outlined and text selectable. The server-rendered page image is the fallback and the
only thing the Files 3D preview shows: no PDF is ever handed to the browser's own viewer, and
every served PDF carries `sandbox; default-src 'none'`. Other documents are plain text; a
recording plays from two seconds before the words were said.

## The 3D shelves

Folders stand as glass shelves in an arc, documents on them, an opened document's sections fan
out in reading order, and a section's passages lie on a reading plane. A search lights the route
as real decisions arrive (paced to at most three lights a second; confidence is line weight,
never a new colour); a footnote flies to its passage and outlines it. Reduced motion cuts instead
of flying. The view has a text twin (a tree). No avatar or figure appears in it.

## Security

Retrieved text is data. Every passage is fenced by a per-turn random marker with a preamble
that says so; a turn that read Library text is tainted, so an outward tool proposed after it
shows that on its card. Library tools are INTERNAL and read-only; adding, removing and
forgetting are cards decided on screen. Laya and the encoder only route inside the Library;
they never vote on an action. Documents never render as HTML: no remote resource loads from
document content (`tests/library_no_remote_loads.spec.ts`, and the chat exfil fix this builds
on).

## Privacy

Forget everywhere tombstones the document first (a folder never brings it back, and nothing can
find it from then on), deletes the index, and fingerprints its text (every run of eight words,
hashed, never stored) to remove it from every place Friday kept a copy: saved chats whether or
not they cited it (footnotes become "[forgotten source]", quotations and abridged or unquoted
copies are removed), the conversation memory's search index, the older chat history, the context
log, and the reasoning traces, live and archived (the archive stays verifiable: changed records
are re-encrypted, later lines re-signed, and a signed redaction line records how many changed,
never what). It takes what the knowledge graph learned from it (entities known only from it go;
descriptions it touched are cleared and the reports that used them dropped) and says plainly
which store it could not reach. A document on the vault shelf can be forgotten only while the
vault is unlocked, because its words must be read to be found. Remove stops indexing and
purges; the file on disk is never touched. Off the record writes no receipts and no citation rows. The knowledge
graph reads Library documents only when the owner chooses, open shelf only, always as private
text.

## Measured, and not

Synthetic labelled set (30 invented documents, 150 answerable, 20 unanswerable questions), real
encoder, this PC's CPU: evidence recall@12 1.00, top-1 precision 0.95, not-found honesty 1.00,
time to evidence p50 35 ms. The parameters were fitted on this set, so it shows the machinery
works, not how it does on real documents. **Not yet measured:** a split of real documents
(`~/.friday/library/eval/`, owner's time), Laya's wording on routing menus (`tools/library_eval.py`
reports the share of menus it answered; it was not loaded), the floor tier, and frame rate on a
real GPU.
