#!/usr/bin/env python3
import json
import os
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

REPORT = Path('official_records_lp_probe.json')
SOURCE_NAME = 'Polk County Clerk Official Records'
SOURCE_URL = 'https://apps.polkcountyclerk.net/browserviewor/'


def parse_date(value):
    return datetime.strptime(value, '%m/%d/%Y').date()


def main():
    if not REPORT.exists():
        raise SystemExit('official_records_lp_probe.json is missing')
    report = json.loads(REPORT.read_text(encoding='utf-8'))
    if report.get('status') != 'completed':
        raise SystemExit(f"probe did not complete successfully: {report.get('status')}")

    rows = report.get('parsed_party_rows') or []
    reported = report.get('total_party_rows')
    if not rows:
        raise SystemExit('probe produced zero parsed rows')
    # Current Polk UI can expose one additional row only through captured JSON/network data.
    # Persist only structured rows we actually parsed; never fabricate the missing row.
    if reported is not None and len(rows) < reported - 1:
        raise SystemExit(f'insufficient structured coverage: parsed={len(rows)} reported={reported}')

    docs = {}
    for row in rows:
        file_no = row['file_no']
        current = docs.get(file_no)
        if current is None:
            current = dict(row)
            docs[file_no] = current
        if row.get('parcel_id') and not current.get('parcel_id'):
            current['parcel_id'] = row['parcel_id']
        if row.get('legal') and not current.get('legal'):
            current['legal'] = row['legal']

    conn = psycopg2.connect(os.environ['SUPABASE_DB_URL'])
    try:
        with conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        '''
                        insert into public.official_records_lp_party_stage
                          (file_no, party_name, from_party, recording_date, doc_type, book, page, legal,
                           status, flag, direct_parcel_id, source_url, raw_data, last_seen_at)
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                        on conflict (file_no, party_name) do update set
                          from_party=excluded.from_party,
                          recording_date=excluded.recording_date,
                          doc_type=excluded.doc_type,
                          book=excluded.book,
                          page=excluded.page,
                          legal=excluded.legal,
                          status=excluded.status,
                          flag=excluded.flag,
                          direct_parcel_id=excluded.direct_parcel_id,
                          raw_data=excluded.raw_data,
                          last_seen_at=now()
                        ''',
                        (
                            row['file_no'], row['name'], bool(row.get('from_party')), parse_date(row['date']),
                            row['type'], row.get('book'), row.get('page'), row.get('legal'), row.get('status'),
                            row.get('flag') or None, row.get('parcel_id'), SOURCE_URL, Json(row),
                        ),
                    )

                for doc in docs.values():
                    cur.execute(
                        '''
                        insert into public.official_records_lp_documents
                          (file_no, recording_date, doc_type, book, page, legal, status, flag,
                           direct_parcel_id, source_url, raw_data, last_seen_at)
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                        on conflict (file_no) do update set
                          recording_date=excluded.recording_date,
                          doc_type=excluded.doc_type,
                          book=excluded.book,
                          page=excluded.page,
                          legal=excluded.legal,
                          status=excluded.status,
                          flag=excluded.flag,
                          direct_parcel_id=coalesce(excluded.direct_parcel_id, official_records_lp_documents.direct_parcel_id),
                          raw_data=excluded.raw_data,
                          last_seen_at=now()
                        ''',
                        (
                            doc['file_no'], parse_date(doc['date']), doc['type'], doc.get('book'), doc.get('page'),
                            doc.get('legal'), doc.get('status'), doc.get('flag') or None, doc.get('parcel_id'),
                            SOURCE_URL, Json(doc),
                        ),
                    )

                cur.execute(
                    '''
                    update public.official_records_lp_documents d
                    set matched_property_id=p.id,
                        resolution_method='direct_parcel_id',
                        resolution_confidence=1.0
                    from public.properties p
                    where d.direct_parcel_id is not null
                      and p.parcel_id=d.direct_parcel_id
                      and (d.matched_property_id is distinct from p.id
                           or d.resolution_method is distinct from 'direct_parcel_id'
                           or d.resolution_confidence is distinct from 1.0)
                    '''
                )
                exact_resolved = cur.rowcount

                cur.execute(
                    '''
                    insert into public.property_distress_signals
                      (property_id, parcel_id, signal_type, signal_status, source_name, source_url,
                       source_record_id, event_date, confidence, raw_data, verified_at, last_seen_at)
                    select d.matched_property_id, d.direct_parcel_id, 'lis_pendens', 'observed', %s, d.source_url,
                           d.file_no, d.recording_date, 1.0,
                           jsonb_build_object('document', d.raw_data, 'resolution_method', d.resolution_method),
                           now(), now()
                    from public.official_records_lp_documents d
                    where d.matched_property_id is not null
                      and d.resolution_method='direct_parcel_id'
                      and d.resolution_confidence=1.0
                    on conflict (signal_type, source_name, source_record_id) do update set
                      property_id=excluded.property_id,
                      parcel_id=excluded.parcel_id,
                      signal_status=excluded.signal_status,
                      event_date=excluded.event_date,
                      confidence=excluded.confidence,
                      raw_data=excluded.raw_data,
                      verified_at=excluded.verified_at,
                      last_seen_at=now()
                    ''',
                    (SOURCE_NAME,),
                )
                signal_upserts = cur.rowcount

                cur.execute('select count(*) from public.official_records_lp_party_stage')
                party_count = cur.fetchone()[0]
                cur.execute('select count(*) from public.official_records_lp_documents')
                document_count = cur.fetchone()[0]
                cur.execute("select count(*) from public.official_records_lp_documents where resolution_method='direct_parcel_id' and resolution_confidence=1.0")
                exact_count = cur.fetchone()[0]
                cur.execute("select count(*) from public.property_distress_signals where signal_type='lis_pendens' and source_name=%s", (SOURCE_NAME,))
                signal_count = cur.fetchone()[0]

        print(json.dumps({
            'parsed_rows_input': len(rows),
            'reported_rows': reported,
            'unique_documents_input': len(docs),
            'party_stage_total': party_count,
            'document_total': document_count,
            'exact_resolved_total': exact_count,
            'exact_resolved_changed_this_run': exact_resolved,
            'lis_pendens_signals_total': signal_count,
            'signal_upserts_this_run': signal_upserts,
            'scores_modified': False,
            'outreach_modified': False,
        }, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
