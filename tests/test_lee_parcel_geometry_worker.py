"""Fail-closed tests for source-backed Lee County parcel geometry ingestion."""
import unittest
from unittest.mock import Mock

from run_lee_parcel_geometry_worker import (
    GeometryError,
    build_pending_query,
    fetch_lee_geometry,
    normalize_strap,
    staging_dsn_is_safe,
)


POLYGON = {
    "type": "Polygon",
    "coordinates": [[
        [-81.6780718718478, 26.6855080438506],
        [-81.6776551152602, 26.6855181254357],
        [-81.6776616022383, 26.6857381339970],
        [-81.6780783626401, 26.6857280524073],
        [-81.6780718718478, 26.6855080438506],
    ]],
}


def fake_session(features):
    response = Mock()
    response.json.return_value = {"type": "FeatureCollection", "features": features}
    response.url = "https://gismapserver.leegov.com/query?f=geojson"
    response.raise_for_status.return_value = None
    session = Mock()
    session.get.return_value = response
    return session


class LeeGeometryWorkerTests(unittest.TestCase):
    def test_strap_normalization_matches_county_format(self):
        self.assertEqual(normalize_strap("01-44-26-L1-01003.0020"), "014426L1010030020")

    def test_reject_bad_strap(self):
        for val in ("", "not a parcel", "1 OR 1=1", "123"):
            with self.subTest(val=val), self.assertRaises(ValueError):
                normalize_strap(val)

    def test_source_backed_polygon_returns_record(self):
        session = fake_session([{"type": "Feature", "properties": {"STRAP": "014426L1010030020", "OBJECTID": 16590}, "geometry": POLYGON}])
        evidence = fetch_lee_geometry(session, "01-44-26-L1-01003.0020")
        self.assertEqual(evidence["source_strap"], "014426L1010030020")
        self.assertEqual(evidence["source_objectid"], 16590)
        self.assertEqual(evidence["polygon_geojson"], POLYGON)
        self.assertFalse(evidence["legally_verified_boundary"])
        self.assertIn("source_url", evidence)
        kwargs = session.get.call_args.kwargs
        self.assertEqual(kwargs["params"]["where"], "STRAP='014426L1010030020'")
        self.assertEqual(kwargs["params"]["f"], "geojson")

    def test_no_match_is_not_evidence(self):
        self.assertIsNone(fetch_lee_geometry(fake_session([]), "01-44-26-L1-01003.0020"))

    def test_strap_mismatch_rejected(self):
        features = [{"properties": {"STRAP": "99999999999999999", "OBJECTID": 16590}, "geometry": POLYGON}]
        with self.assertRaises(GeometryError):
            fetch_lee_geometry(fake_session(features), "01-44-26-L1-01003.0020")

    def test_duplicate_matches_rejected(self):
        f = {"properties": {"STRAP": "014426L1010030020", "OBJECTID": 16590}, "geometry": POLYGON}
        with self.assertRaises(GeometryError):
            fetch_lee_geometry(fake_session([f, f]), "01-44-26-L1-01003.0020")

    def test_invalid_geometry_rejected(self):
        features = [{"properties": {"STRAP": "014426L1010030020", "OBJECTID": 16590}, "geometry": {"type": "Point", "coordinates": [-81.6, 26.6]}}]
        with self.assertRaises(GeometryError):
            fetch_lee_geometry(fake_session(features), "01-44-26-L1-01003.0020")

    def test_arcgis_error_on_http_200_rejected(self):
        session = fake_session([])
        session.get.return_value.json.return_value = {"error": {"code": 400, "message": "Failed query"}}
        with self.assertRaises(GeometryError):
            fetch_lee_geometry(session, "01-44-26-L1-01003.0020")

    def test_lee_county_queue_and_missing_geometry_only(self):
        query = build_pending_query()
        self.assertIn("s.county = 'Lee County'", query)
        self.assertIn("g.parcel_id IS NULL", query)
        self.assertIn("PENDING_SCREENING", query)
        self.assertNotIn("tax_roll_delinquent_status", query)

    def test_keyset_cursor_skips_previously_attempted_missing_straps(self):
        sql = build_pending_query(after=True)
        self.assertIn("AND s.parcel_id > %s", sql)
        self.assertIn("LIMIT %s", sql)

    def test_staging_only_dsn_safety_gate(self):
        self.assertTrue(staging_dsn_is_safe("postgresql://postgres:secret@db.ltwqgkcblvtscgldxvjw.supabase.co:5432/postgres"))
        self.assertTrue(staging_dsn_is_safe("postgresql://postgres.ltwqgkcblvtscgldxvjw:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres"))
        self.assertFalse(staging_dsn_is_safe("postgresql://postgres:secret@db.production.supabase.co:5432/postgres"))


if __name__ == "__main__":
    unittest.main()
