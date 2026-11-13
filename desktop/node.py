"""Run in the same Windows user session as the logged-in WorkBuddy bridge."""
import argparse
import asyncio
import base64
import getpass
import json
import os
import sys
from urllib.parse import urlparse

import httpx
import keyring
from websockets.asyncio.client import connect
import probes

SERVICE = 'agent-gateway-workbuddy-node'

def config():
    p = argparse.ArgumentParser()
    p.add_argument('command', nargs='?', choices=['run','configure'], default='run')
    p.add_argument('--gateway', default=os.environ.get('AG_GATEWAY','wss://api.435038537.xyz/node/ws'))
    p.add_argument('--bridge', default=os.environ.get('AG_BRIDGE','http://127.0.0.1:8790'))
    p.add_argument('--node-token')
    p.add_argument('--bridge-token')
    p.add_argument('--bridge-revision', default=os.environ.get('AG_BRIDGE_REVISION','unknown'))
    return p.parse_args()

async def main(a, status=None):
    def report(kind, detail=''):
        if status: status(kind, detail)
    node_token = os.environ.get('AG_NODE_TOKEN') or keyring.get_password(SERVICE,'node-token')
    bridge_token = os.environ.get('AG_BRIDGE_TOKEN') or keyring.get_password(SERVICE,'bridge-token')
    if not node_token or not bridge_token:
        raise SystemExit('Configure node-token and bridge-token first')
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
                    await send({'type':'hello','protocol':2,'agent_revision':'2','bridge_revision':getattr(a,'bridge_revision','unknown')})
                    async def refresh_models():
                        nonlocal current_models
                        while True:
                            try:
                                r = await http.get('/v1/models',headers={'Authorization':f'Bearer {bridge_token}'})
                                r.raise_for_status()
                                names = [x['id'] for x in r.json().get('data',[]) if isinstance(x.get('id'),str)]
                                current_models=set(names)
                                await send({'type':'models','models':names})
                                report('models', names)
                            except Exception as e:
                                print('Model discovery failed:',type(e).__name__,flush=True)
                                report('bridge_error', type(e).__name__)
                                current_models=set()
                                await send({'type':'models','models':[]})
                            await asyncio.sleep(60)
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
                            results=await probes.run_model(http,model,bridge_token,
                                lambda case,result: report('probe',f'{model} {case}: '+('通过' if result is True else str(result))))
                            await send({'type':'validation','model':model,'probe_id':m.get('probe_id'),'results':results})
                            report('validation', {'model':model,'results':results,
                                                  'passed':all(x is True for x in results.values())})
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
                print('Disconnected:',type(e).__name__,str(e)[:120],flush=True)
                report('disconnected', type(e).__name__ + ': ' + str(e)[:120])
                await asyncio.sleep(delay)
                delay=min(delay*2,30)

if __name__=='__main__':
    a=config()
    if a.command=='configure':
        node_token = a.node_token or getpass.getpass('Gateway node token: ')
        bridge_token = a.bridge_token or getpass.getpass('Local bridge token: ')
        if not node_token or not bridge_token:
            raise SystemExit('Both tokens are required')
        keyring.set_password(SERVICE,'node-token',node_token)
        keyring.set_password(SERVICE,'bridge-token',bridge_token)
        print('Credentials stored in Windows Credential Manager')
    else:
        asyncio.run(main(a))
