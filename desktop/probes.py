"""Bounded, credential-local capability checks for a WorkBuddy bridge."""

import base64
import binascii
import json
import struct
import zlib

import httpx

CASES = [f'{api}_{case}' for api in ('chat','responses','messages') for case in ('text','stream','tool','image')]
PROMPT = 'Reply with one short word.'
IMAGE_PROMPT = 'What is the main color of this image? Answer with one color word.'


def red_png():
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',binascii.crc32(kind+data)&0xffffffff)
    pixels = (b'\x00'+b'\xff\x00\x00'*64)*64
    image = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',64,64,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')
    return base64.b64encode(image).decode()


IMAGE = red_png()
DATA_URI = 'data:image/png;base64,'+IMAGE


def text_of(api, data):
    if api=='chat':
        return str(data.get('choices',[{}])[0].get('message',{}).get('content') or '')
    if api=='responses':
        if isinstance(data.get('output_text'),str): return data['output_text']
        return ' '.join(str(part.get('text','')) for item in data.get('output',[]) if item.get('type')=='message' for part in item.get('content',[]))
    return ' '.join(str(part.get('text','')) for part in data.get('content',[]) if part.get('type')=='text')


def path(api): return {'chat':'/v1/chat/completions','responses':'/v1/responses','messages':'/v1/messages'}[api]


def base_body(api, model, prompt=PROMPT, stream=False):
    if api=='chat': return {'model':model,'messages':[{'role':'user','content':prompt}],'max_tokens':80,'stream':stream}
    if api=='responses': return {'model':model,'input':prompt,'max_output_tokens':80,'stream':stream,'store':False}
    return {'model':model,'messages':[{'role':'user','content':prompt}],'max_tokens':80,'stream':stream}


async def post(http: httpx.AsyncClient, api: str, model: str, token: str, body):
    headers={'Authorization':'Bearer '+token,'x-api-key':token,'anthropic-version':'2023-06-01'}
    response=await http.post(path(api),headers=headers,json=body,timeout=45)
    if response.status_code != 200:
        raise ValueError(f'HTTP {response.status_code}')
    return response


async def check_text(http, api, model, token):
    data=(await post(http,api,model,token,base_body(api,model))).json()
    if not text_of(api,data).strip(): raise ValueError('empty text')


async def check_stream(http, api, model, token):
    response=await post(http,api,model,token,base_body(api,model,stream=True))
    if 'text/event-stream' not in response.headers.get('content-type',''):
        raise ValueError('not SSE')
    body=response.text
    marker={'chat':'data: [DONE]','responses':'event: response.completed','messages':'event: message_stop'}[api]
    if marker not in body: raise ValueError('missing stream completion')


async def check_image(http, api, model, token):
    body=base_body(api,model)
    if api=='chat':
        body['messages'][0]['content']=[{'type':'text','text':IMAGE_PROMPT},{'type':'image_url','image_url':{'url':DATA_URI}}]
    elif api=='responses':
        body['input']=[{'role':'user','content':[{'type':'input_text','text':IMAGE_PROMPT},{'type':'input_image','image_url':DATA_URI}]}]
    else:
        body['messages'][0]['content']=[{'type':'text','text':IMAGE_PROMPT},{'type':'image','source':{'type':'base64','media_type':'image/png','data':IMAGE}}]
    data=(await post(http,api,model,token,body)).json()
    answer=text_of(api,data).lower()
    if 'red' not in answer and '红' not in answer:
        raise ValueError('image color not recognized')


async def check_tool(http, api, model, token):
    name='gateway_echo_value'
    prompt='Call gateway_echo_value exactly once and report the returned value.'
    body=base_body(api,model,prompt)
    if api=='chat':
        tools=[{'type':'function','function':{'name':name,'description':'Get the test value','parameters':{'type':'object','properties':{},'required':[]}}}]
        body.update(tools=tools,tool_choice={'type':'function','function':{'name':name}})
        first=(await post(http,api,model,token,body)).json()
        message=first['choices'][0]['message']
        calls=message.get('tool_calls') or []
        if not calls or calls[0].get('function',{}).get('name')!=name: raise ValueError('tool call missing')
        body['messages'] += [message,{'role':'tool','tool_call_id':calls[0]['id'],'content':'42'}]
        body.pop('tool_choice',None)
    elif api=='responses':
        tools=[{'type':'function','name':name,'description':'Get the test value','parameters':{'type':'object','properties':{},'required':[]}}]
        body.update(tools=tools,tool_choice='required')
        first=(await post(http,api,model,token,body)).json()
        calls=[x for x in first.get('output',[]) if x.get('type')=='function_call' and x.get('name')==name]
        if not calls: raise ValueError('tool call missing')
        body['input']=[{'role':'user','content':[{'type':'input_text','text':prompt}]},*first['output'],{'type':'function_call_output','call_id':calls[0]['call_id'],'output':'42'}]
        body.pop('tool_choice',None)
    else:
        tools=[{'name':name,'description':'Get the test value','input_schema':{'type':'object','properties':{},'required':[]}}]
        body.update(tools=tools,tool_choice={'type':'tool','name':name})
        first=(await post(http,api,model,token,body)).json()
        calls=[x for x in first.get('content',[]) if x.get('type')=='tool_use' and x.get('name')==name]
        if not calls: raise ValueError('tool call missing')
        body['messages'] += [{'role':'assistant','content':first['content']},{'role':'user','content':[{'type':'tool_result','tool_use_id':calls[0]['id'],'content':'42'}]}]
        body.pop('tool_choice',None)
    data=(await post(http,api,model,token,body)).json()
    if not text_of(api,data).strip(): raise ValueError('tool result not consumed')


async def run_model(http: httpx.AsyncClient, model: str, token: str, report=None):
    results={}
    checks={'text':check_text,'stream':check_stream,'tool':check_tool,'image':check_image}
    for api in ('chat','responses','messages'):
        for name,check in checks.items():
            case=f'{api}_{name}'
            try:
                await check(http,api,model,token)
                results[case]=True
            except (httpx.HTTPError,ValueError,KeyError,IndexError,TypeError,json.JSONDecodeError) as exc:
                results[case]=type(exc).__name__+': '+str(exc)[:90]
            if report: report(case,results[case])
    return results
