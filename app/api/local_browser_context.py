"""Direct-loopback selected-organization fence for local-browser transport."""
from __future__ import annotations
import asyncio, base64, json, uuid, ipaddress
from dataclasses import dataclass
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from app.api.remote_auth import verify_supabase_token, headers_indicate_tunnel
from app.services.sync_client import get_sync_client

router=APIRouter(prefix='/local-browser', tags=['local-browser'])
class ContextBody(BaseModel):
 model_config=ConfigDict(extra='forbid', strict=True)
 engine_boot_id:str; expected_revision:int; organization_id:str|None
@dataclass
class ContextState:
 boot_id:str; revision:int=0; organization_id:str|None=None; lock:asyncio.Lock=None
 def __post_init__(self): self.lock=asyncio.Lock()
def _claim(token:str)->tuple[str,str]:
 try:
  part=token.split('.')[1]; data=json.loads(base64.urlsafe_b64decode((part+'='*(-len(part)%4)).encode()))
  sub,sid=data['sub'],data['session_id']; uuid.UUID(sid)
  if not isinstance(sub,str) or str(uuid.UUID(sid))!=sid: raise ValueError
  return sub,sid
 except Exception: raise HTTPException(401,'local_browser_context_refused')
def _state(request:Request)->ContextState:
 state=getattr(request.app.state,'local_browser_context',None)
 if state is None:
  state=ContextState(str(uuid.uuid4())); request.app.state.local_browser_context=state
 return state
async def _owner(request:Request)->tuple[str,str]:
 try:
  if headers_indicate_tunnel(request.headers) or request.client is None or not ipaddress.ip_address(request.client.host).is_loopback: raise ValueError
 except ValueError: raise HTTPException(403,'local_browser_context_refused')
 auth=request.headers.get('authorization','')
 if not auth.lower().startswith('bearer '): raise HTTPException(401,'local_browser_context_refused')
 token=auth[7:].strip(); desktop=await verify_supabase_token(token)
 grant=await get_sync_client().access_grant()
 if desktop is None or grant is None: raise HTTPException(401,'local_browser_context_refused')
 user,sid=_claim(grant[0]); inbound_user,inbound_sid=_claim(token)
 if desktop.user_id!=user or grant[1]!=user or inbound_user!=user or inbound_sid!=sid: raise HTTPException(401,'local_browser_context_refused')
 return user,sid
@router.get('/context')
async def get_context(request:Request):
 await _owner(request); state=_state(request)
 return {'engine_boot_id':state.boot_id,'revision':state.revision,'organization_id':state.organization_id}
@router.post('/context')
async def set_context(body:ContextBody,request:Request):
 await _owner(request); state=_state(request)
 if body.organization_id is not None:
  try: uuid.UUID(body.organization_id)
  except ValueError: raise HTTPException(400,'local_browser_context_refused')
 async with state.lock:
  if body.engine_boot_id!=state.boot_id or body.expected_revision!=state.revision: raise HTTPException(409,'local_browser_context_conflict')
  await _owner(request)
  state.organization_id=body.organization_id; state.revision+=1
  return {'engine_boot_id':state.boot_id,'revision':state.revision,'organization_id':state.organization_id}
