"""Extract review evidence from an authorized clerk/title PDF download.
No challenge bypass; extraction never certifies title or recorded frontage.
Usage: python ops/recorded_document_review.py file.pdf --parcel ID --reference 'PB 15 PG 100' --source-url HTTPS --output review.json
"""
import argparse, hashlib, json, re, subprocess, tempfile
from pathlib import Path
from urllib.parse import urlparse

def review(text, parcel, reference, source_url, digest, pages):
    chunks=text.split('\f')
    refs=[]
    for i,chunk in enumerate(chunks,1):
        for line in chunk.splitlines():
            if re.search(r'frontage|right.of.way|easement|dedicat|access|restrict|vacat|plat|book|page',line,re.I):
                refs.append({'page':i,'excerpt':line.strip()[:500]})
    return {'schema_version':1,'parcel_id':parcel,'instrument_reference':reference,
            'document_url':source_url,'document_sha256':digest,'page_count':pages,
            'status':'REVIEW_REQUIRED','extraction_status':'TEXT_EXTRACTED' if text.strip() else 'NO_TEXT',
            'parcel_id_found_in_text':parcel.casefold() in text.casefold(),
            'reference_found_in_text':reference.casefold() in text.casefold(),
            'evidence_candidates':refs[:500],'exact_frontage_verified':False,
            'legal_access_verified':False,'current_restrictions_checked':False,
            'review_notes':'Match lot/block/plat, measurements and dedication; check subsequent easements, vacations and restrictions. OCR excerpts are not legal approval.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('pdf',type=Path)
    for arg in ['parcel','reference','source-url','output']:p.add_argument('--'+arg,required=True)
    a=p.parse_args();u=urlparse(a.source_url)
    if u.scheme!='https' or not u.hostname or u.username or u.password: p.error('Source must be HTTPS without credentials')
    b=a.pdf.read_bytes()
    if len(b)>50*1024*1024 or not b.startswith(b'%PDF-'):p.error('Expected PDF <=50MB')
    with tempfile.TemporaryDirectory() as tmp:
        textfile=Path(tmp)/'text.txt'
        info=subprocess.check_output(['pdfinfo',str(a.pdf)],text=True,timeout=20)
        pages=int(re.search(r'^Pages:\s+(\d+)',info,re.M)[1])
        if pages>100:p.error('Review documents in batches of <=100 pages')
        subprocess.run(['pdftotext','-layout',str(a.pdf),str(textfile)],check=True,timeout=60)
        text=textfile.read_text(errors='replace')
        if len(text.strip())<100:
            subprocess.run(['pdftoppm','-r','150','-png',str(a.pdf),str(Path(tmp)/'page')],check=True,timeout=60,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            text='\f'.join(subprocess.check_output(['tesseract',str(f),'stdout'],text=True,timeout=60,stderr=subprocess.PIPE) for f in sorted(Path(tmp).glob('page-*.png')))
        result=review(text,a.parcel,a.reference,a.source_url,hashlib.sha256(b).hexdigest(),pages)
        Path(a.output).write_text(json.dumps(result,indent=2))
    print(json.dumps({'status':result['status'],'pages':pages,'candidates':len(result['evidence_candidates'])}))
if __name__=='__main__':main()
