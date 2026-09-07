#!/usr/bin/env python3
import json, re, sys, time
from collections import Counter, defaultdict
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE='https://apps.polkcountyclerk.net/browserviewor/'
FROM_DATE='08/30/2026'
TO_DATE='09/05/2026'
DOC_TYPES=['LP','L PEN']
OUT=Path('official_records_lp_probe.json')


def clean(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()


def extract_field(body, label):
    m=re.search(rf'{re.escape(label)}\s*:?\s*(.+)', body, re.I)
    return clean(m.group(1)) if m else ''


def main():
    report={'from':FROM_DATE,'to':TO_DATE,'doc_types':DOC_TYPES,'status':'started'}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1500,'height':1000})
        page.goto(BASE, wait_until='networkidle', timeout=120000)
        # Top Search tab, then lower Document Type tab.
        # Prefer exact visible tab labels; fall back to index among matching anchors/buttons.
        search_candidates=page.get_by_text('Search', exact=True)
        if search_candidates.count():
            search_candidates.first.click()
            page.wait_for_timeout(500)
        dt_candidates=page.get_by_text('Document Type', exact=True)
        if dt_candidates.count() < 1:
            raise RuntimeError('Document Type tab not found')
        # Use the last exact match; first is commonly a label in Party tab, second the tab.
        dt_candidates.last.click()
        page.wait_for_timeout(800)

        # Dump visible controls for debugging into report.
        report['inputs']=[{'placeholder':x.get_attribute('placeholder'),'name':x.get_attribute('name'),'type':x.get_attribute('type')} for x in page.locator('input:visible').all()]
        report['buttons']=[clean(x.inner_text()) for x in page.locator('button:visible').all()]

        # Find visible text input closest to 'Document Type' section and fill codes directly.
        visible_inputs=page.locator('input:visible')
        doc_input=None
        from_input=None
        to_input=None
        for i in range(visible_inputs.count()):
            el=visible_inputs.nth(i)
            ph=(el.get_attribute('placeholder') or '').lower()
            val=el.input_value() if (el.get_attribute('type') or 'text')!='checkbox' else ''
            if 'document type' in ph and 'search document' not in ph and doc_input is None:
                doc_input=el
            if 'mm/dd/yyyy' in ph:
                if from_input is None: from_input=el
                elif to_input is None: to_input=el
        if doc_input is None:
            # fallback: first visible text input in Document Type tab
            for i in range(visible_inputs.count()):
                el=visible_inputs.nth(i)
                if (el.get_attribute('type') or 'text') in ('text',''):
                    doc_input=el; break
        if not (doc_input and from_input and to_input):
            raise RuntimeError(f'Could not identify search inputs: doc={bool(doc_input)} from={bool(from_input)} to={bool(to_input)}')
        doc_input.fill(','.join(DOC_TYPES))
        from_input.fill(FROM_DATE)
        to_input.fill(TO_DATE)

        # Click visible Search button within active pane: use last visible Search to avoid top tab.
        buttons=page.get_by_role('button', name='Search', exact=True)
        if not buttons.count():
            raise RuntimeError('Search button not found')
        buttons.last.click()
        page.wait_for_timeout(1500)
        try:
            page.wait_for_load_state('networkidle', timeout=30000)
        except Exception:
            pass
        page.screenshot(path='official_records_results.png', full_page=True)
        body=page.locator('body').inner_text()
        report['results_body_excerpt']=clean(body)[:12000]

        # Parse result rows from visible table-like rows by locating View links/buttons.
        views=page.get_by_text('View', exact=True)
        report['view_count_visible_page']=views.count()
        # Total rows from page text when available.
        mt=re.search(r'\((\d+) total\) records', body, re.I)
        if mt: report['total_party_rows']=int(mt.group(1))

        # Collect unique documents from current page and paginate if numbered buttons available.
        docs={}
        page_seen=0
        while page_seen < 50:
            page_seen += 1
            body=page.locator('body').inner_text()
            # Each View lives in a row; inspect closest tr.
            for v in page.get_by_text('View', exact=True).all():
                try:
                    row=v.locator('xpath=ancestor::tr[1]')
                    txt=clean(row.inner_text())
                    # columns often View, *, Name, Date, Type; file no not on result row.
                    # retain row text for party-row inflation analysis.
                    key=txt
                    docs.setdefault('party_rows',[]).append(txt)
                except Exception:
                    pass
            # pagination: click next numeric button not yet active, if any.
            nums=[]
            for b in page.locator('button:visible').all():
                t=clean(b.inner_text())
                if t.isdigit(): nums.append((int(t),b))
            if not nums: break
            current=max([n for n,b in nums if b.get_attribute('disabled') is not None] or [1])
            nxt=[(n,b) for n,b in nums if n>current]
            if not nxt: break
            n,b=min(nxt,key=lambda x:x[0])
            b.click(); page.wait_for_timeout(800)

        # Return to first result page if needed, then open up to 30 unique View rows and capture details.
        # Use repeated first-page navigation conservatively.
        # We gather documents by clicking each visible View and returning to Results tab.
        documents=[]
        results_tab=page.get_by_text('Results', exact=True)
        if results_tab.count(): results_tab.first.click(); page.wait_for_timeout(400)
        max_docs=30
        idx=0
        while idx < max_docs:
            views=page.get_by_text('View', exact=True)
            if idx>=views.count(): break
            v=views.nth(idx)
            rowtxt=''
            try: rowtxt=clean(v.locator('xpath=ancestor::tr[1]').inner_text())
            except Exception: pass
            v.click(); page.wait_for_timeout(700)
            txt=page.locator('body').inner_text()
            # Extract structured labels from rendered page text using line-oriented parser.
            data={'result_row':rowtxt,'page_text':clean(txt)[:12000]}
            for label,key in [('Type','type'),('File No.','file_no'),('Date','date'),('Book/Page','book_page'),('Legal','legal'),('District','district'),('Map','map'),('Sub Map','sub_map'),('Parcel','parcel'),('Sub Parcel','sub_parcel')]:
                m=re.search(rf'^{re.escape(label)}\s*:\s*(.*)$', txt, re.I|re.M)
                data[key]=clean(m.group(1)) if m else ''
            # Capture linked-document button texts if rendered.
            linked=[]; referring=[]
            try:
                alltxt=[clean(b.inner_text()) for b in page.locator('button:visible').all()]
                # Heuristic: CFN-like 10-digit text with optional doc type.
                for t in alltxt:
                    if re.search(r'\b\d{10}\b', t): linked.append(t)
            except Exception: pass
            data['linked_button_candidates']=linked
            if data.get('file_no') and not any(d.get('file_no')==data['file_no'] for d in documents):
                documents.append(data)
            # back to Results tab
            if results_tab.count(): results_tab.first.click(); page.wait_for_timeout(500)
            idx += 1
        report['documents_sample']=documents

        # Summaries
        party_rows=docs.get('party_rows',[])
        report['party_rows_collected']=len(party_rows)
        report['type_counts_party_rows']=dict(Counter((r.split()[-1] if r else '') for r in party_rows))
        report['unique_sample_file_nos']=len({d.get('file_no') for d in documents if d.get('file_no')})
        report['sample_with_direct_parcel_legal']=sum(bool(re.fullmatch(r'\d{2}-\d{2}-\d{2}-\d{6}-\d{6}', d.get('legal',''))) for d in documents)
        report['sample_with_linked_buttons']=sum(bool(d.get('linked_button_candidates')) for d in documents)
        report['status']='completed'
        OUT.write_text(json.dumps(report,indent=2),encoding='utf-8')
        browser.close()
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    main()
