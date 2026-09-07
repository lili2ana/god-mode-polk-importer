#!/usr/bin/env python3
import calendar, json, os, re
from datetime import date, datetime
from urllib.parse import urljoin, urlparse, parse_qs
from playwright.sync_api import sync_playwright
import psycopg2
from psycopg2.extras import execute_values, Json

CALENDAR_URL='https://polk.realforeclose.com/index.cfm?ZACTION=USER&ZMETHOD=CALENDAR'
DAYLIST='https://polk.realforeclose.com/index.cfm?zaction=AUCTION&Zmethod=DAYLIST&AUCTIONDATE={}'
BASE='https://polk.realforeclose.com/'
OUT='realforeclose_month_probe.json'


def clean(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()


def money(s):
    if not s: return None
    m=re.search(r'\$?([0-9][0-9,]*(?:\.\d{1,2})?)', s)
    return float(m.group(1).replace(',','')) if m else None


def normalize_parcel(raw):
    digits=re.sub(r'\D','',raw or '')
    if len(digits)==18:
        return f'{digits[:2]}-{digits[2:4]}-{digits[4:6]}-{digits[6:12]}-{digits[12:18]}'
    return None


def extract_label(text, label, next_labels):
    nxt='|'.join(re.escape(x) for x in next_labels)
    m=re.search(re.escape(label)+r'\s*:?\s*(.+?)(?=\s+(?:'+nxt+r')\s*:|$)', text, re.I)
    return clean(m.group(1)) if m else None


def parse_detail(text, url):
    t=clean(text)
    qs=parse_qs(urlparse(url).query)
    aid=(qs.get('AID') or qs.get('aid') or [''])[0]
    labels=['Case Number','Case Type','Final Judgment Amount','Parcel ID','Certificate Number','Property Address','Assessed Value','Legal Description','Auction Starts','Name On Title']
    vals={lab:extract_label(t,lab,[x for x in labels if x!=lab]) for lab in labels}
    case=vals['Case Number'] or (re.search(r'Case Number\s*:?\s*([A-Za-z0-9-]+)',t,re.I).group(1) if re.search(r'Case Number\s*:?\s*([A-Za-z0-9-]+)',t,re.I) else None)
    parcel_raw=vals['Parcel ID'] or (re.search(r'Parcel ID\s*:?\s*(\d{18})',t,re.I).group(1) if re.search(r'Parcel ID\s*:?\s*(\d{18})',t,re.I) else None)
    auction_start=vals['Auction Starts']
    auction_date=None; auction_time=None
    if auction_start:
        md=re.search(r'(\d{2}/\d{2}/\d{4})',auction_start)
        mt=re.search(r'(\d{1,2}:\d{2}\s*[AP]M(?:\s*ET)?)',auction_start,re.I)
        if md: auction_date=datetime.strptime(md.group(1),'%m/%d/%Y').date()
        if mt: auction_time=clean(mt.group(1))
    return {
      'auction_id': aid,'case_number': case,'auction_date': auction_date,'auction_time': auction_time,
      'parcel_id_raw': parcel_raw,'parcel_id': normalize_parcel(parcel_raw),'property_address': vals['Property Address'],
      'case_type': vals['Case Type'],'final_judgment': money(vals['Final Judgment Amount']),'assessed_value': money(vals['Assessed Value']),
      'legal_description': vals['Legal Description'],'source_url': url,'raw_text': t,'raw_data': vals,
    }


def target_month():
    raw=os.getenv('FORECLOSE_MONTH','').strip()
    if raw:
        return datetime.strptime(raw,'%Y-%m').date().replace(day=1)
    return date.today().replace(day=1)


def main():
    db=os.environ['SUPABASE_DB_URL']
    month=target_month()
    report={'calendar_url':CALENDAR_URL,'target_month':month.isoformat(),'status':'started','days':[],'auctions':[]}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1500,'height':1000})
        page.set_default_timeout(7000)
        page.set_default_navigation_timeout(30000)

        # Verify site/calendar is reachable, but do not depend on its JS/DOM for discovery.
        r=page.goto(CALENDAR_URL,wait_until='domcontentloaded')
        report['calendar_http_status']=r.status if r else None
        report['calendar_title']=page.title()
        report['calendar_text_excerpt']=clean(page.locator('body').inner_text())[:5000]
        if report['calendar_http_status'] and report['calendar_http_status'] >= 400:
            raise RuntimeError(f'Calendar HTTP failure: {report["calendar_http_status"]}')

        days_in_month=calendar.monthrange(month.year,month.month)[1]
        print('enumerating_month=',month.strftime('%Y-%m'),'days=',days_in_month,flush=True)
        seen_detail=set()
        for day in range(1,days_in_month+1):
            day_date=date(month.year,month.month,day)
            ds=day_date.strftime('%m/%d/%Y')
            day_url=DAYLIST.format(ds)
            rr=page.goto(day_url,wait_until='domcontentloaded')
            status=rr.status if rr else None
            page.wait_for_timeout(250)
            day_text=clean(page.locator('body').inner_text())
            detail_links=[]
            for a in page.locator('a').all():
                href=a.get_attribute('href') or ''
                hu=href.upper()
                if 'ZMETHOD=DETAILS' in hu and 'AID=' in hu:
                    full=urljoin(BASE,href)
                    if full not in detail_links: detail_links.append(full)
            # Fallback: details URLs may be embedded in onclick/script instead of href.
            if not detail_links:
                html=page.content()
                for m in re.finditer(r'[^"\']*ZMETHOD=DETAILS[^"\']*AID=\d+[^"\']*',html,re.I):
                    frag=m.group(0).replace('&amp;','&')
                    if 'index.cfm' in frag.lower():
                        frag=frag[frag.lower().find('index.cfm'):]
                    full=urljoin(BASE,frag)
                    if full not in detail_links: detail_links.append(full)
            if not detail_links:
                continue
            report['days'].append({'auction_date':day_date.isoformat(),'source_url':day_url,'http_status':status,'case_count':len(detail_links),'raw_text':day_text[:5000]})
            print('day',day_date,'cases',len(detail_links),flush=True)
            for detail_url in detail_links:
                if detail_url in seen_detail: continue
                seen_detail.add(detail_url)
                page.goto(detail_url,wait_until='domcontentloaded')
                page.wait_for_timeout(300)
                text=page.locator('body').inner_text()
                row=parse_detail(text,detail_url)
                if not row['auction_date']: row['auction_date']=day_date
                report['auctions'].append(row)
                print('case',row['auction_id'],row['case_number'],row['parcel_id'],flush=True)
        browser.close()

    if not report['days']:
        raise RuntimeError(f'No populated foreclosure auction days found for {month:%Y-%m}')
    if not report['auctions']:
        raise RuntimeError(f'Populated auction days found but no auction details parsed for {month:%Y-%m}')

    conn=psycopg2.connect(db)
    try:
      with conn.cursor() as cur:
        for d in report['days']:
          cur.execute('''insert into public.polk_foreclosure_auction_calendar
            (auction_date,case_count,source_url,calendar_month,raw_text,last_seen_at)
            values (%s,%s,%s,date_trunc('month',%s::date)::date,%s,now())
            on conflict (auction_date) do update set case_count=excluded.case_count,source_url=excluded.source_url,raw_text=excluded.raw_text,last_seen_at=now()''',
            (d['auction_date'],d['case_count'],d['source_url'],d['auction_date'],d['raw_text']))
        vals=[]
        for a in report['auctions']:
          if not a['auction_id']: continue
          vals.append((a['auction_id'],a['case_number'],a['auction_date'],a['auction_time'],a['parcel_id_raw'],a['parcel_id'],a['property_address'],a['case_type'],a['final_judgment'],a['assessed_value'],a['legal_description'],a['source_url'],a['raw_text'],Json(a['raw_data'])))
        if vals:
          execute_values(cur,'''insert into public.polk_foreclosure_auctions
            (auction_id,case_number,auction_date,auction_time,parcel_id_raw,parcel_id,property_address,case_type,final_judgment,assessed_value,legal_description,source_url,raw_text,raw_data,last_seen_at)
            values %s
            on conflict (auction_id) do update set case_number=excluded.case_number,auction_date=excluded.auction_date,auction_time=excluded.auction_time,parcel_id_raw=excluded.parcel_id_raw,parcel_id=excluded.parcel_id,property_address=excluded.property_address,case_type=excluded.case_type,final_judgment=excluded.final_judgment,assessed_value=excluded.assessed_value,legal_description=excluded.legal_description,source_url=excluded.source_url,raw_text=excluded.raw_text,raw_data=excluded.raw_data,last_seen_at=now()''',vals)
        cur.execute('''update public.polk_foreclosure_auctions a set matched_property_id=p.id, match_confidence=1.0
                       from public.properties p where a.parcel_id is not null and p.parcel_id=a.parcel_id''')
      conn.commit()
    finally:
      conn.close()
    report['status']='completed'; report['auction_count']=len(report['auctions']); report['day_count']=len(report['days'])
    with open(OUT,'w',encoding='utf-8') as f: json.dump(report,f,indent=2,default=str)
    print(json.dumps({'status':'completed','target_month':report['target_month'],'day_count':report['day_count'],'auction_count':report['auction_count']},indent=2),flush=True)

if __name__=='__main__':
    main()
