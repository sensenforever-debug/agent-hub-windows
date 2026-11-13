"""Connect the current user's local Agent Hub API to the team gateway."""
import argparse
import asyncio
import base64
import getpass
import json
import os
import re
import sys
from urllib.parse import urlparse

import httpx
import keyring
from websockets.asyncio.client import connect
import probes

SERVICE = 'agent-gateway-workbuddy-node'
AGENT_HUB_VERSION = '0.3.0'
SOURCE_MODEL_RE = re.compile(r'^[A-Za-z0-9._:-]{1,100}@(workbuddy|raccoon)$')
WORKBUDDY_REVISION = '2f3c556d38896164bf138627b484662aa6f4d345'
RACCOON_REVISION = '5f1b8e6d86b1178ef2fba7596c5869846beed0aa'

def protocol3_hello(args):
    epoch=getattr(args,'credential_epoch','')
    adapter=getattr(args,'adapter_revision',AGENT_HUB_VERSION)
    return {'type':'hello','protocol':3,'agent_revision':'agent-hub/'+adapter,
            'sources':[{'agent':'workbuddy','backend_revision':getattr(args,'workbuddy_revision',WORKBUDDY_REVISION),'credential_epoch':epoch},
                       {'agent':'raccoon','backend_revision':getattr(args,'raccoon_revision',RACCOON_REVISION),
                        'adapter_revision':adapter,'credential_epoch':epoch}]}

def hub_offers(names):
    return [{'source_model':name,'agent':name.rsplit('@',1)[1],'native_model':name.rsplit('@',1)[0]}
            for name in names if isinstance(name,str) and SOURCE_MODEL_RE.fullmatch(name)][:100]

def config():
    p = argparse.ArgumentParser()
    p.add_argument('command', nargs='?', choices=['run','configure'], default='run')
    p.add_argument('--gateway', default=os.environ.get('AG_GATEWAY','wss://api.435038537.xyz/node/ws'))
    p.add_argument('--protocol', type=int, choices=(2,3), default=int(os.environ.get('AG_NODE_PROTOCOL','2')))
    p.add_argument('--bridge', default=os.environ.get('AG_BRIDGE'))
    p.add_argument('--node-token')
    p.add_argument('--bridge-token')
    p.add_argument('--bridge-revision', default=os.environ.get('AG_BRIDGE_REVISION','unknown'))
    p.add_argument('--workbuddy-revision', default=os.environ.get('AG_WORKBUDDY_REVISION',WORKBUDDY_REVISION))
    p.add_argument('--raccoon-revision', default=os.environ.get('AG_RACCOON_REVISION',RACCOON_REVISION))
    p.add_argument('--adapter-revision', default=os.environ.get('AG_HUB_ADAPTER_REVISION',AGENT_HUB_VERSION))
    p.add_argument('--credential-epoch', default=os.environ.get('AG_CREDENTIAL_EPOCH',''))
    args=p.parse_args()
    if not args.bridge: args.bridge='http://127.0.0.1:8788' if args.protocol==3 else 'http://127.0.0.1:8790'
    return args

async def main(a, status=None):
    def report(kind, detail=''):
        if status: status(kind, detail)
    node_token = os.environ.get('AG_NODE_TOKEN') or keyring.get_password(SERVICE,'node-token')
    protocol=getattr(a,'protocol',2)
    if protocol==3:
        from unified_proxy import local_key
        bridge_token = local_key()
    else:
        bridge_token = os.environ.get('AG_BRIDGE_TOKEN') or keyring.get_password(SERVICE,'bridge-token')
    if not node_token or not bridge_token:
        raise SystemExit('Configure node token and local API key first')
    parsed = urlparse(a.bridge)
    if parsed.hostname not in ('127.0.0.1','localhost') or parsed.scheme != 'http':
        raise SystemExit('Bridge must use http://127.0.0.1')
    if not a.gateway.startswith('wss://'):
        raise SystemExit('Gateway must use wss://')
    async with httpx.AsyncClient(base_url=a.bridge, timeout=httpx.Timeout(30,read=None),trust_env=False) as http:
        delay = 1
        while True:
            try:
                async with connect(a.gateway,additional_headers={'Authorization':f'Bearer {node_token}'},max_size=6*1024*1024,ping_interval=20,ping_timeout=20) as ws:
                    print('Connected to gateway',flush=True)
                    report('connected')
                    delay = 1
                    send_lock = asyncio.Lock()
                    tasks = set()
                    active = {}
                    probe_lock = asyncio.Lock()
                    current_models = set()
                    async def send(payload):
                        async with send_lock: await ws.send(json.dumps(payload,separators=(',',':')))
                    if protocol==3:
                        await send(protocol3_hello(a))
                    else:
                        await send({'type':'hello','protocol':2,'agent_revision':'2','bridge_revision':getattr(a,'bridge_revision','unknown')})
                    async def refresh_models():
                        nonlocal current_models
                        while True:
                            try:
                                r = await http.get('/v1/models',headers={'Authorization':f'Bearer {bridge_token}'})
                                r.raise_for_status()
                                names = [x['id'] for x in r.json().get('data',[]) if isinstance(x,dict) and isinstance(x.get('id'),str)]
                                if protocol==3:
                                    offers=hub_offers(names)
                                    names=[item['source_model'] for item in offers]
                                current_models=set(names)
                                if protocol==3:
                                    await send({'type':'models','offers':offers})
                                else:
                                    await send({'type':'models','models':names})
                                report('models', names)
                                await asyncio.sleep(60)
                            except Exception as e:
                                print('Model discovery failed:',type(e).__name__,flush=True)
                                report('bridge_error', type(e).__name__)
                                current_models=set()
                                await send({'type':'models','offers':[]} if protocol==3 else {'type':'models','models':[]})
                                await asyncio.sleep(5)
                    refresh = asyncio.create_task(refresh_models())
                    async def handle(m):
                        req_id = m['id']
                        headers = {'Authorization':f'Bearer {bridge_token}','x-api-key':bridge_token,'Content-Type':'application/json'}
                        headers.update(m.get('headers',{}))
                        try:
                            async with http.stream('POST',m['path'],headers=headers,json=m['body']) as r:
                                await send({'type':'start','id':req_id,'status':r.status_code,'content_type':r.headers.get('content-type','application/json')})
                                async for chunk in r.aiter_raw():
                                    if chunk:
                                        await send({'type':'chunk','id':req_id,'data':base64.b64encode(chunk).decode()})
                                await send({'type':'end','id':req_id})
                        except Exception as e:
                            await send({'type':'error','id':req_id,'detail':type(e).__name__})
                    async def handle_probe(m):
                        model=m.get('model')
                        if not isinstance(model,str) or model not in current_models:
                            return
                        async with probe_lock:
                            for _ in range(150):
                                if not active: break
                                await asyncio.sleep(0.2)
                            report('probe',f'验收 {model} 开始')
                            budget=m.get('max_tokens',80)
                            if type(budget) is not int or not 1<=budget<=1024:
                                budget=512 if protocol==3 and model.endswith('@raccoon') else 80
                            results=await probes.run_model(http,model,bridge_token,
                                lambda case,result: report('probe',f'{model} {case}: '+('通过' if result is True else str(result))),
                                budget=budget)
                            await send({'type':'validation','model':model,'probe_id':m.get('probe_id'),'results':results})
                            report('validation',{'model':model,'results':results,'passed':all(x is True for x in results.values()),
                                                 'budget':budget,'suite_revision':m.get('suite_revision','')})
                            report('probe',f'验收 {model} '+('通过' if all(x is True for x in results.values()) else '未通过'))
                    try:
                        async for raw in ws:
                            m = json.loads(raw)
                            if m.get('type')=='request':
                                t=asyncio.create_task(handle(m))
                                tasks.add(t); t.add_done_callback(tasks.discard)
                                active[m['id']] = t
                                t.add_done_callback(lambda _, req_id=m['id']: active.pop(req_id,None))
                            elif m.get('type')=='probe':
                                t=asyncio.create_task(handle_probe(m))
                                tasks.add(t); t.add_done_callback(tasks.discard)
                            elif m.get('type')=='cancel':
                                task = active.get(m.get('id'))
                                if task: task.cancel()
                    finally:
                        refresh.cancel()
                        for t in tasks: t.cancel()
                        await asyncio.gather(refresh,*tasks,return_exceptions=True)
            except Exception as e:
                print('Disconnected:',type(e).__name__,flush=True)
                report('disconnected',type(e).__name__)
                await asyncio.sleep(delay)
                delay=min(delay*2,30)

if __name__=='__main__':
    a=config()
    if a.command=='configure':
        node_token = a.node_token or keyring.get_password(SERVICE,'node-token') or getpass.getpass('Gateway node token: ')
        if not node_token:
            raise SystemExit('Node token required')
        keyring.set_password(SERVICE,'node-token',node_token)
        if a.protocol==3:
            from unified_proxy import local_key
            local_key()
        else:
            bridge_token = a.bridge_token or keyring.get_password(SERVICE,'bridge-token') or getpass.getpass('Local bridge token: ')
            if not bridge_token:
                raise SystemExit('Bridge token required')
            keyring.set_password(SERVICE,'bridge-token',bridge_token)
        print('Credentials stored in Windows Credential Manager')
    else:
        asyncio.run(main(a))
