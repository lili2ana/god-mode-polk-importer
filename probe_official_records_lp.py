#!/usr/bin/env python3
import json, re
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE='https://apps.polkcountyclerk.net/browserviewor/'
FROM_DATE='08/30/2026'
TO_DATE='09/05/2026'
DOC_TYPES=['LP','L PEN']
OUT=Path('official_records_lp_probe.json')
SHOT=Path('official_records_results.png')


def clean(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()


def log(*args):
    print(*args, flush=True)


def click_visible_exact(page, text, timeout=5000):
    loc=page.get_by_text(text, exact=True)
    for i in range(loc.count()):
        x=loc.nth(i)
        try:
            if x.is_visible():
                x.click(timeout=timeout)
                return True
        except Exception as e:
            log(f'CLICK WARNING [{text}] #{i}: {type(e).__name__}: {e}')
    return False


def write_report(report):
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')


def parse_result_rows(body):
    text=clean(body)
    marker='Name Date Type Book Page Legal File# Status Flag View'
    if marker in text:
        text=text.split(marker,1)[1]
    footer=re.search(r'\s+Retrieved records\b', text, re.I)
    if footer:
        text=text[:footer.start()]

    row_re=re.compile(
        r'(?P<name>.+?)\s+'
        r'(?P<date>\d{2}/\d{2}/\d{4})\s+'
        r'(?P<type>LP|L PEN)\s+'
        r'(?P<book>\d+)\s+'
        r'(?P<page>\d+)\s+'
        r'(?P<legal>.+?)\s+'
        r'(?P<file_no>\d{10})\s+'
        r'(?P<status>[A-Z])'
        r'(?:\s+(?P<flag>[A-Z]))?\s+View'
        r'(?=\s+.+?\s+\d{2}/\d{2}/\d{4}\s+(?:LP|L PEN)\s+\d+\s+\d+|$)',
        re.I,
    )
    rows=[]
    for m in row_re.finditer(text):
        name=clean(m.group('name'))
        from_party=name.startswith('*')
        name=name.lstrip('*').strip()
        legal=clean(m.group('legal'))
        parcel_id=legal if re.fullmatch(r'\d{2}-\d{2}-\d{2}-\d{6}-\d{6}',legal) else None
        rows.append({
            'name':name,
            'from_party':from_party,
            'date':m.group('date'),
            'type':m.group('type').upper(),
            'book':m.group('book'),
            'page':m.group('page'),
            'legal':legal,
            'file_no':m.group('file_no'),
            'status':m.group('status').upper(),
            'flag':(m.group('flag') or '').upper(),
            'parcel_id':parcel_id,
        })
    return rows


def main():
    report={'from':FROM_DATE,'to':TO_DATE,'doc_types':DOC_TYPES,'status':'started'}
    try:
        with sync_playwright() as p:
            log('STAGE: launch browser')
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1500,'height':1000})
            page.set_default_timeout(5000)
            page.set_default_navigation_timeout(30000)

            log('STAGE: initial navigation', BASE)
            response=page.goto(BASE, wait_until='domcontentloaded', timeout=30000)
            report['http_status']=response.status if response else None
            report['final_url']=page.url
            report['page_title']=page.title()
            log('HTTP STATUS:', report['http_status'])
            log('FINAL URL:', report['final_url'])
            log('PAGE TITLE:', report['page_title'])
            page.screenshot(path=str(SHOT), full_page=True)
            body0=clean(page.locator('body').inner_text(timeout=5000))
            report['initial_body_excerpt']=body0[:5000]
            log('INITIAL BODY EXCERPT:', body0[:1000])

            block_terms=('access denied','attention required','captcha','verify you are human','request blocked','incapsula','cloudflare','akamai')
            hay=(report['page_title']+' '+body0).lower()
            matched=[t for t in block_terms if t in hay]
            if matched:
                report['status']='blocked'
                report['block_indicators']=matched
                raise RuntimeError(f'Probable bot/access block detected: {matched}')

            report['visible_links_initial']=[clean(x.inner_text(timeout=3000)) for x in page.locator('a:visible').all()]
            log('STAGE: open Search tab')
            if not click_visible_exact(page,'Search'):
                raise RuntimeError('Visible Search tab not found')
            page.wait_for_timeout(300)
            log('STAGE: open Document Type tab')
            if not click_visible_exact(page,'Document Type'):
                raise RuntimeError('Visible Document Type tab not found')
            page.wait_for_timeout(700)

            report['inputs']=[{'placeholder':x.get_attribute('placeholder'),'name':x.get_attribute('name'),'type':x.get_attribute('type')} for x in page.locator('input:visible').all()]
            report['buttons']=[clean(x.inner_text(timeout=3000)) for x in page.locator('button:visible').all()]

            visible_inputs=page.locator('input:visible')
            doc_input=from_input=to_input=None
            date_inputs=[]
            for i in range(visible_inputs.count()):
                el=visible_inputs.nth(i)
                ph=(el.get_attribute('placeholder') or '').lower()
                typ=(el.get_attribute('type') or 'text').lower()
                if typ=='checkbox':
                    continue
                if ('document type' in ph) and ('search document' not in ph) and doc_input is None:
                    doc_input=el
                if 'mm/dd/yyyy' in ph:
                    date_inputs.append(el)
            if len(date_inputs)>=2:
                from_input,to_input=date_inputs[0],date_inputs[1]
            if doc_input is None:
                text_inputs=[visible_inputs.nth(i) for i in range(visible_inputs.count()) if (visible_inputs.nth(i).get_attribute('type') or 'text').lower()!='checkbox']
                if text_inputs:
                    doc_input=text_inputs[0]
            if not (doc_input and from_input and to_input):
                raise RuntimeError(f'Could not identify search inputs; visible={report["inputs"]}')

            log('STAGE: submit search', DOC_TYPES, FROM_DATE, TO_DATE)
            doc_input.fill(','.join(DOC_TYPES), timeout=5000)
            from_input.fill(FROM_DATE, timeout=5000)
            to_input.fill(TO_DATE, timeout=5000)
            searches=page.get_by_role('button',name='Search',exact=True)
            clicked=False
            for i in range(searches.count()):
                b=searches.nth(i)
                if b.is_visible():
                    b.click(timeout=5000)
                    clicked=True
                    break
            if not clicked:
                raise RuntimeError('Visible Search button not found')

            page.wait_for_timeout(1600)
            page.screenshot(path=str(SHOT),full_page=True)
            body=page.locator('body').inner_text(timeout=5000)
            clean_body=clean(body)
            report['results_body_excerpt']=clean_body[:15000]
            mt=re.search(r'\((\d+) total\) records',body,re.I)
            if mt:
                report['total_party_rows']=int(mt.group(1))
            log('STAGE: results loaded; reported total rows =', report.get('total_party_rows'))

            rows=parse_result_rows(body)
            report['parsed_party_rows']=rows
            report['parsed_party_row_count']=len(rows)
            unique_documents={}
            for row in rows:
                unique_documents.setdefault(row['file_no'],row)
            report['documents']=list(unique_documents.values())
            report['unique_document_count']=len(unique_documents)
            report['direct_parcel_rows']=[r for r in rows if r['parcel_id']]
            report['direct_parcel_row_count']=len(report['direct_parcel_rows'])
            report['direct_parcel_ids']=sorted({r['parcel_id'] for r in rows if r['parcel_id']})
            report['direct_parcel_unique_count']=len(report['direct_parcel_ids'])
            report['parse_matches_reported_total']=(report.get('total_party_rows')==len(rows)) if report.get('total_party_rows') is not None else None

            log('STAGE: parsed party rows =', len(rows))
            log('STAGE: unique documents =', len(unique_documents))
            log('STAGE: direct parcel-linked rows =', report['direct_parcel_row_count'])
            log('STAGE: direct unique parcels =', report['direct_parcel_unique_count'])
            if report.get('total_party_rows') is not None and len(rows)!=report['total_party_rows']:
                raise RuntimeError(f'Parser row count mismatch: parsed={len(rows)} reported={report["total_party_rows"]}')

            report['status']='completed'
            write_report(report)
            log('STAGE: completed')
            log(json.dumps(report,indent=2))
            browser.close()
    except Exception as e:
        report['error']=f'{type(e).__name__}: {e}'
        if report.get('status')=='started':
            report['status']='failed'
        write_report(report)
        log(json.dumps(report,indent=2))
        raise

if __name__=='__main__':
    main()
