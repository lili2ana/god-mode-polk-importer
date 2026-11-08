import io
import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import Mock

from fetch_lee_govhub_report import (
    validate_govhub_export_url, download_govhub_csv, GovHubSourceError,
)
from import_lee_appraiser_valuation import (
    validate_appraiser_source_url, parse_valuation_csv,
    AppraiserSourceError, parse_official_zip,
)


def fake_response(url, content, headers=None, history=None):
    response = Mock()
    response.url = url
    response.status_code = 200
    response.headers = headers or {'Content-Type': 'text/csv'}
    response.history = history or []
    response.iter_content.return_value = [content]
    response.raise_for_status.return_value = None
    return response

class GovHubEvidenceTests(unittest.TestCase):
    def test_https_whitelist(self):
        self.assertTrue(validate_govhub_export_url('https://lee.county-taxes.com/reports/export.csv?token=ab'))
        self.assertTrue(validate_govhub_export_url('https://county-taxes.net/fl-lee/reports/export.csv'))
        for bad in ('http://lee.county-taxes.com/report.csv','https://county-taxes.net/fl-other/report.csv',
                    'https://evil.example/fl-lee/report.csv','https://lee.county-taxes.com.evil.org/report.csv',
                    'https://user:pass@lee.county-taxes.com/report.csv','https://lee.county-taxes.com:8443/a.csv'):
            self.assertFalse(validate_govhub_export_url(bad),bad)

    def test_fetch_rejects_html_login_and_wrong_redirect(self):
        with tempfile.TemporaryDirectory() as tmp:
            resp = fake_response('https://lee.county-taxes.com/login',b'<html>Sign in</html>',{'Content-Type':'text/html'})
            sess = Mock(); sess.get.return_value = resp
            with self.assertRaises(GovHubSourceError):
                download_govhub_csv(sess,'https://lee.county-taxes.com/reports/export.csv',Path(tmp)/'a.csv')
            resp = fake_response('https://third.party.example/export.csv',b'parcel_id,tax_year,tax_status,amount_due\n')
            sess.get.return_value = resp
            with self.assertRaises(GovHubSourceError):
                download_govhub_csv(sess,'https://lee.county-taxes.com/reports/export.csv',Path(tmp)/'b.csv')

    def test_fetch_capped_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=b'parcel_id,tax_year,tax_status,amount_due\n01-44-26-L1-01003.0020,2024,PAID,0.00\n'
            resp=fake_response('https://lee.county-taxes.com/reports/export.csv',data)
            sess=Mock();sess.get.return_value=resp
            out=Path(tmp)/'tax.csv'
            digest=download_govhub_csv(sess,'https://lee.county-taxes.com/reports/export.csv',out)
            self.assertEqual(out.read_bytes(),data)
            self.assertEqual(len(digest),64)
            self.assertEqual(sess.get.call_args.kwargs['stream'],True)

    def test_fetch_rejects_oversized(self):
        with tempfile.TemporaryDirectory() as tmp:
            resp=fake_response('https://lee.county-taxes.com/report.csv',b'a'*21)
            sess=Mock();sess.get.return_value=resp
            with self.assertRaises(GovHubSourceError):
                download_govhub_csv(sess,'https://lee.county-taxes.com/report.csv',Path(tmp)/'bad.csv',max_bytes=20)

class AppraiserEvidenceTests(unittest.TestCase):
    def test_official_link_only(self):
        self.assertTrue(validate_appraiser_source_url('https://www.leepa.org/TaxRoll/ParcelData/LCPA_Parcel_Data_TXT.zip'))
        self.assertTrue(validate_appraiser_source_url('https://leepa.org/OnlineReports/Reports.aspx'))
        self.assertFalse(validate_appraiser_source_url('https://wrong.example/ParcelData.zip'))
        self.assertFalse(validate_appraiser_source_url('http://www.leepa.org/TaxRoll/ParcelData/LCPA_Parcel_Data_TXT.zip'))

    def test_parses_explicit_current_owner_value_only(self):
        csv_text=io.StringIO('STRAP,OWNER_NAME,SITEADDR,JUST_VALUE,TAX_YEAR\n01-44-26-L1-01003.0020,Example LLC,7502 KAREN AVE N LEHIGH ACRES,45000,2026\n')
        rows=parse_valuation_csv(csv_text,valuation_year=2026)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['normalized_strap'],'014426L1010030020')
        self.assertEqual(rows[0]['just_value'],'45000.00')
        self.assertEqual(rows[0]['owner_name'],'Example LLC')

    def test_missing_valuation_or_ambiguous_columns_fail_closed(self):
        for bad in [
          'STRAP,OWNER_NAME,SITEADDR,TAX_YEAR\n01-44-26-L1-01003.0020,Example LLC,7502 LEHIGH ACRES,2026\n',
          'STRAP,OWNER_NAME,OWNER,SITEADDR,JUST_VALUE,TAX_YEAR\n01-44-26-L1-01003.0020,One,Two,7502 LEHIGH ACRES,25000,2026\n',
          'STRAP,OWNER_NAME,SITEADDR,JUST_VALUE,TAX_YEAR\n01-44-26-L1-01003.0020,Example LLC,7502 LEHIGH ACRES,-1,2026\n',
        ]:
            with self.subTest(bad=bad),self.assertRaises(AppraiserSourceError):
                parse_valuation_csv(io.StringIO(bad),valuation_year=2026)

    def test_official_zip_rejects_invalid_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'fake.zip';p.write_bytes(b'<html>portal</html>')
            with self.assertRaises(AppraiserSourceError):
                parse_official_zip(p,valuation_year=2026)

if __name__=='__main__':unittest.main()

# Separate focused test: staging insert cannot quietly authorize an appraiser profile.
class StagingWriteGuards(unittest.TestCase):
    def test_appraiser_batch_only_review_pending(self):
        import sys,types
        from unittest.mock import patch,MagicMock
        from import_lee_appraiser_valuation import stage_valuation
        generated=[]
        fake_extras=types.SimpleNamespace(execute_values=lambda cur,sql,rows,template,page_size:generated.append((sql,rows,template)))
        fake_psycopg=types.SimpleNamespace(extras=fake_extras)
        conn=MagicMock()
        cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect=[('00000000-0000-0000-0000-000000000001',),(1,)]
        records=[{'normalized_strap':'014426L1010030020','owner_name':'Example LLC','site_address':'LEHIGH ACRES','just_value':'45000.00','valuation_year':2026}]
        with patch.dict(sys.modules,{'psycopg2':fake_psycopg,'psycopg2.extras':fake_extras}):
            result=stage_valuation(conn,records,'https://www.leepa.org/OnlineReports/Reports.aspx',date(2026,11,7),'a'*64)
        self.assertEqual(result,1)
        self.assertEqual(len(generated),1)
        self.assertIn("'REVIEW_REQUIRED'",generated[0][0])
        self.assertIn("verification_status<>'VERIFIED_SOURCE'",generated[0][0])
        self.assertEqual(len(generated[0][1][0]),7)
        conn.commit.assert_called_once()