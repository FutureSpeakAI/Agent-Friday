# PW-G7: files routes for the phone and tablet: design note before any code

Status: design only (BURN-DOWN, 2026-11-17). Spec: phone workspaces parity PW-G7 ("`GET /tv/v1/files?path=` within the Library's
tracked roots, `/files/raw/<id>` with Range and an 8 MiB cap per request, `POST /tv/v1/files/inbox` (a phone file into the
Library's staging)").

## What exists (checked in the code, `routes/library.py`)

* The Library is an **indexed document store**, not a folder view. A file is a document with an integer id
  (`/api/library/document/<id>`, `/tree`, `/search`, `/section/<id>`), read through a principal with grants and scopes
  (`grants`, `runtime`). "Tracked" (`/api/library/tracked`) is the owner's **one bulk consent switch** ("everything Friday
  already tracks"), not a list of roots a client can browse.
* `GET /api/library/raw/<doc_id>` serves the file with `send_file(..., conditional=True)` (so Range works), `nosniff`, a CSP,
  and `no-store` for the **vault shelf**. A document that is no longer in the Library is a 404.
* There is **no directory-listing route** and **no upload route**. `/api/library/staging` lists *proposals* (items the
  Library suggests adding); `/api/library/add` takes an existing path on the PC.
* The media routes already built (#838) have the capped-Range reader this would reuse (`pc_read` `_capped_range`,
  `MAX_FILE_CHUNK`, content type allow-list, `Content-Disposition: attachment` for anything that is not image, audio or
  video).

## Consequence for the spec's shape

`GET /tv/v1/files?path=` (a directory browser over tracked roots) does not match how the Library works, and a path-taking
route would be a new file-serving surface on the PC's disk. Proposal: **address files by Library document id, never by
path.**

## Proposal

1. `GET /tv/v1/files` (and `?shelf=&cursor=`): the Library tree/search results shaped for a device: id, title, kind, size,
   shelf, modified; **no host path** (`hide_host_paths`); `pc_read`, owner phone or tablet. Vault-shelf documents are
   listed as a generic row and never opened from a device unless the PC says so (open question below).
2. `GET /tv/v1/files/raw/<doc_id>` (`pc_read`): the desktop's own `/api/library/raw/<id>` through the same capped-Range
   code as the media routes (at most 8 MiB per request, `416` passed through, a full answer over the cap is refused, inline
   only for image/audio/video/PDF, `attachment` for the rest). The Library's own principal and shelf rules decide; a 404
   is a 404.
3. `POST /tv/v1/files/inbox` (`pc_act`): **needs a desktop route that does not exist.** Proposal: a new
   `POST /api/library/inbox` that stores the bytes in a quarantine folder inside the Library's staging area as a
   **proposed** item the owner accepts on the PC (the existing staging review), never indexed or opened before that.
   Limits: 25 MiB per file, an extension and magic-number allow-list (documents, images, audio, video, archives refused),
   no executables, no duplicate names, one pending upload per device at a time, the file name is never used as a path
   (generated id), and a `device_act` receipt names the device, size and type, never the name.

## Tests (each with a red twin)

Ids only (a path, `..`, a drive letter is refused before the desktop is asked); no host path in any answer; the Range cap and
416 pass-through; a vault-shelf document is refused; without `pc_read`/`pc_act` or from a non-owner device nothing reaches
the desktop; inbox: over the cap, a refused type, an executable renamed `.pdf` (magic number), a name with separators, a
second upload while one is pending, and a proof that nothing is indexed before the owner accepts.

## Questions for Dispatch / Stephen

* Should vault-shelf documents ever be openable on a phone (suggest: no, until the vault has a device-unlock story)?
* Is an inbox (a phone file arriving on the PC, accepted on the PC) wanted now, given it adds a new write path into the
  Library? If yes, 25 MiB and the allow-list above?
* Who owns the desktop `POST /api/library/inbox` route (Library lane), since it is a new write path into their store?
