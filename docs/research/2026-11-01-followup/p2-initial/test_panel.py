import sys, unittest
from pathlib import Path
ROOT = Path(__file__).parent
sys.path[:0] = [str(ROOT/'api'), str(ROOT/'admin')]
from approvals import approve_batch
from view import show_approvals
class PanelTest(unittest.TestCase):
    def test_existing_menu_access(self):
        self.assertTrue(show_approvals({'permissions':['catalog:manage']}))
        self.assertFalse(show_approvals({'permissions':[]}))
    def test_api_boundaries(self):
        user={'business_id':'a','permissions':['inventory:manage']}
        p={'id':'p','business_id':'a','area':'inventory','grant_key':'g'}
        self.assertEqual(approve_batch(user,[p],'g')[0]['status'],'approved')
        for u,k in [(dict(user,business_id='b'),'g'),(dict(user,permissions=[]),'g'),(user,'bad')]:
            with self.assertRaises(PermissionError): approve_batch(u,[p],k)
