import unittest
from recorded_document_review import review
class DocumentReviewTests(unittest.TestCase):
 def test_extraction_never_approves_access(self):
  d=review('Plat PB 15 PG 100\n80 feet frontage\fAccess easement recorded', 'parcel', 'PB 15 PG 100','https://example.org/plat.pdf','a'*64,2)
  self.assertEqual([x['page'] for x in d['evidence_candidates']],[1,1,2])
  self.assertFalse(d['exact_frontage_verified']);self.assertFalse(d['legal_access_verified'])
  self.assertEqual(d['status'],'REVIEW_REQUIRED')
 def test_empty_scan_not_evidence(self):
  d=review('', 'parcel','record','https://example.org','a'*64,1)
  self.assertEqual(d['extraction_status'],'NO_TEXT');self.assertFalse(d['parcel_id_found_in_text'])
if __name__=='__main__':unittest.main()
