import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock

from run_lee_nwi_local_postgis import (
    NWIError,
    NWI_SOURCE_URL,
    WETLAND_TYPES,
    WATERS_TYPES,
    source_url_allowed,
    validate_archive,
    classify_type,
    validate_results,
    SELECT_INTERSECTIONS_SQL,
    read_database_dsn,
)

class NWIWorkerTests(unittest.TestCase):
    def test_official_florida_source_only(self):
        self.assertTrue(source_url_allowed(NWI_SOURCE_URL))
        self.assertFalse(source_url_allowed('http://documentst.ecosphere.fws.gov/wetlands/data/State-Downloads/FL_geopackage_wetlands.zip'))
        self.assertFalse(source_url_allowed('https://fake.fws.gov.evil.test/FL_geopackage_wetlands.zip'))
        self.assertFalse(source_url_allowed('https://documentst.ecosphere.fws.gov/wetlands/data/State-Downloads/GA_geopackage_wetlands.zip'))

    def test_zips_fail_closed_without_single_geopackage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'nwi.zip'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('readme.txt','not GIS')
            with self.assertRaises(NWIError): validate_archive(path)
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('../evil.gpkg','bad')
            with self.assertRaises(NWIError): validate_archive(path)
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('FL_Wetlands.gpkg','sqlite data')
                z.writestr('FL_Duplicate.gpkg','sqlite data')
            with self.assertRaises(NWIError): validate_archive(path)

    def test_classification_distinguishes_wetlands_water(self):
        for item in WETLAND_TYPES:
            self.assertEqual(classify_type(item), 'WETLAND')
        for item in WATERS_TYPES:
            self.assertEqual(classify_type(item), 'AQUATIC_OTHER')
        self.assertRaises(NWIError,classify_type,'Wetland Unknown Category')

    def test_local_intersection_uses_postgis_and_union(self):
        sql = SELECT_INTERSECTIONS_SQL.lower()
        for token in ('st_intersects', 'st_intersection', 'st_unaryunion', 'st_collect', 'st_transform','st_area'):
            self.assertIn(token,sql)
        self.assertIn('god_mode_ops.lee_parcel_geometry_postgis',sql)
        self.assertIn('god_mode_ops.nwi_wetlands_polygons',sql)
        self.assertNotIn('md5(parcel_id)',sql)

    def test_partial_and_empty_mapping_fail_closed(self):
        self.assertEqual(validate_results(0, 0, 0, 0)['review_status'],'REVIEW_REQUIRED')
        self.assertEqual(validate_results(0, 0, 0, 0)['nwi_mapped_coverage_pct'],0)
        self.assertEqual(validate_results(18.1, 22.3, 1, 2)['review_status'],'REVIEW_REQUIRED')
        self.assertRaises(NWIError,validate_results,120,150,1,3)
        self.assertRaises(NWIError,validate_results,33,21,1,2)
        self.assertRaises(NWIError,validate_results,-1,1,1,1)

    def test_no_ambient_production_database_dsn(self):
        self.assertRaises(NWIError,read_database_dsn,{})
        self.assertRaises(NWIError,read_database_dsn,{'STAGING_SUPABASE_DB_URL':'postgresql://postgres:secret@db.production.supabase.co/postgres'})

if __name__=='__main__': unittest.main()