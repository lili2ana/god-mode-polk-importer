"""Authorized FTPS/PDF intake and ATTOM distress candidates. No approval bypass."""
import argparse,datetime,ftplib,hashlib,hmac,json,os,re,ssl,subprocess,sys,tempfile
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path,PurePosixPath
from urllib.parse import urlparse,urlencode
from urllib.request import Request,urlopen
MAX_PDF=50*1024*1024
FIPS={'lee':'12071','charlotte':'12015','osceola':'12097'}
def validate_remote_path(path):
    if not path or '\\' in path or any(x in path for x in ['\r','\n','\x00']) or '..' in PurePosixPath(path).parts:
        raise ValueError('Invalid remote path')
    return path

def validate_upload(data,meta):
    if not data.startswith(b'%PDF-') or len(data)>MAX_PDF:raise ValueError('Expected PDF <=50MB')
    for field in ['parcel_id','instrument_reference','source_url']:
        if not isinstance(meta.get(field),str) or not meta[field].strip() or len(meta[field])>2000:raise ValueError('Missing/invalid '+field)
    u=urlparse(meta['source_url'])
    if u.scheme!='https' or not u.hostname or u.username or u.password:raise ValueError('Expected credential-free HTTPS source')
    return meta

def normalize_document(data):
    if len(data)>MAX_PDF:raise ValueError('Document exceeds 50MB')
    if data.startswith(b'%PDF-'):return data
    if not data.startswith((b'II*\x00',b'MM\x00*',b'\x89PNG',b'\xff\xd8')):raise ValueError('Expected PDF, TIFF, PNG or JPEG')
    import io,warnings
    from PIL import Image,ImageSequence
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if getattr(image,'n_frames',1)>100:raise ValueError('Image pages exceed 100')
            if image.width*image.height*getattr(image,'n_frames',1)>100_000_000:raise ValueError('Image pixel budget exceeded')
            pages=[frame.convert('RGB') for frame in ImageSequence.Iterator(image)]
            out=io.BytesIO();pages[0].save(out,format='PDF',save_all=True,append_images=pages[1:])
            converted=out.getvalue()
            if len(converted)>MAX_PDF:raise ValueError('Converted PDF exceeds 50MB')
            return converted

def normalize_attom(response,parcel,county):
    props=response.get('property',[]);norm=lambda x:re.sub('[^A-Z0-9]','',str(x).upper())
    if not props or any(norm(x.get('identifier',{}).get('apn',''))!=norm(parcel) or str(x.get('identifier',{}).get('fips',''))!=FIPS.get(county.lower()) for x in props):
        raise ValueError('ATTOM parcel/county identity mismatch or missing records')
    records=[x['foreclosure'] for x in props if x.get('foreclosure')]
    return {'schema_version':1,'parcel_id':parcel,'county':county,'source_name':'ATTOM',
        'source_url':'https://api.gateway.attomdata.com/propertyapi/v1.0.0/saleshistory/expandedhistory',
        'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'record_sha256':hashlib.sha256(json.dumps(response,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'verification_status':'REVIEW_REQUIRED' if records else 'NO_ADVERSE_RECORD_RETURNED',
        'qualifying_distress':False,'release_search_complete':False,'foreclosure_records':records,'raw':response}

def fetch_attom(parcel,county):
    key=os.environ.get('ATTOM_API_KEY')
    if not key:raise ValueError('ATTOM_API_KEY is not configured')
    url='https://api.gateway.attomdata.com/propertyapi/v1.0.0/saleshistory/expandedhistory?'+urlencode({'fips':FIPS[county.lower()],'APN':parcel})
    # Key header, never URL/log output; bounded response, verified TLS.
    req=Request(url,headers={'Accept':'application/json','apikey':key})
    with urlopen(req,timeout=30,context=ssl.create_default_context()) as r:
        data=r.read(10*1024*1024+1)
    if len(data)>10*1024*1024:raise ValueError('Provider response too large')
    return normalize_attom(json.loads(data),parcel,county)

def download_ftps(remote_path):
    path=validate_remote_path(remote_path)
    config={k:os.environ.get('LEE_FTP_'+k) for k in ['HOST','USER','PASSWORD']}
    if not all(config.values()):raise ValueError('Lee FTP host/user/password are not configured')
    chunks=[];size=0
    def receive(b):
        nonlocal size
        size+=len(b)
        if size>MAX_PDF:raise ValueError('Clerk download exceeds 50MB')
        chunks.append(b)
    with ftplib.FTP_TLS(context=ssl.create_default_context(),timeout=30) as ftp:
        ftp.connect(config['HOST'],int(os.environ.get('LEE_FTP_PORT','21')))
        ftp.login(config['USER'],config['PASSWORD']);ftp.prot_p()
        ftp.retrbinary('RETR '+path,receive)
    return b''.join(chunks)

def download_provider(url):
    # Operator-configured aggregator or presigned HTTPS download; never from upload payload.
    host=os.environ.get('DOCUMENT_PROVIDER_HOST');u=urlparse(url)
    if u.scheme!='https' or not host or u.hostname!=host or u.username or u.password:raise ValueError('Document provider host is not authorized')
    class NoRedirect(__import__('urllib.request',fromlist=['HTTPRedirectHandler']).HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):raise ValueError('Cross-host redirects disabled')
    from urllib.request import build_opener,HTTPSHandler
    headers={}
    if os.environ.get('DOCUMENT_API_KEY'):headers['Authorization']='Bearer '+os.environ['DOCUMENT_API_KEY']
    with build_opener(NoRedirect(),HTTPSHandler(context=ssl.create_default_context())).open(Request(url,headers=headers),timeout=30) as r:data=r.read(MAX_PDF+1)
    return data

def stage_database(kind,evidence):
    if not os.environ.get('GOD_MODE_DATABASE_URL'):return False
    import psycopg
    function={'document':'stage_recorded_document','distress':'stage_distress_candidate'}[kind]
    with psycopg.connect(os.environ['GOD_MODE_DATABASE_URL'],sslmode='verify-full',sslrootcert=os.environ.get('PGSSLROOTCERT','system'),connect_timeout=10) as conn:
        with conn.cursor() as cur:cur.execute('select god_mode_ops.'+function+'(%s::jsonb)',(json.dumps(evidence),))
    return True

def ingest_pdf(data,meta,inbox):
    original=data;data=normalize_document(data);validate_upload(data,meta);digest=hashlib.sha256(original).hexdigest()
    # Local content-addressed archive keeps bytes and extracted evidence linked.
    root=Path(inbox)/digest;root.mkdir(parents=True,exist_ok=True)
    (root/'original.bin').write_bytes(original)
    pdf=root/'source.pdf';pdf.write_bytes(data);out=root/'review.json'
    subprocess.run([sys.executable,str(Path(__file__).with_name('recorded_document_review.py')),str(pdf),
       '--parcel',meta['parcel_id'],'--reference',meta['instrument_reference'],'--source-url',meta['source_url'],'--output',str(out)],check=True,timeout=900,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    evidence=json.loads(out.read_text());evidence['processing_pdf_sha256']=evidence['document_sha256'];evidence['document_sha256']=digest; evidence['database_staged']=stage_database('document',evidence)
    # Per-parcel record avoids conflating one plat covering several subject lots.
    name=hashlib.sha256((meta['parcel_id']+'|'+meta['instrument_reference']).encode()).hexdigest()
    (root/(name+'.json')).write_text(json.dumps(evidence,indent=2))
    return evidence

def serve(port,inbox):
    token=os.environ.get('DOCUMENT_INTAKE_TOKEN')
    if not token or len(token)<32:raise ValueError('Configure DOCUMENT_INTAKE_TOKEN with >=32 characters')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            if self.path!='/documents':self.send_error(404);return
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):self.send_error(401);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=MAX_PDF:self.send_error(413);return
                meta={'parcel_id':self.headers.get('X-Parcel-Id'),'instrument_reference':self.headers.get('X-Instrument-Reference'),'source_url':self.headers.get('X-Source-Url')}
                self.connection.settimeout(30)
                data=self.rfile.read(length)
                if len(data)!=length:raise ValueError('Incomplete upload')
                d=ingest_pdf(data,meta,inbox)
                body=json.dumps({'sha256':d['document_sha256'],'status':d['status'],'database_staged':d['database_staged']}).encode()
                self.send_response(201);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            except (ValueError,subprocess.SubprocessError):self.send_error(422,'Invalid document or extraction failed')
            except Exception:self.send_error(503,'Evidence persistence unavailable')
    server=HTTPServer(('127.0.0.1',port),Handler);server.timeout=30
    print('Document intake listening on loopback port '+str(port),flush=True);server.serve_forever()

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['serve','manifest','attom','queue']);p.add_argument('--port',type=int,default=8765)
    p.add_argument('--inbox',default='/tmp/god-mode-document-inbox');p.add_argument('--manifest');p.add_argument('--parcel');p.add_argument('--county',choices=['Lee','Charlotte','Osceola']);p.add_argument('--output')
    a=p.parse_args()
    if a.command=='serve':serve(a.port,a.inbox)
    elif a.command=='attom':
        if not a.parcel or not a.county or not a.output:p.error('attom requires parcel, county, output')
        d=fetch_attom(a.parcel,a.county);d['database_staged']=stage_database('distress',d);Path(a.output).write_text(json.dumps(d,indent=2));print(json.dumps({'status':d['verification_status'],'database_staged':d['database_staged']}))
    elif a.command=='queue':
        if not os.environ.get('GOD_MODE_DATABASE_URL'):p.error('GOD_MODE_DATABASE_URL is required')
        import psycopg
        with psycopg.connect(os.environ['GOD_MODE_DATABASE_URL'],sslmode='verify-full',sslrootcert=os.environ.get('PGSSLROOTCERT','system'),connect_timeout=10) as conn:
            rows=conn.execute("select parcel_id,county from god_mode_ops.buyer_demand_land_screening where screening_status='PENDING_FLOOD_WETLAND_ROAD_DISTRESS' and lower(city) in ('lehigh acres','port charlotte') and lower(county) in ('lee','charlotte') order by parcel_id limit 25").fetchall()
        failed=0
        if os.environ.get('ATTOM_API_KEY'):
            for parcel,county in rows:
                try:stage_database('distress',fetch_attom(parcel,county))
                except Exception:failed+=1;print(json.dumps({'parcel_id':parcel,'status':'DISTRESS_FETCH_BLOCKED'}))
        else:print(json.dumps({'distress_status':'MISSING_ATTOM_API_KEY','active_hold_count':len(rows)}))
        with psycopg.connect(os.environ['GOD_MODE_DATABASE_URL'],sslmode='verify-full',sslrootcert=os.environ.get('PGSSLROOTCERT','system'),connect_timeout=10) as conn:
            result=conn.execute('select god_mode_ops.run_county_automation(5,true)').fetchone()[0]
        print(json.dumps({'automation':result,'provider_failures':failed,'provider_limit':25}))
        if failed:raise SystemExit(1)
    else:
        if not a.manifest:p.error('manifest file required')
        entries=json.loads(Path(a.manifest).read_text())
        if not isinstance(entries,list) or len(entries)>25:p.error('Manifest must be an array <=25 documents')
        for m in entries:
            try:
                data=download_ftps(m['remote_path']) if m.get('remote_path') else download_provider(m['download_url'])
                d=ingest_pdf(data,m,a.inbox);print(json.dumps({'parcel_id':m['parcel_id'],'status':d['status'],'database_staged':d['database_staged']}))
            except Exception as e:
                # Never print provider URLs, passwords, responses or request exceptions.
                print(json.dumps({'parcel_id':m.get('parcel_id'),'status':'INTAKE_BLOCKED','kind':type(e).__name__}));raise SystemExit(1)
if __name__=='__main__':main()
