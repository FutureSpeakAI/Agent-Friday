# Documents

Friday can make real Word, Excel and PowerPoint files (`.docx`, `.xlsx`,
`.pptx`) on your PC, with no Microsoft Office installed and nothing sent
anywhere. Ask in chat: "make a one-page brief as a Word document", "turn this
table into a spreadsheet", "build a five-slide deck".

For a presentation that opens in a browser, Friday can also build an HTML deck;
ask for "a web presentation".

## How it works

Friday uses [OfficeCLI](https://github.com/iOfficeAI/OfficeCLI), an open-source
(Apache-2.0) document engine that runs as a single program on your PC.

- Documents live in `%USERPROFILE%\.friday\documents`.
- Before saying a document is done, Friday validates it and renders a preview
  image, looks at it, and fixes what it finds (overlapping shapes, overflowing
  text, leftover placeholders).
- Text read out of a document is treated as data written by someone else,
  never as instructions to Friday.

## What needs your approval

| Action | Approval |
|---|---|
| Reading a document; creating a new one in the documents folder; editing one Friday made there | None |
| Overwriting a file that already exists | Asks first |
| Editing a document Friday did not make, or anything outside the documents folder | Asks first |
| Low-level XML editing (`raw`) | Asks first |

See [Approvals and receipts](approvals-and-receipts.md).

## Setting it up

The Windows installer does not install OfficeCLI yet. Until it does, Friday
reports that the engine is not installed when you ask for an Office file. To
enable it:

1. Download `officecli-win-x64.exe` version **1.0.152** from the OfficeCLI
   GitHub releases page.
2. Save it as `%USERPROFILE%\.friday\runtime\officecli\officecli.exe`.
3. Record its checksum next to it in
   `%USERPROFILE%\.friday\runtime\officecli\INSTALL.json`:

   ```json
   { "pinned_version": "1.0.152", "sha256": "<the file's SHA-256, lower case>" }
   ```

   In PowerShell, `(Get-FileHash officecli.exe -Algorithm SHA256).Hash.ToLower()`
   prints it.

Friday checks the file against that checksum before running it and refuses a
binary that does not match. The checksum detects a damaged or swapped file; it
cannot tell you the download was genuine, so download it only from the
project's own releases page. OfficeCLI's own update check is switched off every
time Friday runs it.

## One network exception

When no local Office is installed and a document uses diagrams, equations, 3D
models or web fonts, OfficeCLI's preview renderer loads Mermaid, KaTeX,
three.js or the fonts from public CDNs. That request is made by OfficeCLI, not
Friday, so it does not pass Friday's egress gate. It carries no document text;
a web-font request names the fonts the document uses.
