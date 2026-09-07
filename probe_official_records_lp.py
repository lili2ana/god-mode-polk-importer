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

            log('STAGE: submit search', DOC_TYPES, FROM_DATE, TO_DATE)
            doc_input.fill(','.join(DOC_TYPES), timeout=5000)
            from_input.fill(FROM_DATE, timeout=5000)
            to_input.fill(TO_DATE, timeout=5000)
            searches=page.get_by_role('button',name='Search',exact=True)
            clicked=False
            for i in range(searches.count()):
                b=searches.nth(i)
                if b.is_visible():
                    b.click(timeout=5000); clicked=True; break
            if not clicked:
                raise RuntimeError('Visible Search button not found')
            page.wait_for_timeout(1600)
            page.screenshot(path=str(SHOT),full_page=True)
            body=page.locator('body').inner_text(timeout=5000)
            report['results_body_excerpt']=clean(body)[:15000]
            mt=re.search(r'\((\d+) total\) records',body,re.I)
            if mt: report['total_party_rows']=int(mt.group(1))
            log('STAGE: results loaded; total rows =', report.get('total_party_rows'))

            party_rows=[]
            for v in page.get_by_text('View',exact=True).all():
                try:
                    if v.is_visible(): party_rows.append(clean(v.locator('xpath=ancestor::tr[1]').inner_text(timeout=3000)))
                except Exception as e:
                    log('ROW CAPTURE WARNING:', type(e).__name__, e)
            report['party_rows_first_page']=party_rows
            report['view_count_first_page']=len(party_rows)
            log('STAGE: visible result rows captured =', len(party_rows))

            documents=[]
            detail_errors=[]
            sample_limit=min(10,len(party_rows))
            log('STAGE: detail sampling; limit =', sample_limit)
            for idx in range(sample_limit):
                log(f'DETAIL {idx+1}/{sample_limit}: locating View link')
                try:
                    views=[v for v in page.get_by_text('View',exact=True).all() if v.is_visible()]
                    if idx>=len(views):
                        detail_errors.append({'index':idx,'error':'View link index unavailable'})
                        break
                    rowtxt=''
                    try:
                        rowtxt=clean(views[idx].locator('xpath=ancestor::tr[1]').inner_text(timeout=3000))
                    except Exception as e:
                        log(f'DETAIL {idx+1}: row text warning:', type(e).__name__, e)
                    log(f'DETAIL {idx+1}: clicking', rowtxt[:160])
                    views[idx].click(timeout=5000)
                    page.wait_for_timeout(500)
                    txt=page.locator('body').inner_text(timeout=5000)
                    data={'result_row':rowtxt}
                    for label,key in [('Type','type'),('File No.','file_no'),('Date','date'),('Book/Page','book_page'),('Legal','legal'),('District','district'),('Map','map'),('Sub Map','sub_map'),('Parcel','parcel'),('Sub Parcel','sub_parcel')]:
                        m=re.search(rf'^{re.escape(label)}\s*:\s*(.*)$',txt,re.I|re.M)
                        data[key]=clean(m.group(1)) if m else ''
                    data['linked_button_candidates']=[clean(b.inner_text(timeout=2000)) for b in page.locator('button:visible').all() if re.search(r'\b\d{10}\b',clean(b.inner_text(timeout=2000)))]
                    if data.get('file_no') and not any(d.get('file_no')==data['file_no'] for d in documents):
                        documents.append(data)
                    log(f'DETAIL {idx+1}: file_no={data.get("file_no")!r} legal={data.get("legal")!r}')
                except Exception as e:
                    detail_errors.append({'index':idx,'error':f'{type(e).__name__}: {e}'})
                    log(f'DETAIL {idx+1}: ERROR {type(e).__name__}: {e}')
                finally:
                    try:
                        if click_visible_exact(page,'Results',timeout=3000):
                            page.wait_for_timeout(250)
                        else:
                            log(f'DETAIL {idx+1}: Results tab not found; attempting browser back')
                            page.go_back(wait_until='domcontentloaded',timeout=5000)
                    except Exception as e:
                        log(f'DETAIL {idx+1}: return-to-results warning {type(e).__name__}: {e}')

            report['documents_sample']=documents
            report['detail_errors']=detail_errors
            report['unique_sample_file_nos']=len({d['file_no'] for d in documents if d.get('file_no')})
            report['sample_with_direct_parcel_legal']=sum(bool(re.fullmatch(r'\d{2}-\d{2}-\d{6}-\d{6}',d.get('legal',''))) for d in documents)
            report['sample_with_linked_buttons']=sum(bool(d.get('linked_button_candidates')) for d in documents)
            report['status']='completed'
            write_report(report)
            log('STAGE: completed')
            log(json.dumps(report,indent=2))
            browser.close()
    except Exception as e:
        report['error']=f'{type(e).__name__}: {e}'
        if report.get('status')=='started': report['status']='failed'
        write_report(report)
        log(json.dumps(report,indent=2))
        raise

if __name__=='__main__':
    main()
