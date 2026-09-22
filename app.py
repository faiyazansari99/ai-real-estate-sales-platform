import os,json,uuid,re
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI,HTTPException,UploadFile,File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import requests
BASE=Path(__file__).resolve().parent.parent; DATA=BASE/'data'; DATA.mkdir(exist_ok=True); DB=DATA/'db.json'; UP=DATA/'uploads'; UP.mkdir(exist_ok=True)
load_dotenv(BASE/'backend'/'.env')
DEFAULT={'settings':{'brand':'EstateAI','phone':'+91 98765 43210','whatsapp':'+919876543210','currency':'₹','ai_greeting':'Hi! I’m your AI property advisor. Tell me your location, budget and property requirement.','business_hours':'Mon-Sat 9:00 AM-7:00 PM'},'properties':[{'id':'p1','title':'Skyline Residency','price':7500000,'location':'Pune, Maharashtra','bhk':'2 BHK','area':'1,050 sq.ft','type':'Apartment','status':'Ready to Move','amenities':['Parking','Gym','Lift','Security','Clubhouse'],'description':'Modern 2 BHK apartment with premium amenities.','rera':'P52100000000','published':True,'views':2841},{'id':'p2','title':'Lakeview Heights','price':9800000,'location':'Pune, Maharashtra','bhk':'3 BHK','area':'1,420 sq.ft','type':'Apartment','status':'Under Construction','amenities':['Pool','Gym','Parking','Garden'],'description':'Spacious 3 BHK family residence.','rera':'P52100000001','published':True,'views':1640}],'leads':[],'visits':[],'messages':[],'notifications':[],'team':[{'id':'u1','name':'Owner','email':'owner@example.com','role':'Owner','status':'Active'}],'audit':[]}
def load():
 if not DB.exists(): save(DEFAULT)
 return json.loads(DB.read_text())
def save(d): DB.write_text(json.dumps(d,indent=2))
def now(): return datetime.utcnow().isoformat()+'Z'
app=FastAPI(title='EstateAI')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])
class Chat(BaseModel): message:str; session_id:str='public'
class Lead(BaseModel): name:str; phone:str; email:str=''; budget:str=''; location:str=''; bhk:str=''; property_id:str=''; source:str='AI Chatbot'
class Settings(BaseModel): brand:str='EstateAI';phone:str='';whatsapp:str='';currency:str='₹';ai_greeting:str='';business_hours:str=''
@app.get('/')
def home(): return FileResponse(BASE/'frontend'/'index.html')
@app.get('/api/settings')
def settings(): return load()['settings']
@app.get('/api/properties')
def properties(): return [p for p in load()['properties'] if p.get('published',True)]
@app.get('/api/owner/data')
def owner_data():
 d=load(); return {k:d[k] for k in ['settings','properties','leads','visits','messages','notifications','team','audit']}
@app.post('/api/settings')
def update_settings(s:Settings):
 d=load();d['settings']=s.model_dump();d['audit'].append({'at':now(),'action':'settings.updated'});save(d);return d['settings']
@app.post('/api/leads')
def create_lead(l:Lead):
 d=load();item={'id':str(uuid.uuid4()),**l.model_dump(),'status':'New','score':'WARM','created_at':now()};d['leads'].insert(0,item);d['notifications'].insert(0,{'id':str(uuid.uuid4()),'type':'lead','title':'New lead','text':f'{l.name} submitted an enquiry','at':now(),'read':False});save(d);return item
@app.post('/api/visits')
def create_visit(payload:dict):
 d=load();item={'id':str(uuid.uuid4()),**payload,'status':'Pending','created_at':now()};d['visits'].insert(0,item);d['notifications'].insert(0,{'id':str(uuid.uuid4()),'type':'visit','title':'New site visit','text':f'{payload.get("name","Customer")} requested a visit','at':now(),'read':False});save(d);return item
@app.post('/api/properties')
def add_property(p:dict):
 d=load();p['id']=str(uuid.uuid4());p.setdefault('published',True);p.setdefault('views',0);d['properties'].insert(0,p);save(d);return p
@app.put('/api/properties/{pid}')
def edit_property(pid:str,p:dict):
 d=load()
 for i,x in enumerate(d['properties']):
  if x['id']==pid:p['id']=pid;d['properties'][i]=p;save(d);return p
 raise HTTPException(404,'Property not found')
@app.delete('/api/properties/{pid}')
def delete_property(pid:str):
 d=load();d['properties']=[p for p in d['properties'] if p['id']!=pid];save(d);return {'ok':True}
@app.post('/api/upload')
async def upload(file:UploadFile=File(...)):
 ext=Path(file.filename or '').suffix.lower()
 if ext not in ['.jpg','.jpeg','.png','.webp','.mp4','.pdf']:raise HTTPException(400,'Unsupported file type')
 name=f'{uuid.uuid4()}{ext}';(UP/name).write_bytes(await file.read());return {'url':f'/uploads/{name}','name':name}
@app.get('/uploads/{name}')
def uploaded(name:str):
 p=UP/name
 if not p.exists():raise HTTPException(404)
 return FileResponse(p)
@app.post('/api/chat')
def chat(c:Chat):
 d=load();props=[p for p in d['properties'] if p.get('published',True)];u=c.message.lower();matches=[]
 for p in props:
  if p['title'].lower() in u or p['location'].split(',')[0].lower() in u or p['bhk'].lower() in u:matches.append(p)
 m=re.search(r'(\d+(?:\.\d+)?)\s*(lakh|lac|cr|crore|l)',u)
 if m:
  b=float(m.group(1))*(10000000 if m.group(2) in ['cr','crore'] else 100000);matches=[p for p in props if p['price']<=b]
 context='\n'.join(f"{p['title']}: {p['bhk']}, {p['location']}, {d['settings']['currency']}{p['price']/100000:.0f}L, {p['status']}" for p in (matches or props[:5]))
 system=f"You are a careful real-estate sales assistant for {d['settings']['brand']}. Only use the property data below. Never invent facts. If unknown say so. Phone: {d['settings']['phone']} WhatsApp: {d['settings']['whatsapp']}\nProperties:\n{context}"
 answer=None;key=os.getenv('GROQ_API_KEY','').strip()
 if key and not key.startswith('PASTE_'):
  try:
   r=requests.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},json={'model':os.getenv('GROQ_MODEL','llama-3.3-70b-versatile'),'messages':[{'role':'system','content':system},{'role':'user','content':c.message}],'temperature':0.2},timeout=30)
   if r.ok:answer=r.json()['choices'][0]['message']['content']
  except Exception:pass
 if not answer:answer=f"I can help with properties, budgets and site visits. You may want to explore: {', '.join(p['title'] for p in (matches or props[:3]))}. You can also use WhatsApp or Call to speak with the property team."
 d['messages'].append({'id':str(uuid.uuid4()),'session_id':c.session_id,'user':c.message,'assistant':answer,'at':now()});save(d);return {'answer':answer,'matches':matches[:6],'phone':d['settings']['phone'],'whatsapp':d['settings']['whatsapp']}
@app.get('/api/health')
def health():return {'ok':True,'time':now()}
if __name__=='__main__':
 import uvicorn;uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','8000')))
