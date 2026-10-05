# DD operational diagnosis — October 5, 2026

## Verified live

51 reviews: 50 qualification_hold and one qualified Lake Wales residential property in review_required. The 50 older reviews are not a pool of qualified seller leads. Seller DD eligibility requires fresh matched lis pendens evidence, owner/parcel/legal corroboration and a subsequent-release check.

Deployed DD worker version 9 removes queries to nonexistent NWI spatial layers. Internal request 1000040 reran one qualified review. Wetlands returned a successful point no-hit, zero failed layers, source_error=false; FEMA source_error=false. Neither proves full-parcel clearance. Comps still returned source_error=true. No acquisition/enrichment clearance was granted.

Mocked full-handler tests cover one NWI spatial query, successful empty response versus outage, and preserved review_required/underwriting hold.

## Actual remaining blockers

1. Comparable sales source fails; current nearby-sale median has no date/type/transaction qualification and cannot establish ARV. Recent-sales shadow is incomplete and not a released comp feed.
2. Legal title and recorded ingress/easement evidence have no automated clearance. PA owner-name matching and nearby road geometry cannot replace these records.
3. Future land use CITY identifies municipal jurisdiction, not parcel zoning. Lake Wales municipal zoning evidence is required for that property.
4. PA AMTDUE is context, not tax-collector payment/delinquency clearance. Utilities service areas are context, not proof of connection/capacity.
5. Qualified review has no buyer_matches exit path. Existing buyer relationships must be reconciled before creating new outreach.
6. Costs and qualified resale estimate are unavailable, so no supported MAO exists.
7. DD worker and finalizer hardcode status=review_required, dd_score=0, and pending underwriting. There is no evidence-complete evaluator that advances reviews. Seller enrichment gate requires completed status and explicit passed/verified fields, so these workers cannot release enrichment even if some source calls succeed.

## Smallest completion path

Keep the 50 unqualified reviews held. Complete source-specific evidence for the one candidate: qualify comps; collect official title/access/tax evidence; route CITY zoning to its jurisdiction; screen the whole parcel for flood/wetlands; verify utilities for proposed use; match an existing buyer; enter complete costs. Implement and test a completion evaluator against these explicit facts, freshness and fatal flags, including missing/error/expired/contradictory evidence tests. Do not loosen pass requirements or manufacture cleared values to obtain a completed review.

Contacts and skip tracing remain outside this work. Full legal/transaction review remains necessary before releasing offers/contracts.
