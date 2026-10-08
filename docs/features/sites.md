# Sites and domains

Sites connects an existing repository to its saved builds, preview, publication
and domain. The Sites workspace, chat and voice use the same operations and
approval records. A site belongs to one conversation and its current project;
moving or archiving that owner invalidates earlier action authority.

## From repository to website

Choose an existing managed codebase, a build root, an explicit build command
and a static output directory. A build review captures the source files and
command. Approval runs that captured source separately from the checkout.
This is a host process with bounded runtime and output, not an operating-system
sandbox. Provider credentials and the user's configuration do not enter its
environment.

Select a successful saved build for a layout preview. The panel serves its
frozen HTML, styles, images and fonts from a separate temporary origin inside
an opaque frame. JavaScript does not run: JavaScript interactions, dynamic
data loading and WebAssembly execution are unavailable. Applications that
depend on JavaScript may appear incomplete or blank. This view checks static
layout, not application behavior, and is not a general browser network sandbox.
Browser storage, service workers, forms and popups remain unavailable. Close,
expiry, changed ownership or a privacy transition invalidates the preview;
opening it again uses the same saved build. A preview panel acknowledgement
does not certify that the application rendered correctly.

Publishing has a separate review for the exact build and hosting destination.
Saved Sites supports GitHub Pages. Cloudflare Pages and applications that
require a running backend need another hosting adapter. An earlier build can
be selected and reviewed for publication again; a failed new build does not
remove an existing deployment.

Publication history distinguishes an accepted upload, host readiness and a
verified live page. Verification checks one public HTTPS endpoint for the
expected deployment marker and hostname certificate. Repository cleanliness,
a pushed commit and a successful upload do not establish a live website.
An uncertain write remains uncertain until read-back; it is never replayed
automatically.

## Name.com accounts

Keep each registrar account under a separate name. A Google sign-in email is
not necessarily the Name.com API username. Enter credentials in the local
account form; chat and voice do not accept them. An account can also track
an imported inventory before it is connected.

Imported renewal dates retain their source and their original **Renews** or
**Expires** meaning. They stay unverified until authenticated synchronization.
An auto-renew setting, a renewal date and a completed paid renewal are separate
facts. No automatic renewal or reminder is created by importing an inventory.

Bind a site to an exact account, domain and hostname. A project conversation
can act on that binding; account-wide inventory management belongs in Sites
or an unfiled conversation. DNS changes review one exact record, preserve
unrelated records, and require the domain to use Name.com's DNS authority.

Auto-renew changes require their own review, including the possibility of
future registrar charges. Paid renewal is prepared for Name.com checkout,
where the owner reviews the final total and completes payment. A quoted
subtotal does not establish tax or the final charge. Purchases, transfers,
nameserver changes, DNSSEC, contacts and registrar locks are outside these
tools.

DNS verification records one public resolver observation. It does not prove
global propagation, DNSSEC or a valid HTTPS deployment.

## Chat and voice

Use `site_action` to list, inspect, open, save, build, preview, prepare
publication, read hosting requirements and check an operation. Use
`domain_action` to inspect the chosen account and inventory, read DNS,
prepare a record or auto-renew change, prepare renewal checkout and reconcile
an uncertain result.

For example: “Open this site's latest successful build,” “Prepare publication
of this saved build,” or “Show the DNS change needed for this site's domain.”
Friday resolves the current identities, opens the same workspace and presents
the same exact review. Background delegates cannot acquire repository or
registrar authority through these actions. A delayed action retains its
original privacy authority; ending that context requires a fresh request.
