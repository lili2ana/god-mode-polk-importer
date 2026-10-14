# Lee free-source evidence intake

Verified 2026-10-14. No paid bulk agreement was accepted.

The Lee Tax Collector links its property account and report tools to
https://county-taxes.net/fl-lee/reports . Select Real Estate, search for
Delinquent Real Property, then set Tax Yr=2024, Property Address=LEHIGH,
Balance Status=Unpaid, and run Search.

The live report returned 3,745 rows. Four public pages (1,000/1,000/1,000/745)
were collected and reconciled against the reported total. The browser export
reported a download path but the file was unavailable; the saved snapshot is
the complete rendered table collection, not a downloaded CSV. Snapshot bytes
are private and are not committed to this public repository.

Run `python ops/lee_tax_report.py SNAPSHOT.json --output CANDIDATES.json`.
The normalizer validates source/county, report count, row layout, parcel IDs,
certificate number, issued status, no deed application, unpaid positive finite
balance, and delinquency strictly older than 12 months. Duplicate certificate
identities are quarantined, rather than letting one conflicting row win.

Live result: 3,376 candidates, of which 3,084 have use code 00; 346 non-issued
rows excluded and 23 duplicate-certificate rows quarantined. Every candidate
remains REVIEW_REQUIRED with qualifying_distress=false. Report owner is not
treated as a verified current owner; release_search_complete remains false.
These candidates are not imported into the active qualification queue yet.
Next ingestion must join exact parcel and current owner, reconcile account and
certificate status, and screen GIS/comps before guarded lead creation.

1102 Floyd Ave S / 334426L3030160020 was absent from this specific 2024 Lehigh
report and from the tested exact tax-account lookup. This is not a certified
negative search or proof of payment. The parcel remains held.

Free Clerk individual viewing/printing is documented at
https://www.leeclerk.org/i-want-to/request/copies-of-records . The cloud browser
opened LandMarkWeb but received an Akamai Access Denied at the search page;
one reload also failed. No safeguard bypass was attempted. Plat request
W114391-101326 remains the fallback for PB15 PG100. The existing document OCR
intake can process an authorized downloaded or supplied copy; no plat was
retrieved in this run.

The Clerk lists a public Foreclosure Registry Weekly Report, but its document
bytes and layout were not retrieved or integrated. LCPA parcel fabric returned
the exact subject legal description and a Clerk document pointer; the document
pointer returned an empty body. Its older subdivision/dimension service returned
HTTP502. None of these results proves exact recorded frontage or legal access.

Validation: six tax intake tests plus twelve existing evidence pipeline tests
passed locally. No outreach, purchase, lead promotion, or qualification release.
