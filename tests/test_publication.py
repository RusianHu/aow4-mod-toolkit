from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import publication
from aow4mod.core import FormatError,sha
from aow4mod.filesystem import inventory


class FakeSteam:
    def __init__(self,owner=123):self.owner=owner;self.calls=0;self.closed=False
    def steam_id(self):return self.owner
    def close(self):self.closed=True
    def publish(self,manifest,content,receipt,timeout):
        self.calls+=1
        assert '.upload-attempts' in str(content)
        assert {k:v['sha256'] for k,v in inventory(content).items()}==manifest['files']
        result={'status':'success','app_id':1669000,'published_file_id':456,'content_files':manifest['files']}
        receipt.write_text(json.dumps(result), encoding='utf-8');return result
    def verify_download(self,manifest,timeout):return {'content_matches':True,'files':manifest['files']}


class PublicationTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        content=self.root/'content/mod';content.mkdir(parents=True);(content/'sample.acp').write_bytes(b'payload')
        (self.root/'description.txt').write_bytes(b'Description\n');(self.root/'notes.txt').write_bytes(b'Changes\n')
        self.manifest={'format':'aow4mod-release/1','app_id':1669000,'version':'1.0','content_directory':'content/mod',
            'release_gate':{'review_required':False},
            'files':{'sample.acp':sha(b'payload')},'description_file':'description.txt','change_notes_file':'notes.txt',
            'description_sha256':sha(b'Description\n'),'change_notes_sha256':sha(b'Changes\n'),
            'workshop':{'published_file_id':456,'expected_owner':123,'expected_title':'Example'}}
        self.path=self.root/'manifest.json';self.path.write_text(json.dumps(self.manifest), encoding='utf-8')
        self.remote={'consumer_app_id':1669000,'creator':'123','publishedfileid':'456','title':'Example','description':'Description','time_updated':1,'visibility':0}
        self.client=FakeSteam();self.factory=lambda game:self.client;self.fetcher=lambda item,proxy:self.remote

    def test_preflight_never_submits(self):
        result=publication.publish_existing(self.path,factory=self.factory,fetcher=self.fetcher)
        self.assertTrue(result['online_checked']);self.assertEqual(self.client.calls,0)
        self.assertFalse((self.root/'upload-receipt.json').exists())

    def test_submit_uses_reviewed_snapshot_and_rejects_repeat(self):
        result=publication.publish_existing(self.path,True,factory=self.factory,fetcher=self.fetcher)
        self.assertEqual(self.client.calls,1);self.assertTrue(result['verification_required'])
        with self.assertRaisesRegex(FormatError,'prior submission'):publication.publish_existing(self.path,True,factory=self.factory,fetcher=self.fetcher)
        self.assertEqual(self.client.calls,1)

    def test_wrong_account_cannot_submit(self):
        self.client.owner=999
        with self.assertRaisesRegex(FormatError,'Signed-in'):publication.publish_existing(self.path,True,factory=self.factory,fetcher=self.fetcher)
        self.assertEqual(self.client.calls,0)

    def test_added_payload_file_and_cross_app_are_rejected(self):
        (self.root/'content/mod/unreviewed.txt').write_text('extra', encoding='utf-8')
        with self.assertRaises(FormatError):publication.load_release(self.path)
        (self.root/'content/mod/unreviewed.txt').unlink()
        self.manifest['app_id']=1;self.path.write_text(json.dumps(self.manifest), encoding='utf-8')
        with self.assertRaises(FormatError):publication.load_release(self.path)

    def test_unknown_receipt_can_be_reconciled_without_resubmission(self):
        receipt={'status':'timeout_outcome_unknown','app_id':1669000,'published_file_id':456,'content_files':self.manifest['files']}
        (self.root/'upload-receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
        result=publication.reconcile(self.path,factory=self.factory,fetcher=self.fetcher)
        self.assertTrue(result['desired_state_verified']);self.assertFalse(result['resubmitted'])
        self.assertEqual(self.client.calls,0)

    def test_metadata_only_verification_does_not_claim_content_verified(self):
        result=publication.verify(self.path,factory=self.factory,fetcher=self.fetcher)
        self.assertTrue(result['remote']['description_matches'])
        self.assertFalse(result['content_verified']);self.assertFalse(result['desired_state_verified'])

    def test_incomplete_release_requires_explicit_review_override(self):
        self.manifest['release_gate']['review_required']=True;self.path.write_text(json.dumps(self.manifest), encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'require review'):
            publication.publish_existing(self.path,True,factory=self.factory,fetcher=self.fetcher)
        self.assertEqual(self.client.calls,0)

    def test_audit_report_is_hash_bound(self):
        report=self.root/'audit.json';report.write_bytes(b'{"summary":{"issues":0}}')
        self.manifest.update(audit_file='audit.json',audit_sha256=sha(report.read_bytes()),audit_summary={'issues':0})
        self.path.write_text(json.dumps(self.manifest), encoding='utf-8')
        publication.load_release(self.path)
        report.write_bytes(b'{"summary":{"issues":1}}')
        with self.assertRaisesRegex(FormatError,'Audit report changed'):publication.load_release(self.path)


if __name__=='__main__':unittest.main()
