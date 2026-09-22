# Property source differences and review flags

The accepted September 21 snapshot was compared with the existing production
parcel, owner and legal tables on September 22. Only the previously identified
changed keys were retrieved, read-only. Every retrieved production profile matched
its previously saved fingerprint before field comparison; no source-version drift
was silently ignored. Raw records and parcel-specific differences stay in the
private `.polk_import/property-release/source-difference-review/` directory.

## Measured differences

- **63 parcel profiles:** 57 amount-due changes, 56 taxable-value changes, 30
  assessed-value changes and 19 use-code changes. Amount due does not establish
  delinquency. Neither assessed nor taxable value establishes resale value.
- **172 owner profiles:** 171 changed plus one now missing. The old 247 rows became
  287 (64 added, 24 removed, 215 changed). Owner-name sets differ for 103 profiles,
  including the newly missing profile; this does not itself prove a transfer.
- **9 legal profiles:** 41 rows became 39, with 29 changed descriptions, four added
  rows and six removed rows. None of the 29 description changes reduces to only
  case/whitespace differences. They still require substantive review before cutover.

Among changed parcel classifications, 13 moved from split/combine to the county
label `Inaccessible tracts`, and one moved to `Mineral Rights (Not Phos.)`.
Across **all** target profiles the current snapshot contains:

| Source code | County description | Target profiles |
| --- | --- | ---: |
| 9910 | Inaccessible tracts | 8,105 |
| 9350 | Mineral Rights (Not Phos.) | 64 |
| 0989 | Split and/or Combine in Progress | 1 |

These are source classifications, not independent legal determinations. In
particular, a mapped road or complete owner/parcel/legal feed does not establish
legal access or a fee-simple acquisition opportunity. The target registry is an
inventory boundary, not an acquisition-eligible universe. Do not delete these
properties or silently treat them as ordinary residential opportunities.

## READY reader change

`PropertyProfiles.get()` now emits `source_review_flags` with the observed code,
description and a false legal-verification marker. These three classes require
review even if every feed is present. A known code/description disagreement also
requires review. Unknown or ordinary labels do not confer legal clearance or
acquisition eligibility. All profiles retain `production_verified=false` and
`eligible_for_automated_acquisition=false`.

`feed_completeness_review_required` retains the missing-feed distinction; the
broader `review_required` includes source-classification flags. The archived
manifest's 85 `profiles_requiring_review` refers to feed completeness at build
time, not a comprehensive DD count. The archive, SQLite rows, source fingerprints,
target registry and production records are unchanged by this reader update.

The private review queue is evidence for later downstream integration, not a
production hold-table deployment, source cutover or title/access clearance.
Production verification and read-cutover flags remain false. Legal differences,
ownership gaps, the target eligibility policy and remaining underwriting still
need review. Operator login and draft PR8 are independent of this change.

## Executed offline verification

All **36,713** profiles were read through the updated reader. Exactly **8,170**
had these source flags; **8,254** required review after including the **85** owner
gaps (one overlaps). Independent SQLite class counts matched the reader counts.
All acquisition and production-verification flags remained false. Before/after
SHA-256 checks matched the unchanged archived snapshot fingerprint. Four added
unit cases cover complete-but-risky profiles, mineral/split labels, classification
disagreement and the absence of automatic clearance for ordinary classes.
