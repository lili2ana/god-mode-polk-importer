# Offline sales candidate screen

`build_sales_candidate_snapshot.py` builds a private SQLite review snapshot from
an accepted complete sales ZIP and complete current parcel ZIP. It never connects
to PostgreSQL or enables a production loader. This is a candidate screen, not a
valuation model or a verified comps feed.

The builder validates every sales row, including historical rows outside the
retention window, and rejects duplicate canonical `(parcel, line)` keys. Both
archives require accepted SHA-256 fingerprints and row counts. It retains copies
of the complete archives, verifies their hashes again after processing, and
rejects an existing output directory. A failed build leaves a BLOCKED manifest;
only a completed build publishes `sales_candidates.sqlite` with a READY manifest.
Raw archives, parcel-level results and operator exports belong in ignored private
storage, never GitHub or public CI artifacts.

## Retention and screening are separate

All county sales in the explicit inclusive date window are retained, including
non-target parcels and records held for review. Target membership is an annotation,
not a comp filter. The target export must have verified registry provenance and be
no more than 24 hours old at both the beginning and end of a new build. Properties
without sales are valid; the builder does not require every target to have sold.
Undated, older and post-as-of rows are counted separately and remain in the archive.
An accepted historical snapshot is not a claim that the source is current today.

The versioned `polk-sales-candidate-screen-v2` contract holds records when:

- The observed qualification code is not exactly 01/02, or its description differs
  from the accepted observed description. A `Q-` prefix is insufficient.
- Price is missing or at most $100. This threshold is a screening heuristic;
  a higher price is not proof of market value or an arm's-length transaction.
- Sale type is outside I/V, or the foreclosure flag is anything other than N.
  N does not establish the property's current distress status.
- Recording references or sale IDs are absent or are not positive ASCII integers.
- A recording reference repeats anywhere in the complete accepted sales history.
  Multiple-parcel recording groups have an additional explicit reason.
- The current parcel is missing, its classification is incomplete, or it carries
  one of the source-risk flags documented in `PROFILE_SOURCE_REVIEW.md`.

Reference grouping strips surrounding spaces and leading zeroes from positive
numeric fields; raw fields remain in the normalized payload and full archive.
This is deliberately conservative recording-reference collision detection.
Repeated references can be corrections or reused identifiers; they are not
verified package/deed identity. SALE_ID is demonstrably not globally unique:
its reuse is recorded in `identity_observations`, not treated as proof of a package
sale or as a standalone reason to hold every affected record. Never join, dedupe
or allocate prices using SALE_ID alone. Version 1 of the private experiment held
every window row after treating global SALE_ID reuse as a screening hold; version
2 corrects that unsupported interpretation while preserving explicit uncertainty.
There is no price allocation, summing of package prices, or inferred replacement
parcel. A collision outside the retention window still holds an affected window
row. The full-history scratch index is removed only from the newly created private
output directory; original inputs and existing snapshots are preserved.

Records passing this screen are `candidate_for_comparability_review`, never
qualified comps. Every row requires review and has `qualified_comp=false` and
`eligible_for_automated_acquisition=false`. The manifest also keeps
`qualified_comps`, `production_verified` and `read_cutover_allowed` false.

## What remains before underwriting

The current parcel classification is source context, not sale-date classification.
Historical Polk export semantics, transaction identity, sale-date attributes,
subject geography/type/similarity, adjustments and a supported confidence model
remain unverified. This screen does not choose a residential-only universe and does
not interpret assessed value, tax amounts or source class labels as legal findings.
Countywide and staging production tables, existing DD jobs and outreach stay
unchanged. Actual read integration and archive automation are separate releases.

Official references informing the prior investigation are the Florida DOR
[pre-2026 transfer codes](https://www.floridarevenue.com/property/Documents/salequalcodes_bef01012026.pdf),
[2026 transfer codes](https://www.floridarevenue.com/property/Documents/salequalcodes_aft01012026.pdf),
and [2026 SDF field summary](https://www.floridarevenue.com/property/Documents/2026SDFSummaryTable.pdf).
Polk's 15-field export is not the 14-field DOR SDF. These references do not establish
every historical export's semantics; the remaining-review flags reflect that gap.

## Local use

Run `python build_sales_candidate_snapshot.py --help` for required archive paths,
accepted fingerprints/counts, target export, new output directory and explicit
start/as-of dates. Do not pass database credentials. Inspect `manifest.json` and
independently reconcile retained keys and payloads before using the local snapshot.
READY here means the offline artifact completed validation, not production access.
