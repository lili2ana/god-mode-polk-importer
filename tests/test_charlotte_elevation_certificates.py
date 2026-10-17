import unittest
from charlotte_elevation_certificates import certificate_enrichment, CERTIFICATES, PARCELS


class CertificateTests(unittest.TestCase):
    def test_join_preserves_datum_and_never_clears_flood(self):
        def fetch(url, params):
            if url == PARCELS:
                return [{"attributes": {"ACCOUNT": "402128202004"}}]
            self.assertEqual(url, CERTIFICATES)
            return [{"attributes": {
                "ParcelID": "402128202004", "ProjectID": "P1",
                "Base_Flood_Elevation": "10.2", "Floor_Elevation": "11.5",
                "Datum": "NGVD 1929", "AppStatus": "Complete", "LINK": "https://example.org/document"
            }}]
        result = certificate_enrichment("402128202004", fetch)
        self.assertEqual(result["status"], "REVIEW")
        self.assertFalse(result["flood_clearance_verified"])
        self.assertFalse(result["vertical_datum_conversion_verified"])
        self.assertEqual(result["certificates"][0]["Datum"], "NGVD 1929")

    def test_missing_parcel_fails_closed(self):
        result = certificate_enrichment("402128202004", lambda *_: [])
        self.assertEqual(result["reason"], "parcel_match_missing_or_ambiguous")
        self.assertEqual(result["certificates"], [])

    def test_bad_account_rejected(self):
        with self.assertRaises(ValueError):
            certificate_enrichment("402128202004' OR 1=1")


if __name__ == "__main__":
    unittest.main()
