import json
import pathlib
import tempfile
import unittest
import zipfile

from shapely.geometry import Polygon, mapping

from run_lee_fema_static_postgis import (
    FEMA_SOURCE_URL,
    download_url_allowed,
    fema_category,
    extract_safe_zip,
    interpret_local_intersections,
    LOCAL_INTERSECTION_SQL,
    NFHLInputError,
)

SQUARE = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])

class LocalFEMATests(unittest.TestCase):
    def test_reject_nonofficial_download_urls(self):
        self.assertTrue(download_url_allowed(FEMA_SOURCE_URL))
        for url in ['http://msc.fema.gov/portal/downloadProduct?productID=NFHL_12071C',
                    'https://msc.fema.gov.evil.com/portal/downloadProduct?productID=NFHL_12071C',
                    'https://example.com/file.zip',
                    'https://msc.fema.gov/portal/downloadProduct?productID=NFHL_99999C']:
            self.assertFalse(download_url_allowed(url), url)

    def test_zip_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            z=pathlib.Path(d)/'bad.zip'
            with zipfile.ZipFile(z,'w') as f:
                f.writestr('../evil.txt', 'no')
            with self.assertRaises(NFHLInputError):
                extract_safe_zip(z, pathlib.Path(d)/'out')

    def test_zip_rejects_non_geospatial_archive(self):
        with tempfile.TemporaryDirectory() as d:
            z=pathlib.Path(d)/'bad.zip'
            with zipfile.ZipFile(z,'w') as f:
                f.writestr('readme.txt','not FEMA spatial data')
            with self.assertRaises(NFHLInputError):
                extract_safe_zip(z, pathlib.Path(d)/'out')

    def test_flood_category(self):
        self.assertEqual(fema_category('AE', '', 'T'),'High Risk (A/V)')
        self.assertEqual(fema_category('X', '0.2 PCT ANNUAL CHANCE FLOOD HAZARD','F'),'Moderate (X-Shaded)')
        self.assertEqual(fema_category('X', 'AREA OF MINIMAL FLOOD HAZARD','F'),'Low (X-Unshaded)')
        self.assertEqual(fema_category('D','','F'),'Undetermined (D)')
        with self.assertRaises(NFHLInputError):
            fema_category('AE','','F')
        with self.assertRaises(NFHLInputError):
            fema_category('??','','F')

    def test_no_intersections_not_classified(self):
        self.assertIsNone(interpret_local_intersections(SQUARE, []))

    def test_partial_unshaded_not_classified(self):
        half = Polygon([(0,0),(5,0),(5,10),(0,10)])
        self.assertIsNone(interpret_local_intersections(SQUARE,[('X','AREA OF MINIMAL FLOOD HAZARD','F',half)]))

    def test_partial_highrisk_requires_review(self):
        half = Polygon([(0,0),(5,0),(5,10),(0,10)])
        obj=interpret_local_intersections(SQUARE,[('AE','','T',half)])
        self.assertEqual(obj['fema_flood_category'],'High Risk (A/V)')
        self.assertEqual(obj['review_status'],'REVIEW_REQUIRED')
        self.assertAlmostEqual(obj['fema_coverage_pct'],50,delta=1)

    def test_full_low_risk_only_when_covered(self):
        obj=interpret_local_intersections(SQUARE,[('X','AREA OF MINIMAL FLOOD HAZARD','F',SQUARE)])
        self.assertEqual(obj['review_status'],'EVIDENCE_CAPTURED')
        self.assertAlmostEqual(obj['fema_coverage_pct'],100,delta=.01)

    def test_mixed_highlow_requires_review(self):
        west = Polygon([(0,0),(5,0),(5,10),(0,10)])
        east = Polygon([(5,0),(10,0),(10,10),(5,10)])
        obj=interpret_local_intersections(SQUARE,[('AE','','T',west),('X','AREA OF MINIMAL FLOOD HAZARD','F',east)])
        self.assertTrue(obj['mixed_zones'])
        self.assertEqual(obj['fema_flood_category'],'High Risk (A/V)')
        self.assertEqual(obj['review_status'],'REVIEW_REQUIRED')
        self.assertAlmostEqual(obj['sfha_overlap_pct'],50,delta=1)

    def test_sql_uses_local_postgis_and_dedicated_dataset(self):
        self.assertIn('extensions.ST_Intersects',LOCAL_INTERSECTION_SQL)
        self.assertIn('fema_nfhl_hazard_polygons',LOCAL_INTERSECTION_SQL)
        self.assertNotIn('hash(',LOCAL_INTERSECTION_SQL)

if __name__=='__main__':unittest.main()