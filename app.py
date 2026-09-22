import os, json, uuid, hmac, hashlib, base64, re
from datetime import datetime, timezone
from pathlib import Path
import requests
from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parent.parent
load_dotenv(ROOT/'.env')
DB=ROOT/'data'/'db.json'; DB.parent.mkdir(exist_ok=True)
JWT_SECRET=os.getenv('JWT_SECRET','CHANGE_ME_IN_VERCEL')
OWNER_USER=os.getenv('OWNER_USERNAME','owner'); OWNER_PASS=os.getenv('OWNER_PASSWORD','change-me-now')
COOKIE='estate_session'; SESSION_DAYS=int(os.getenv('SESSION_DAYS','30')); MAX_UPLOAD_MB=int(os.getenv('MAX_UPLOAD_MB','10'))
DEFAULT={'settings':{'brand':'EstateAI','owner_name':'Property Owner','owner_email':'','phone':'+91 98765 43210','whatsapp':'+919876543210','currency':'₹','ai_greeting':'Hi! I’m your AI property advisor. Tell me your location, budget and requirement.','business_hours':'Mon-Sat 9:00 AM-7:00 PM','about':'AI-powered real estate sales and customer assistance.'},'properties':[{'id':'p1','title':'Skyline Residency','price':7500000,'location':'Pune, Maharashtra','bhk':'2 BHK','area':'1,050 sq.ft','type':'Apartment','status':'Ready to Move','amenities':['Parking','Gym','Lift','Security','Clubhouse'],'description':'Modern 2 BHK apartment with premium amenities.','rera':'P52100000000','published':True,'views':0,'images':[],'vision':[]}],'leads':[],'visits':[],'messages':[],'notifications':[],'team':[],'audit':[],'metrics':{'searches':0,'calls':0,'whatsapp':0,'bookings':0}}

def load():
    if not DB.exists(): save(DEFAULT)
    try: return json.loads(DB.read_text())
    except Exception: save(DEFAULT); return json.loads(DB.read_text())
def save(d):
    tmp=DB.with_suffix('.tmp'); tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2)); tmp.replace(DB)
def now(): return datetime.now(timezone.utc).isoformat()
def audit(d,action,meta=None): d['audit'].insert(0,{'id':str(uuid.uuid4()),'at':now(),'action':action,'meta':meta or {}})
def public_props(d): return [p for p in d['properties'] if p.get('published') and p.get('status') not in ['Draft','Archived']]
def token(user,days):
    payload={'u':user,'role':'owner','exp':int(datetime.now(timezone.utc).timestamp())+days*86400}; body=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('='); sig=hmac.new(JWT_SECRET.encode(),body.encode(),hashlib.sha256).digest(); return body+'.'+base64.urlsafe_b64encode(sig).decode().rstrip('=')
def verify(t):
    try:
        body,s=t.split('.',1); sig=base64.urlsafe_b64decode(s+'=='); good=hmac.new(JWT_SECRET.encode(),body.encode(),hashlib.sha256).digest()
        if not hmac.compare_digest(sig,good): return None
        p=json.loads(base64.urlsafe_b64decode(body+'==')); return p if p.get('role')=='owner' and p.get('exp',0)>int(datetime.now(timezone.utc).timestamp()) else None
    except: return None
def owner(request:Request):
    p=verify(request.cookies.get(COOKIE,''))
    if not p: raise HTTPException(401,'Owner login required')
    return p

def groq(system,user,model=None,image_data=None):
    key=os.getenv('GROQ_API_KEY','').strip()
    if not key: return None
    model=model or os.getenv('GROQ_TEXT_MODEL','openai/gpt-oss-120b')
    content=[{'type':'text','text':user}]
    if image_data: content.append({'type':'image_url','image_url':{'url':image_data}})
    try:
        r=requests.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},json={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':content}],'temperature':0.2,'max_completion_tokens':1400},timeout=35)
        if r.ok: return r.json()['choices'][0]['message']['content']
    except Exception: pass
    return None

app=FastAPI(title='EstateAI Real Estate Sales Platform',version='2.0')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])
class Login(BaseModel): username:str; password:str; remember:bool=False
class Chat(BaseModel): message:str; session_id:str='public'
class Lead(BaseModel): name:str=Field(min_length=2); phone:str=Field(min_length=5); email:str=''; budget:str=''; location:str=''; bhk:str=''; property_id:str=''; source:str='Website'; consent:bool=True
class Visit(BaseModel): name:str=Field(min_length=2); phone:str=Field(min_length=5); email:str=''; date:str; time:str; property_id:str=''; notes:str=''; consent:bool=True
class Settings(BaseModel): brand:str='EstateAI'; owner_name:str='Property Owner'; owner_email:str=''; phone:str=''; whatsapp:str=''; currency:str='₹'; ai_greeting:str=''; business_hours:str=''; about:str=''

@app.get('/')
def home(): return FileResponse(ROOT/'frontend'/'index.html')
@app.get('/api/health')
def health(): return {'ok':True,'time':now()}
@app.post('/api/auth/login')
def login(x:Login):
    if x.username!=OWNER_USER or not hmac.compare_digest(x.password,OWNER_PASS): raise HTTPException(401,'Invalid username or password')
    t=token(x.username,SESSION_DAYS if x.remember else 1); r=JSONResponse({'ok':True,'username':x.username,'remembered':x.remember})
    r.set_cookie(COOKIE,t,max_age=SESSION_DAYS*86400 if x.remember else None,httponly=True,secure=True,samesite='lax',path='/'); d=load(); audit(d,'auth.login',{'username':x.username}); save(d); return r
@app.post('/api/auth/logout')
def logout(): r=JSONResponse({'ok':True}); r.delete_cookie(COOKIE,path='/'); return r
@app.get('/api/auth/me')
def me(request:Request):
    p=verify(request.cookies.get(COOKIE,'')); return {'authenticated':bool(p),'username':p.get('u') if p else None}
@app.get('/api/settings')
def settings(): return load()['settings']
@app.get('/api/properties')
def properties(): return public_props(load())
@app.get('/api/properties/{pid}')
def property_detail(pid:str):
    d=load(); p=next((x for x in public_props(d) if x['id']==pid),None)
    if not p: raise HTTPException(404,'Property not found')
    p['views']=p.get('views',0)+1; save(d); return p

@app.post('/api/leads')
def create_lead(l:Lead):
    d=load(); item={'id':str(uuid.uuid4()),**l.model_dump(),'status':'New','score':'Warm','created_at':now()}; d['leads'].insert(0,item); d['notifications'].insert(0,{'id':str(uuid.uuid4()),'type':'lead','title':'New customer lead','text':f'{l.name} submitted an enquiry','at':now(),'read':False}); d['metrics']['bookings']+=1; audit(d,'lead.created',{'lead_id':item['id']}); save(d); return item
@app.post('/api/visits')
def create_visit(v:Visit):
    d=load(); item={'id':str(uuid.uuid4()),**v.model_dump(),'status':'Pending','created_at':now()}; d['visits'].insert(0,item); d['notifications'].insert(0,{'id':str(uuid.uuid4()),'type':'visit','title':'New site-visit request','text':f'{v.name} requested a visit','at':now(),'read':False}); audit(d,'visit.created',{'visit_id':item['id']}); save(d); return {'ok':True,'visit':item,'message':'Request submitted. The property team will confirm the visit.'}

@app.get('/api/owner/data')
def owner_data(_:dict=Depends(owner)):
    d=load(); return {k:d[k] for k in ['settings','properties','leads','visits','messages','notifications','team','audit','metrics']}
@app.post('/api/settings')
def update_settings(s:Settings,_:dict=Depends(owner)):
    d=load(); d['settings']=s.model_dump(); audit(d,'settings.updated'); save(d); return d['settings']
@app.post('/api/properties')
def add_property(p:dict,_:dict=Depends(owner)):
    d=load(); p=dict(p); p['id']=str(uuid.uuid4()); p.setdefault('published',False); p.setdefault('views',0); p.setdefault('images',[]); p.setdefault('vision',[]); d['properties'].insert(0,p); audit(d,'property.created',{'id':p['id']}); save(d); return p
@app.put('/api/properties/{pid}')
def edit_property(pid:str,p:dict,_:dict=Depends(owner)):
    d=load()
    for i,x in enumerate(d['properties']):
        if x['id']==pid:
            p=dict(p); p['id']=pid; p.setdefault('images',x.get('images',[])); p.setdefault('vision',x.get('vision',[])); d['properties'][i]=p; audit(d,'property.updated',{'id':pid}); save(d); return p
    raise HTTPException(404,'Property not found')
@app.delete('/api/properties/{pid}')
def delete_property(pid:str,_:dict=Depends(owner)):
    d=load(); before=len(d['properties']); d['properties']=[p for p in d['properties'] if p['id']!=pid]
    if len(d['properties'])==before: raise HTTPException(404,'Property not found')
    audit(d,'property.deleted',{'id':pid}); save(d); return {'ok':True}
@app.post('/api/properties/{pid}/publish')
def publish(pid:str,published:bool=True,_:dict=Depends(owner)):
    d=load(); p=next((x for x in d['properties'] if x['id']==pid),None)
    if not p: raise HTTPException(404,'Property not found')
    p['published']=published; audit(d,'property.published' if published else 'property.unpublished',{'id':pid}); save(d); return p

@app.post('/api/properties/{pid}/images')
async def image_upload(pid:str,file:UploadFile=File(...),_:dict=Depends(owner)):
    d=load(); p=next((x for x in d['properties'] if x['id']==pid),None)
    if not p: raise HTTPException(404,'Property not found')
    if not (file.content_type or '').startswith('image/'): raise HTTPException(400,'Only image files are allowed')
    raw=await file.read()
    if len(raw)>MAX_UPLOAD_MB*1024*1024: raise HTTPException(413,f'Max image size is {MAX_UPLOAD_MB} MB')
    data_url=f"data:{file.content_type};base64,{base64.b64encode(raw).decode()}"
    prompt='Analyze this real-estate property image. Extract only visually supported facts: property type, rooms/BHK only if clearly visible, notable amenities/features, visible text, condition/style and useful customer-facing description. Never guess exact price, location, owner, area or legal facts. Return concise JSON or concise bullets.'
    analysis=groq('You are a careful visual property analyst. Never invent facts.',prompt,os.getenv('GROQ_VISION_MODEL','qwen/qwen3.8-27b'),data_url) or 'Vision analysis unavailable. Add GROQ_API_KEY to enable image understanding.'
    item={'id':str(uuid.uuid4()),'name':file.filename,'content_type':file.content_type,'data_url':data_url,'analysis':analysis}; p.setdefault('images',[]).append(item); p.setdefault('vision',[]).append(analysis); audit(d,'property.image.uploaded',{'property_id':pid,'image_id':item['id']}); save(d); return {'ok':True,'image':item,'analysis':analysis}

@app.post('/api/chat')
def chat(c:Chat):
    d=load(); props=public_props(d); d['metrics']['searches']+=1; u=c.message.lower(); matches=[]
    for p in props:
        hay=' '.join(map(str,[p.get('title',''),p.get('location',''),p.get('bhk',''),p.get('type',''),p.get('description',''),*p.get('amenities',[]),*p.get('vision',[])] )).lower()
        words=[w for w in re.findall(r'[a-z0-9₹]+',u) if len(w)>2]
        if any(w in hay for w in words): matches.append(p)
    m=re.search(r'(\d+(?:\.\d+)?)\s*(lakh|lac|cr|crore|l)',u)
    if m:
        budget=float(m.group(1))*(10000000 if m.group(2) in ['cr','crore'] else 100000); matches=[p for p in props if float(p.get('price',0))<=budget]
    if not matches: matches=props[:8]
    data=[]
    for p in matches[:8]: data.append({'id':p['id'],'title':p.get('title'),'price':p.get('price'),'location':p.get('location'),'bhk':p.get('bhk'),'area':p.get('area'),'type':p.get('type'),'status':p.get('status'),'amenities':p.get('amenities',[]),'description':p.get('description',''),'rera':p.get('rera',''),'image_observations':p.get('vision',[])})
    s=d['settings']; system=(f"You are {s['brand']}'s real-estate sales assistant. Answer in the customer's language. Use ONLY the published property/business data supplied. You may explain uploaded-image observations, but label them as visual observations. Never invent price, availability, location, BHK, legal status, owner identity or other facts. If unknown, say it is not provided. You may answer public owner/business contact information configured in settings. If customer wants to buy/book, use the website lead/visit form and never claim a purchase is completed. Public owner/contact: {s['owner_name']} / {s['owner_email']} / {s['phone']} / {s['whatsapp']}. Business: {s['about']}. Data: {json.dumps(data,ensure_ascii=False)}")
    answer=groq(system,c.message) or f"I can help with published properties, prices, locations, BHK, amenities, owner/business contact and site visits. Relevant options: {', '.join(p['title'] for p in matches[:4]) or 'No matching property found.'}"
    d['messages'].append({'id':str(uuid.uuid4()),'session_id':c.session_id,'user':c.message,'assistant':answer,'matches':[p['id'] for p in matches[:6]],'at':now()}); save(d); return {'answer':answer,'matches':matches[:6],'phone':s['phone'],'whatsapp':s['whatsapp']}

if __name__=='__main__':
    import uvicorn; uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','8000')))
