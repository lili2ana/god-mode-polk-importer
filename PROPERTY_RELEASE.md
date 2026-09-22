# Reduced property-profile release — September 22, 2026

This release preserves all countywide PostgreSQL tables and staging rows. It builds
a private offline property profile from accepted official sources, fixes the target
refresh dependency, and seeds a private object archive without a paid upgrade.
No production dashboard read path or scoring model is switched here.

## Executed evidence

The September 21 official FTPS snapshots were downloaded with certificate checks,
stable remote size/mtime, strict decoding, exact headers and complete key checks.

| Feed | Full source rows | Retained rows | Targets covered |
| --- | ---: | ---: | ---: |
| Parcel | 438,663 | 36,713 | 36,713 |
| Owner | 678,932 | 56,335 | 36,628 |
| Legal | 1,074,392 | 97,752 | 36,713 |

The local property snapshot is 62,537,728 bytes, including lossless raw legal
records alongside the explicitly normalized description. This is not a PostgreSQL
storage measurement. All selected fields and raw legal records were reconciled
against the full input files; the property reader was exercised for all 36,713
targets. 36,628 profiles contain all three feeds; 85 require ownership review.
Even complete profiles are not automatically eligible for acquisition or outreach.

The prior production owner feed lacked 84 target owners. The new source lacks 85;
retain that additional missing-owner case for review rather than silently copying
an old owner into a supposedly current profile. Source version differences versus
the existing countywide tables are explicit:

| Feed | Unchanged target profiles | Changed target profiles | Newly missing |
| --- | ---: | ---: | ---: |
| Parcel | 36,650 | 63 | 0 |
| Owner | 36,457 | 171 | 1 |
| Legal | 36,704 | 9 | 0 |

Comparison uses all mapped source fields, ordered by canonical parcel/line key.
Owner line numbers and percentages compare by numeric value. These are source
version differences, not evidence of approved downstream behavior. The new
snapshot remains `production_verified=false` and `read_cutover_allowed=false`.

## Source archive and restore

Private Supabase Storage bucket `polk-source-archive` holds the three full ZIPs and
their source metadata. Files use feed plus SHA-256 names. All three were downloaded
again from the bucket and matched their original SHA-256 hashes and byte lengths.
Total ZIP bytes: 39,807,399. The bucket is non-public, has no anonymous/authenticated
object policies, and an unauthenticated public-object request was rejected.

This uses the existing object-storage allowance; it does not place ZIP contents in
PostgreSQL tables or change billing. Names and hashes detect replacement; the bucket
is not a WORM/object-lock service. No lifecycle deletion is installed. Seed upload
and restore were executed through the authenticated dashboard. Unattended future
uploads still require a scoped storage credential and quota monitoring; do not
claim the entire archive ingestion pipeline is scheduled yet. No credentials or
target-property data are committed to this public repository.

Fingerprints:

- Parcel: `5ec6f45d1e7b9913a3ceb94f0f08bf0a74e5a56851944d4b8eae460eaaf0548d`
- Owner: `3bade90c9a9e40bcfe70e5914eaaa1160f263ef852c2cbb64f2921286d1448b8`
- Legal: `c96ac14dc02320b5ac2b29fb33935a9d222dab7ec4c6310a5381464209ff1e98`

## Legal parsing contract

Ordinary strict CSV fails at physical line 3517 on an unescaped inch quote.
The accepted source has exactly one logical record per physical line. The first
seven fields must be quoted numeric identifiers; the eighth field is quoted free
text with possible internal quotes and commas. `property_source.py` preserves the
complete physical record and removes only the outer description wrapper plus
surrounding description whitespace for the compatibility field. Unknown prefixes,
unbalanced wrappers, invalid keys, duplicate normalized keys and unexpected
continuation lines stop the build. No line is silently appended or discarded.

## Live target refresh change

The migration replaces `god_mode_ops.refresh_target_parcels()` with a key-validating
refresh that removes obsolete derived entries for deleted/blank/changed parcel IDs,
handles key swaps, and avoids rewriting unchanged rows. Countywide tables are never
cleaned up by it. The private `refresh_market_and_targets()` calls the existing
synchronous market refresh first, checks for empty/capped results, then refreshes
targets in the same transaction. Exceptions roll everything back.

The existing daily cron now calls that wrapper at **05:17 GMT**. The former 05:27
timer is disabled, with its configuration retained. No earlier/successful cron run
is claimed for the new wrapper until one is observed. Direct target refresh was
executed after deployment: 36,713 targets, zero drift. A one-row write probe was
rolled back before deployment. RLS is enabled on private ops tables, and anonymous
function execution is denied. Security Advisor INFO notices about RLS with no
policies are intentional default-deny for these administrator-only tables.

The legacy countywide bulk workflow now only checks readiness manually. Its CLI
rejects production writes/merges; read-only verification and local staging dry
runs remain available. The legal diagnostic no longer uploads raw ZIPs to public
GitHub artifacts. Historical code remains available for reference/testing.

## Remaining production gates

1. Review the 85 ownership gaps and the measured version differences; confirm how
   dashboard/DD readers represent unknown owners and updated legal descriptions.
2. Supply an unattended, scoped archive upload identity without exposing secrets;
   enforce a storage budget and hash-verified restore checks on new versions.
3. Capacity check: the dashboard reports 1.70 GB used of 2 GB, with 160 MB WAL;
   the graph shows 88% disk use (different sampling). Bulk shadow-table writes
   remain gated. Current private SQLite profiles are a tested read model, not a
   deployed dashboard API.
4. Validate downstream behavior, then switch reads with rollback. Archive the exact
   old production snapshots before considering any later retirement of those tables.
5. Build a separately validated sales comp universe. Tax amount due alone still
   does not prove delinquency. Seller outreach stays OFF.
