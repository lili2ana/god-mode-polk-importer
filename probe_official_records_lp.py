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


def click_visible_exact(page, text):
    loc=page.get_by_text(text, exact=True)
    for i in range(loc.count()):
        x=loc.nth(i)
        try:
            if x.is_visible():
                x.click(); return True
        except Exception:
            pass
    return False


def write_report(report):
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')


def main():
    report={'from':FROM_DATE,'to':TO_DATE,'doc_types':DOC_TYPES,'status':'started'}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1500,'height':1000})
            response=page.goto(BASE, wait_until='domcontentloaded', timeout=30000)
            report['http_status']=response.status if response else None
            report['final_url']=page.url
            report['page_title']=page.title()
            print('HTTP STATUS:', report['http_status'])
            print('FINAL URL:', report['final_url'])
            print('PAGE TITLE:', report['page_title'])
            page.screenshot(path=str(SHOT), full_page=True)
            body0=clean(page.locator('body').inner_text())
            report['initial_body_excerpt']=body0[:5000]
            print('INITIAL BODY EXCERPT:', body0[:1000])

            block_terms=('access denied','attention required','captcha','verify you are human','request blocked','incapsula','cloudflare','akamai')
            hay=(report['page_title']+' '+body0).lower()
            matched=[t for t in block_terms if t in hay]
            if matched:
                report['status']='blocked'
                report['block_indicators']=matched
                raise RuntimeError(f'Probable bot/access block detected: {matched}')

            report['visible_links_initial']=[clean(x.inner_text()) for x in page.locator('a:visible').all()]
            if not click_visible_exact(page,'Search'):
                raise RuntimeError('Visible Search tab not found')
            page.wait_for_timeout(300)
            if not click_visible_exact(page,'Document Type'):
                raise RuntimeError('Visible Document Type tab not found')
            page.wait_for_timeout(700)

            report['inputs']=[{'placeholder':x.get_attribute('placeholder'),'name':x.get_attribute('name'),'type':x.get_attribute('type')} for x in page.locator('input:visible').all()]
            report['buttons']=[clean(x.inner_text()) for x in page.locator('button:visible').all()]

            visible_inputs=page.locator('input:visible')
            doc_input=from_input=to_input=None
            date_inputs=[]
            for i in range(visible_inputs.count()):
                el=visible_inputs.nth(i)
                ph=(el.get_attribute('placeholder') or '').lower()
                typ=(el.get_attribute('type') or 'text').lower()
                if typ=='checkbox': continue
                if ('document type' in ph) and ('search document' not in ph) and doc_input is None:
                    doc_input=el
                if 'mm/dd/yyyy' in ph:
                    date_inputs.append(el)
            if len(date_inputs)>=2:
                from_input,to_input=date_inputs[0],date_inputs[1]
            if doc_input is None:
                text_inputs=[visible_inputs.nth(i) for i in range(visible_inputs.count()) if (visible_inputs.nth(i).get_attribute('type') or 'text').lower()!='checkbox']
                if text_inputs: doc_input=text_inputs[0]
            if not (doc_input and from_input and to_input):
                raise RuntimeError(f'Could not identify search inputs; visible={report["inputs"]}')

            doc_input.fill(','.join(DOC_TYPES))
            from_input.fill(FROM_DATE)
            to_input.fill(TO_DATE)
            searches=page.get_by_role('button',name='Search',exact=True)
            clicked=False
            for i in range(searches.count()):
                b=searches.nth(i)
                if b.is_visible():
                    b.click(); clicked=True; break
            if not clicked:
                raise RuntimeError('Visible Search button not found')
            page.wait_for_timeout(1600)
            page.screenshot(path=str(SHOT),full_page=True)
            body=page.locator('body').inner_text()
            report['results_body_excerpt']=clean(body)[:15000]
            mt=re.search(r'\((\d+) total\) records',body,re.I)
            if mt: report['total_party_rows']=int(mt.group(1))

            party_rows=[]
            for v in page.get_by_text('View',exact=True).all():
                try:
                    if v.is_visible(): party_rows.append(clean(v.locator('xpath=ancestor::tr[1]').inner_text()))
                except Exception: pass
            report['party_rows_first_page']=party_rows
            report['view_count_first_page']=len(party_rows)

            documents=[]
            for idx in range(min(30,len(party_rows))):
                views=[v for v in page.get_by_text('View',exact=True).all() if v.is_visible()]
                if idx>=len(views): break
                rowtxt=''
                try: rowtxt=clean(views[idx].locator('xpath=ancestor::tr[1]').inner_text())
                except Exception: pass
                views[idx].click(); page.wait_for_timeout(600)
                txt=page.locator('body').inner_text()
                data={'result_row':rowtxt}
                for label,key in [('Type','type'),('File No.','file_no'),('Date','date'),('Book/Page','book_page'),('Legal','legal'),('District','district'),('Map','map'),('Sub Map','sub_map'),('Parcel','parcel'),('Sub Parcel','sub_parcel')]:
                    m=re.search(rf'^{re.escape(label)}\s*:\s*(.*)$',txt,re.I|re.M)
                    data[key]=clean(m.group(1)) if m else ''
                data['linked_button_candidates']=[clean(b.inner_text()) for b in page.locator('button:visible').all() if re.search(r'\b\d{10}\b',clean(b.inner_text()))]
                if data.get('file_no') and not any(d.get('file_no')==data['file_no'] for d in documents):
                    documents.append(data)
                if not click_visible_exact(page,'Results'):
                    raise RuntimeError('Could not return to Results tab')
                page.wait_for_timeout(350)
            report['documents_sample']=documents
            report['unique_sample_file_nos']=len({d['file_no'] for d in documents if d.get('file_no')})
            report['sample_with_direct_parcel_legal']=sum(bool(re.fullmatch(r'\d{2}-\d{2}-\d{2}-\d{6}-\d{6}',d.get('legal',''))) for d in documents)
            report['sample_with_linked_buttons']=sum(bool(d.get('linked_button_candidates')) for d in documents)
            report['status']='completed'
            write_report(report)
            print(json.dumps(report,indent=2))
            browser.close()
    except Exception as e:
        report['error']=f'{type(e).__name__}: {e}'
        if report.get('status')=='started': report['status']='failed'
        write_report(report)
        print(json.dumps(report,indent=2))
        raise

if __name__=='__main__':
    main()
