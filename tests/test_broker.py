"""Broker policy tests use a deliberately synthetic JWT, never live credentials."""
import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from forge.broker import Broker, UPSTREAM

class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.auth=Path(self.tmp.name)/'auth.json'
        claims={'exp':time.time()+3600,'https://api.openai.com/auth':{'chatgpt_account_id':'unit-test-account'}}
        payload=base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
        self.real='synthetic-test-header.'+payload+'.synthetic-test-signature'
        self.auth.write_text(json.dumps({'credential_pool':{'openai-codex':[{'access_token':self.real,'base_url':UPSTREAM}]}}))
        self.broker=Broker(self.auth,'127.0.0.1',60)
        self.broker.__enter__()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.broker.__exit__,None,None,None)
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def rejected(self,path,status,token=None,body=None):
        request=urllib.request.Request(self.broker.url+path,data=json.dumps(body or {'model':'gpt-6.1-sol'}).encode(),headers={'Authorization':'Bearer '+(token or self.broker.capability)},method='POST')
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.opener.open(request,timeout=3)
        self.assertEqual(result.exception.code,status)
        result.exception.close()

    def test_capability_contains_no_provider_grant(self):
        serialized=json.dumps(self.broker.worker_auth())
        self.assertNotIn(self.real,serialized)
        self.assertNotIn('refresh_token',serialized)
        self.assertIn(self.broker.capability,serialized)

    def test_invalid_capability_rejected(self):
        self.rejected('/responses',401,token='invalid-capability')

    def test_arbitrary_endpoints_rejected(self):
        for path in ('/models','/v1/responses','/responses?url=https://example.com','/../account','//example.com/responses'):
            self.rejected(path,404)

    def test_other_models_rejected(self):
        self.rejected('/responses',403,body={'model':'not-the-approved-model'})

    def test_expired_capability_rejected(self):
        self.broker.deadline=0
        self.rejected('/responses',410)

    def test_budget_and_concurrency_caps(self):
        self.broker.requests=120
        self.rejected('/responses',429)
        self.broker.requests=0
        self.broker.active.acquire()
        self.broker.active.acquire()
        try:
            self.rejected('/responses',429)
        finally:
            self.broker.active.release()
            self.broker.active.release()

    def test_refuses_expired_or_refresh_or_other_upstream_grants(self):
        for entry in ({'access_token':self.real,'base_url':'https://example.com'}, {'access_token':self.real,'base_url':UPSTREAM,'refresh_token':'synthetic-test-refresh'}):
            self.auth.write_text(json.dumps({'credential_pool':{'openai-codex':[entry]}}))
            with self.assertRaises(ValueError):
                Broker(self.auth,'127.0.0.1',60)
        claims=base64.urlsafe_b64encode(json.dumps({'exp':1}).encode()).decode().rstrip('=')
        self.auth.write_text(json.dumps({'credential_pool':{'openai-codex':[{'access_token':'test.'+claims+'.test','base_url':UPSTREAM}]}}))
        with self.assertRaises(RuntimeError):
            Broker(self.auth,'127.0.0.1',60)

if __name__=='__main__':
    unittest.main()
