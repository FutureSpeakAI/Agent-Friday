# North star

`agent-friday-ideal-product-spec.md` is the target-state product specification
that Agent Friday is measured against.

**Provenance:** Astra, 2026-09-29, via Stephen. The spec is committed exactly as
it was received and is never edited in place. Changes to it go in the two
documents beside it.

How to read it together with the rest of the tree:

- **[`AMENDMENTS.md`](AMENDMENTS.md)** holds the owner's standing rulings.
  Where a ruling conflicts with the spec, the ruling wins.
- **[`GAP-MATRIX.md`](GAP-MATRIX.md)** maps each normative requirement to the
  code as it ships today. Each requirement is marked shipped, partial,
  proposed, missing, conflicting or obsolete, with evidence. The design docs
  in `docs/design/active/` count as implementations of the requirements they
  cover.
- **`docs/design/ROADMAP.md`** orders the build.
- **`docs/decisions/`** records the decisions made along the way.

The spec's claims about the current product were checked against the code
rather than accepted as written. The gap matrix records where the spec is
wrong about what exists today.
