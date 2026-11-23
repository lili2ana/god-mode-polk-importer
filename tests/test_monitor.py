import unittest
from monitor import diff, validate_db, webhook_payload


def sample():
    return {
        'db': {'jobs': {
            'LEE_PARCEL_GEOMETRY': {'job_status':'PARTIAL_GEOMETRY_VERIFIED','total_parcels_queued':3376,'parcels_processed':2710},
            'FEMA_NFHL': {'job_status':'PIVOT_LOCAL_POSTGIS_INTERSECTION','total_parcels_queued':3376,'parcels_processed':0},
            'USFWS_NWI': {'job_status':'NWI_LOCAL_POSTGIS_INITIALIZED','total_parcels_queued':3376,'parcels_processed':0},
            'LEE_TAX_COLLECTOR': {'job_status':'AWAITING_OFFICIAL_GOVHUB_REPORT_EXPORT','total_parcels_queued':3376,'parcels_processed':0},
        }, 'table_counts': {'lee_parcel_fema_intersections':0, 'lee_parcel_nwi_intersections':0,'lee_tax_report_import_observations':0,'lee_verified_owner_valuation':0,'lee_master_screening':3376}, 'qualified':0, 'geometry_evidence_count':2710, 'reconciliation_unresolved':666},
        'github': {'pr':{'merged':False,'html_url':'https://github.com/lili2ana/god-mode-polk-importer/pull/22'}, 'workflows':{
            'lee-real-parcel-geometry.yml':{'run_id':20,'run_attempt':1,'status':'completed','conclusion':'failure','html_url':'https://github.com/lili2ana/god-mode-polk-importer/actions/runs/20'},
            'lee-official-source-import.yml':{'run_id':21,'run_attempt':1,'status':'completed','conclusion':'success','html_url':'https://github.com/lili2ana/god-mode-polk-importer/actions/runs/21'}
        }}
    }

class MonitorTests(unittest.TestCase):
    def test_baseline_is_silent(self):
        self.assertEqual([], diff(None, sample()))

    def test_same_snapshot_is_silent(self):
        s=sample(); self.assertEqual([], diff(s,s))

    def test_geometry_progress_and_unresolved(self):
        import copy
        a=sample(); b=copy.deepcopy(a); b['db']['jobs']['LEE_PARCEL_GEOMETRY']['parcels_processed']=2711; b['db']['geometry_evidence_count']=2711; b['db']['reconciliation_unresolved']=665
        msgs=diff(a,b)
        self.assertTrue(any('2,710 → 2,711' in m and '666 → 665' in m for m in msgs))

    def test_new_failure_same_conclusion(self):
        import copy
        a=sample(); b=copy.deepcopy(a); b['github']['workflows']['lee-real-parcel-geometry.yml']['run_id']=23; b['github']['workflows']['lee-real-parcel-geometry.yml']['html_url']='https://github.com/lili2ana/god-mode-polk-importer/actions/runs/23'
        msgs=diff(a,b); self.assertTrue(any('runs/20' not in m and 'runs/23' in m for m in msgs))

    def test_same_run_finishes(self):
        import copy
        a=sample(); a['github']['workflows']['lee-real-parcel-geometry.yml']['status']='in_progress';a['github']['workflows']['lee-real-parcel-geometry.yml']['conclusion']=None
        b=copy.deepcopy(a);b['github']['workflows']['lee-real-parcel-geometry.yml']['status']='completed';b['github']['workflows']['lee-real-parcel-geometry.yml']['conclusion']='failure'
        self.assertTrue(any('failed' in m.lower() for m in diff(a,b)))

    def test_blocker_change(self):
        import copy
        a=sample(); b=copy.deepcopy(a); b['db']['jobs']['FEMA_NFHL']['job_status']='COMPLETE'
        self.assertTrue(any('FEMA_NFHL' in m for m in diff(a,b)))

    def test_provenance_counts(self):
        import copy
        a=sample(); b=copy.deepcopy(a);b['db']['table_counts']['lee_parcel_nwi_intersections']=10
        self.assertTrue(any('lee_parcel_nwi_intersections' in m for m in diff(a,b)))

    def test_reject_missing_job_and_bad_counts(self):
        a=sample()['db'];del a['jobs']['FEMA_NFHL']
        with self.assertRaises(ValueError):validate_db(a)
        b=sample()['db'];b['jobs']['LEE_PARCEL_GEOMETRY']['parcels_processed']=None
        with self.assertRaises(ValueError):validate_db(b)

    def test_formats(self):
        self.assertEqual({'content':'hi'},webhook_payload('discord','hi'))
        self.assertEqual({'text':'hi'},webhook_payload('slack','hi'))
        with self.assertRaises(ValueError):webhook_payload('other','hi')

    def test_new_qualified_leads(self):
        import copy
        a=sample(); b=copy.deepcopy(a);b['db']['qualified']=1
        self.assertTrue(any('qualified' in m.lower() for m in diff(a,b)))

    def test_api_failure_does_not_fabricate_no_runs(self):
        from unittest.mock import patch
        from urllib.error import HTTPError
        import monitor
        valid_pr = {'merged': False, 'html_url': 'https://github.com/lili2ana/god-mode-polk-importer/pull/22'}
        def fake_get(url, token):
            if url.endswith('/pulls/22'):
                return valid_pr
            raise HTTPError(url, 403, 'Rate limited', {}, None)
        with patch.object(monitor, 'get_json', side_effect=fake_get):
            with self.assertRaises(HTTPError):
                monitor.check_github('fake')

    def test_pr_branch_404_fallback(self):
        from unittest.mock import patch
        from urllib.error import HTTPError
        import monitor
        valid_pr = {'merged': False, 'html_url': 'https://github.com/lili2ana/god-mode-polk-importer/pull/22'}
        def fake_get(url, token):
            if url.endswith('/pulls/22'):
                return valid_pr
            if '/actions/workflows/' in url:
                raise HTTPError(url, 404, 'Not yet on main', {}, None)
            return {'total_count': 1, 'workflow_runs': [{
                'id':55,'run_attempt':1,'status':'completed','conclusion':'failure',
                'html_url':'https://github.com/lili2ana/god-mode-polk-importer/actions/runs/55',
                'path':'.github/workflows/lee-real-parcel-geometry.yml@refs/pull/22/merge'}]}
        with patch.object(monitor, 'get_json', side_effect=fake_get):
            result = monitor.check_github('fake')
        self.assertEqual(55, result['workflows']['lee-real-parcel-geometry.yml']['run_id'])
        self.assertEqual('no_runs', result['workflows']['lee-official-source-import.yml']['status'])

    def test_webhook_failure_does_not_advance_state(self):
        import monitor
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / 'state.json'
            with patch.object(monitor, 'STATE_FILE', file_path), \
                 patch.dict('os.environ', {'STAGING_SUPABASE_DB_URL': 'fake',
                                          'GH_MONITOR_TOKEN':'fake',
                                          'ALERT_WEBHOOK_URL':'https://discord.com/api/webhooks/test'}, clear=False), \
                 patch.object(monitor, 'load_previous_state', return_value=sample()), \
                 patch.object(monitor, 'check_supabase', return_value=sample()['db']), \
                 patch.object(monitor, 'check_github', return_value={**sample()['github'], 'pr': {'merged': True, 'html_url': 'https://github.com/lili2ana/god-mode-polk-importer/pull/22'}}), \
                 patch.object(monitor, 'send_webhook', side_effect=OSError('Delivery error')):
                with self.assertRaises(OSError):
                    monitor.main()
                self.assertFalse(file_path.exists())

if __name__=='__main__':unittest.main()
