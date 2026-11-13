import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch

import node
import node_controller
import probes


class Protocol3ConnectorTests(unittest.TestCase):
    def test_source_offers_and_versions(self):
        offers=node.hub_offers(['glm-5.3@workbuddy','sn-glm-5-3-flash@raccoon',
                                'unknown','bad@unknown','a@raccoon@workbuddy'])
        self.assertEqual([offer['agent'] for offer in offers],['workbuddy','raccoon'])
        self.assertEqual(offers[1]['native_model'],'sn-glm-5-3-flash')
        args=type('Args',(),{'workbuddy_revision':'abc123','raccoon_revision':'def456',
                             'adapter_revision':node.AGENT_HUB_VERSION,'credential_epoch':'random-epoch'})()
        hello=node.protocol3_hello(args)
        self.assertEqual(hello['protocol'],3)
        self.assertEqual(hello['sources'][1]['backend_revision'],'def456')
        self.assertEqual(hello['sources'][1]['credential_epoch'],'random-epoch')
        self.assertNotIn('token',str(hello).lower())

    def test_unavailable_workbuddy_is_omitted_until_healthy_revision_is_known(self):
        args=type('Args',(),{'workbuddy_revision':None,'raccoon_revision':node.RACCOON_REVISION})()
        hello=node.protocol3_hello(args)
        self.assertEqual([source['agent'] for source in hello['sources']], ['raccoon'])
        names=['glm-5.3@workbuddy','sn-glm-5-3-flash@raccoon']
        self.assertEqual([offer['source_model'] for offer in node.hub_offers(names, {'raccoon'})],
                         ['sn-glm-5-3-flash@raccoon'])
        args.workbuddy_revision='a'*40
        self.assertEqual(node.protocol3_hello(args)['sources'][0]['backend_revision'], 'a'*40)
        args.workbuddy_revision='b'*40
        self.assertEqual(node.protocol3_hello(args)['sources'][0]['backend_revision'], 'b'*40)

    def test_revision_requires_running_process_and_live_model_catalog(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'data':[{'id':'glm-5.3'}]}
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def get(self, *args, **kwargs): return Response()
        with patch.object(node.bm, 'running_revision', side_effect=[None,'a'*40,'b'*40]), \
             patch.object(node.bm, 'port_open', return_value=True), \
             patch.object(node.httpx, 'AsyncClient', Client):
            versions=[asyncio.run(node.workbuddy_revision('http://127.0.0.1:8790','secret'))
                      for _ in range(3)]
        self.assertEqual(versions,[None,'a'*40,'b'*40])

    def test_reconnects_when_workbuddy_recovers_or_changes_version(self):
        class Socket:
            def __init__(self): self.closed=[]
            async def close(self, **kwargs): self.closed.append(kwargs)
        async def immediate_sleep(_): pass
        for old,new in ((None,'a'*40),('a'*40,'b'*40),('a'*40,None)):
            socket=Socket()
            events=[]
            with patch.object(node, 'workbuddy_revision', return_value=new), \
                 patch.object(node.asyncio, 'sleep', side_effect=immediate_sleep), \
                 patch.object(node.keyring, 'get_password', return_value='secret'):
                asyncio.run(node.watch_workbuddy(socket,'http://127.0.0.1:8790',old,
                                                   lambda *event:events.append(event)))
            self.assertEqual(len(socket.closed),1)
            self.assertEqual(events[0][0],'workbuddy_revision')

    def test_raccoon_budget_and_legacy_budget(self):
        for api in ('chat','responses','messages'):
            field='max_output_tokens' if api=='responses' else 'max_tokens'
            self.assertEqual(probes.base_body(api,'sn-test@raccoon',budget=512)[field],512)
            self.assertEqual(probes.base_body(api,'glm-5.3')[field],80)

    def test_desktop_controller_connects_unified_api_without_workbuddy_bridge(self):
        with tempfile.TemporaryDirectory() as folder:
            calls=[]
            async def fake_main(args,notify):
                calls.append(args)
            def credential(service,name):
                return 'agn_test_node' if name=='node-token' else None
            with patch.object(node_controller,'CONFIG',Path(folder)/'missing.json'), \
                 patch.object(Path,'home',return_value=Path(folder)), \
                 patch.object(node_controller.keyring,'get_password',side_effect=credential), \
                 patch.object(node_controller.bm,'active',return_value=(None,None)), \
                 patch.object(node_controller.node,'main',side_effect=fake_main):
                controller=node_controller.NodeController(lambda *args:None)
                controller.start()
                controller.worker.join(timeout=2)
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0].protocol,3)
            self.assertEqual(calls[0].bridge,'http://127.0.0.1:8788')
            self.assertEqual(calls[0].raccoon_revision,node.RACCOON_REVISION)


if __name__=='__main__':
    unittest.main()
