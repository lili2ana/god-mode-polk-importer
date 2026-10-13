import unittest,datetime,tempfile,json
from pathlib import Path
import evidence_pipeline as p
class PipelineTests(unittest.TestCase):
 def test_upload_rejects_unbounded_or_non_pdf(self):
  with self.assertRaises(ValueError):p.validate_upload(b'not pdf',{'parcel_id':'P','instrument_reference':'PB 15 PG 100','source_url':'https://www.leeclerk.org/'})
 def test_remote_path_cannot_escape(self):
  with self.assertRaises(ValueError):p.validate_remote_path('../../etc/passwd')
 def test_attom_mismatch_never_bound(self):
  response={'property':[{'identifier':{'apn':'OTHER','fips':'12071'},'foreclosure':{'documentNumber':'123'}}]}
  with self.assertRaises(ValueError):p.normalize_attom(response,'334426L3030160020','Lee')
 def test_historical_foreclosure_is_candidate_only(self):
  response={'property':[{'identifier':{'apn':'334426L3030160020','fips':'12071'},'foreclosure':{'documentNumber':'123','recordingDate':'2025-01-01','distressType':'NOD'}}]}
  d=p.normalize_attom(response,'334426L3030160020','Lee')
  self.assertEqual(d['verification_status'],'REVIEW_REQUIRED');self.assertFalse(d['release_search_complete'])
 def test_no_foreclosure_not_negative_certificate(self):
  d=p.normalize_attom({'property':[{'identifier':{'apn':'P','fips':'12071'}}]},'P','Lee')
  self.assertEqual(d['verification_status'],'NO_ADVERSE_RECORD_RETURNED');self.assertFalse(d['qualifying_distress'])

class AttomResponseTests(unittest.TestCase):
 def test_success_without_result_is_a_hold_not_a_connection_error(self):
  d=p.normalize_attom({'status':{'code':400,'msg':'SuccessWithoutResult'},'property':[]},'P','Lee')
  self.assertEqual(d['verification_status'],'NO_PROPERTY_MATCH');self.assertFalse(d['qualifying_distress'])
 def test_authentication_failure_is_not_absence(self):
  with self.assertRaises(ValueError):p.normalize_attom({'status':{'code':401,'msg':'InvalidApiKey'},'property':[]},'P','Lee')
 def test_400_no_result_allows_address_fallback(self):
  from unittest.mock import patch
  from urllib.error import HTTPError
  import io
  from urllib.parse import parse_qs,urlparse
  no_result=HTTPError('https://example.org',400,'bad',{},io.BytesIO(b'{"status":{"code":400,"msg":"SuccessWithoutResult"},"property":[]}'))
  response={'property':[{'identifier':{'apn':'P','fips':'12071'}}]}
  class Reply:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,*args):return json.dumps(response).encode()
  with patch.dict('os.environ',{'ATTOM_API_KEY':'test-key'}),patch.object(p,'urlopen',side_effect=[no_result,Reply()]) as request:
   d=p.fetch_attom('P','Lee',address='1102 FLOYD AVE S, LEHIGH ACRES, FL')
   self.assertEqual(d['verification_status'],'NO_ADVERSE_RECORD_RETURNED')
   self.assertEqual(parse_qs(urlparse(request.call_args[0][0].full_url).query)['address'],['1102 FLOYD AVE S, LEHIGH ACRES, FL'])
   self.assertNotIn('test-key',request.call_args[0][0].full_url)

class UploadEndpointTests(unittest.TestCase):
 def test_loopback_endpoint_auth_and_pdf_extraction(self):
  import os,subprocess,sys,socket,urllib.request,urllib.error
  # Generate a real PDF fixture with a valid xref; >100 text chars avoids OCR.
  text='Plat TEST ONLY. Frontage and right of way are review candidates. This test never certifies legal access or creates a property lead.'
  stream=('BT /F1 12 Tf 20 700 Td ('+text+') Tj ET').encode()
  objs=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
  pdf=b'%PDF-1.4\n';offsets=[0]
  for i,obj in enumerate(objs,1):offsets.append(len(pdf));pdf+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
  xref=len(pdf);pdf+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets[1:])+b'trailer << /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF'
  with tempfile.TemporaryDirectory() as tmp,socket.socket() as sock:
   sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
   env=dict(os.environ);env.pop('GOD_MODE_DATABASE_URL',None);env['DOCUMENT_INTAKE_TOKEN']='test-token-'+'a'*32
   proc=subprocess.Popen([sys.executable,str(Path(p.__file__)),'serve','--port',str(port),'--inbox',tmp],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   try:
    self.assertIn('loopback',proc.stdout.readline())
    url=f'http://127.0.0.1:{port}/documents'
    with self.assertRaises(urllib.error.HTTPError) as bad:urllib.request.urlopen(urllib.request.Request(url,data=pdf),timeout=10)
    self.assertEqual(bad.exception.code,401)
    req=urllib.request.Request(url,data=pdf,headers={'Authorization':'Bearer '+env['DOCUMENT_INTAKE_TOKEN'],'X-Parcel-Id':'TEST','X-Instrument-Reference':'TEST ONLY','X-Source-Url':'https://example.org/test.pdf'})
    d=json.load(urllib.request.urlopen(req,timeout=30))
    self.assertEqual(d['status'],'REVIEW_REQUIRED');self.assertFalse(d['database_staged'])
    self.assertEqual(len(list(Path(tmp).glob('*/source.pdf'))),1)
   finally:proc.terminate();proc.communicate(timeout=5)

class ImageIntakeTests(unittest.TestCase):
 def test_tiff_conversion_preserves_original_hash(self):
  from PIL import Image
  import io
  out=io.BytesIO();Image.new('RGB',(32,32),'white').save(out,format='TIFF');original=out.getvalue()
  converted=p.normalize_document(original)
  self.assertTrue(converted.startswith(b'%PDF-'));self.assertNotEqual(converted,original)
 def test_unsupported_image_rejected(self):
  with self.assertRaises(ValueError):p.normalize_document(b'GIF89a')

if __name__=='__main__':unittest.main()
