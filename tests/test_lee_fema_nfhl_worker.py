"""Test the FEMA NFHL parcel intersection logic without a live service or DB."""
import unittest
from unittest.mock import Mock

from run_lee_fema_nfhl_worker import (
    FEMAError, fema_category, interpret_nfhl_features, build_fema_query,
    get_fema_features, validate_nfhl_layer,
)

PARCEL = {"type": "Polygon", "coordinates": [[
    [-81.68, 26.68], [-81.679, 26.68], [-81.679, 26.681],
    [-81.68, 26.681], [-81.68, 26.68],
]]}


def rectangle(x1, y1, x2, y2):
    return {"type": "Polygon", "coordinates": [[
        [x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1],
    ]]}


def feature(geometry, zone, subtype="", sfha="N"):
    return {"type": "Feature", "geometry": geometry,
            "properties": {"FLD_ZONE": zone, "ZONE_SUBTY": subtype,
                           "SFHA_TF": sfha, "FLD_AR_ID": "source_id"}}


class FEMATests(unittest.TestCase):
    def test_zone_categories_fail_closed(self):
        self.assertEqual(fema_category("AE", ""), "High Risk (A/V)")
        self.assertEqual(fema_category("X", "0.2 PCT ANNUAL CHANCE FLOOD HAZARD"), "Moderate (X-Shaded)")
        self.assertEqual(fema_category("X", "AREA OF MINIMAL FLOOD HAZARD"), "Low (X-Unshaded)")
        with self.assertRaises(FEMAError):
            fema_category("MADE_UP", "")

    def test_spatial_query_is_real_envelope_not_hash(self):
        params = build_fema_query(PARCEL)
        self.assertEqual(params["geometryType"], "esriGeometryEnvelope")
        self.assertEqual(params["spatialRel"], "esriSpatialRelIntersects")
        self.assertEqual(params["outSR"], "4326")
        self.assertEqual(params["f"], "geojson")
        self.assertIn("xmin", params["geometry"])

    def test_high_risk_overlap_blocks_low_risk_assumption(self):
        # AE intersects exactly one half of the parcel, X the other half.
        xs = [
            feature(rectangle(-81.68, 26.68, -81.6795, 26.681), "AE", sfha="T"),
            feature(rectangle(-81.6795, 26.68, -81.679, 26.681), "X"),
        ]
        result = interpret_nfhl_features(PARCEL, xs)
        self.assertEqual(result["fema_flood_zone"], "AE")
        self.assertEqual(result["fema_flood_category"], "High Risk (A/V)")
        self.assertAlmostEqual(result["sfha_overlap_pct"], 50, delta=1)
        self.assertTrue(result["mixed_zones"])
        self.assertEqual(result["review_status"], "REVIEW_REQUIRED")

    def test_unrelated_feature_not_counted_as_parcel_intersection(self):
        xs = [feature(rectangle(-81.65, 26.68, -81.64, 26.681), "AE", sfha="T")]
        with self.assertRaises(FEMAError):
            interpret_nfhl_features(PARCEL, xs)

    def test_overlapping_high_risk_polygons_not_double_counted(self):
        xs = [feature(PARCEL, "AE", sfha="T"), feature(PARCEL, "AE", sfha="T")]
        result = interpret_nfhl_features(PARCEL, xs)
        self.assertLessEqual(result["sfha_overlap_pct"], 100)
        self.assertGreater(result["sfha_overlap_pct"], 99)

    def test_query_service_error_not_accepted(self):
        res = Mock()
        res.raise_for_status.return_value = None
        res.json.return_value = {"error": {"code": 400, "message": "bad geometry"}}
        sess = Mock()
        sess.post.return_value = res
        with self.assertRaises(FEMAError):
            get_fema_features(sess, PARCEL)

    def test_truncated_response_rejected(self):
        res = Mock()
        res.raise_for_status.return_value = None
        res.json.return_value = {"type": "FeatureCollection", "features": [], "exceededTransferLimit": True}
        sess = Mock()
        sess.post.return_value = res
        with self.assertRaises(FEMAError):
            get_fema_features(sess, PARCEL)

    def test_no_fema_geometry_not_zone_x(self):
        with self.assertRaises(FEMAError):
            interpret_nfhl_features(PARCEL, [])

    def test_layer_name_and_fields_verification(self):
        valid = {"name": "Flood Hazard Zones", "type": "Feature Layer", "geometryType": "esriGeometryPolygon",
                 "fields": [{"name": "FLD_ZONE"}, {"name": "ZONE_SUBTY"}, {"name": "SFHA_TF"}]}
        validate_nfhl_layer(valid)
        with self.assertRaises(FEMAError):
            validate_nfhl_layer({**valid, "fields": [{"name": "FAKE_ZONE"}]})


if __name__ == "__main__":
    unittest.main()
