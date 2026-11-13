import tempfile
import unittest
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
