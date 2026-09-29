import os, json, uuid, hmac, hashlib, base64, re, copy, threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from collections import defaultdict
from time import time as time_now

import requests
from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# ============================================================
# CONFIG
# ============================================================
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')

DB = ROOT / 'data' / 'db.json'
DB.parent.mkdir(exist_ok=True)

JWT_SECRET = os.getenv('JWT_SECRET', '').strip()
OWNER_USER = os.getenv('OWNER_USERNAME', 'owner').strip()
OWNER_PASS = os.getenv('OWNER_PASSWORD', '').strip()
COOKIE = 'estate_session'
CUSTOMER_COOKIE = 'estate_customer'
CHAT_COOKIE = 'estate_chat'
SESSION_DAYS = int(os.getenv('SESSION_DAYS', '30'))
MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '2'))
CLOUDINARY_URL = os.getenv('CLOUDINARY_URL', '').strip()
SECURE_COOKIE = os.getenv('SECURE_COOKIE', 'false').lower() == 'true'
COOKIE_SAMESITE = os.getenv('COOKIE_SAMESITE', 'lax').lower()
if COOKIE_SAMESITE not in {'lax', 'strict', 'none'}:
    COOKIE_SAMESITE = 'lax'

if not JWT_SECRET or len(JWT_SECRET) < 32:
    raise RuntimeError('JWT_SECRET must be configured and at least 32 characters long')
if not OWNER_PASS:
    raise RuntimeError('OWNER_PASSWORD must be configured before starting the application')

ph = PasswordHasher()
_SESSION_CACHE = {}
_SESSION_LOCK = threading.Lock()

_RATE_LIMIT = defaultdict(list)
_RATE_WINDOW = 60
_RATE_MAX = 15

# ============================================================
# DEFAULT DATA
# ============================================================
DEFAULT = {
    'settings': {
        'brand': 'EstateAI',
        'tagline': 'Find a place you will love to call home.',
        'owner_name': 'Property Owner',
        'owner_email': '',
        'phone': '',
        'whatsapp': '',
        'currency': '₹',
        'ai_greeting': 'Hi! Main aapka AI property advisor hu. Aap location, budget, BHK ya property type bataiye — main best options dhundhne mein help karunga.',
        'business_hours': 'Mon-Sat 9:00 AM-7:00 PM',
        'about': 'A premium AI-powered real estate sales experience.',
        'address': '',
        'logo_url': '',
        'hero_image': '',
        'ai_business_context': (
            'You are a professional real-estate assistant for this business. '
            'You help customers in 3 ways: '
            '(1) PROPERTY SEARCH - when customer asks for properties by location, budget, BHK, type, '
            'search published property data and show matching options with price, location, area, amenities. '
            '(2) GENERAL KNOWLEDGE - when customer asks about EMI, home loans, RERA, stamp duty, '
            'registration process, documents, taxes - answer helpfully using your knowledge. '
            '(3) LOCALITY and MARKET - when customer asks about an area (schools, safety, connectivity, '
            'investment potential), give helpful context. '
            'RULES: For property-specific facts use ONLY published property data. '
            'Never invent property facts, prices, availability, or legal claims. '
            'For general questions answer confidently. '
            'LANGUAGE RULE (CRITICAL): Always detect the language of the customer message and reply in EXACTLY the same language. '
            'If customer writes in Hindi (Devanagari), reply in Hindi. '
            'If customer writes in Hinglish (Roman Hindi like "kya haal hai"), reply in Hinglish. '
            'If customer writes in English, reply in English. '
            'If customer writes in Tamil, Telugu, Marathi, Bengali, Gujarati, Punjabi, Urdu, Arabic, Spanish, French, German, or ANY other language, reply in that SAME language. '
            'NEVER switch languages on your own. Match the customer tone too: casual, formal, or friendly. '
            'Keep replies short (2-5 lines). Use bullet points for multiple properties. '
            'Always end with a call-to-action: call, WhatsApp, or site visit.'
        ),
    },
    'properties': [],
    'customers': [],
    'leads': [],
    'visits': [],
    'messages': [],
    'notifications': [],
    'audit': [],
    'metrics': {
        'searches': 0, 'calls': 0, 'whatsapp': 0, 'bookings': 0,
        'enquiries': 0, 'property_views': 0, 'chat_sessions': 0,
    },
    'next': 1,
}

# ============================================================
# DB
# ============================================================
def load():
    if not DB.exists():
        save(copy.deepcopy(DEFAULT))
    try:
        d = json.loads(DB.read_text(encoding='utf-8'))
        for k, v in DEFAULT.items():
            if k not in d:
                d[k] = copy.deepcopy(v)
        return d
    except Exception:
        save(copy.deepcopy(DEFAULT))
        return json.loads(DB.read_text(encoding='utf-8'))

def save(d):
    tmp = DB.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(DB)

# ============================================================
# HELPERS
# ============================================================
def now():
    return datetime.now(timezone.utc).isoformat()

def uid(prefix=''):
    return prefix + str(uuid.uuid4())

def audit(d, action, meta=None):
    d['audit'].insert(0, {'id': uid('a_'), 'at': now(), 'action': action, 'meta': meta or {}})

def public_props(d):
    return [p for p in d['properties'] if p.get('published') and p.get('status', '') not in ['Draft', 'Archived']]

def normalize_phone(v):
    return re.sub(r'[^0-9+]', '', v or '')

def whatsapp_url(v):
    n = normalize_phone(v).replace('+', '')
    return f'https://wa.me/{n}' if n else ''

def hash_password(password: str) -> str:
    return ph.hash(password)

def verify_password(password_hash: str, password: str) -> bool:
    try:
        if password_hash.startswith('$argon2'):
            return ph.verify(password_hash, password)
        if re.fullmatch(r'[0-9a-f]{64}', password_hash or ''):
            return hmac.compare_digest(password_hash, hashlib.sha256(password.encode()).hexdigest())
        return False
    except VerifyMismatchError:
        return False
    except Exception:
        return False

def get_session_history(d, session_id, limit=8):
    cached = _SESSION_CACHE.get(session_id)
    if cached is not None:
        return cached[-limit:]
    hist = [m for m in d['messages'] if m.get('session_id') == session_id][-limit:]
    _SESSION_CACHE[session_id] = hist[-20:]
    return _SESSION_CACHE[session_id][-limit:]

def append_session_message(session_id, item):
    with _SESSION_LOCK:
        hist = _SESSION_CACHE.setdefault(session_id, [])
        hist.append(item)
        if len(hist) > 20:
            del hist[:-20]

def check_rate_limit(ip: str):
    now_ts = time_now()
    _RATE_LIMIT[ip] = [t for t in _RATE_LIMIT[ip] if now_ts - t < _RATE_WINDOW]
    if len(_RATE_LIMIT[ip]) >= _RATE_MAX:
        raise HTTPException(429, 'Too many requests. Please wait a minute.')
    _RATE_LIMIT[ip].append(now_ts)

# ============================================================
# TOKEN / AUTH
# ============================================================
def make_token(user, role, days=1):
    payload = {
        'u': user, 'role': role,
        'exp': int(datetime.now(timezone.utc).timestamp()) + days * 86400,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    sig = hmac.new(JWT_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return body + '.' + base64.urlsafe_b64encode(sig).decode().rstrip('=')

def verify(t, role=None):
    try:
        body, s = t.split('.', 1)
        sig = base64.urlsafe_b64decode(s + '==')
        good = hmac.new(JWT_SECRET.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(sig, good):
            return None
        p = json.loads(base64.urlsafe_b64decode(body + '=='))
        if p.get('exp', 0) <= int(datetime.now(timezone.utc).timestamp()):
            return None
        if role and p.get('role') != role:
            return None
        return p
    except Exception:
        return None

def owner(request: Request):
    p = verify(request.cookies.get(COOKIE, ''), 'owner')
    if not p:
        raise HTTPException(401, 'Owner login required')
    return p

def customer(request: Request):
    p = verify(request.cookies.get(CUSTOMER_COOKIE, ''), 'customer')
    if not p:
        raise HTTPException(401, 'Customer login required')
    return p

def optional_customer(request: Request):
    return verify(request.cookies.get(CUSTOMER_COOKIE, ''), 'customer')

def chat_session(request: Request):
    token = request.cookies.get(CHAT_COOKIE, '')
    if token and re.fullmatch(r's_[A-Za-z0-9_-]{20,100}', token):
        return token, False
    return 's_' + uuid.uuid4().hex, True

def detect_query_type(message: str, has_image: bool = False) -> str:
    if has_image:
        return 'vision'
    text = message.lower()
    property_kw = ['bhk', 'flat', 'apartment', 'villa', 'plot', 'property', 'rent', 'buy',
                   'under', 'lakh', 'crore', 'sqft', 'sq ft', 'pune', 'mumbai', 'bangalore',
                   'delhi', 'hyderabad', 'location', 'price', 'budget']
    complex_kw = ['emi', 'loan', 'rera', 'stamp duty', 'registration', 'tax', 'legal',
                  'investment', 'roi', 'appreciation', 'documents', 'process', 'kaise',
                  'kyun', 'kyu', 'explain', 'compare', 'difference', 'best', 'suggest',
                  'should i', 'worth', 'calculate']
    word_count = len(text.split())
    if any(k in text for k in complex_kw):
        return 'smart'
    if word_count > 20:
        return 'smart'
    if any(k in text for k in property_kw):
        return 'fast'
    return 'fast'


def groq(messages, model=None, image_data=None, query_type=None, _retry=0):
    key = os.getenv('GROQ_API_KEY', '').strip()

    if _retry == 0:
        print(f"[GROQ DEBUG] key_present={bool(key)} key_len={len(key) if key else 0} key_prefix={key[:8] if key else 'NONE'}...")

    if not key:
        print("[GROQ ERROR] GROQ_API_KEY is missing or empty")
        return None

    if model:
        chosen = model
    elif query_type == 'vision' or image_data:
        chosen = os.getenv('GROQ_VISION_MODEL', 'meta-llama/llama-4-scout-17b-16e-instruct')
    elif query_type == 'smart':
        chosen = os.getenv('GROQ_TEXT_MODEL', 'openai/gpt-oss-120b')
    else:
        chosen = os.getenv('GROQ_FAST_MODEL', 'llama-3.1-8b-instant')

    if _retry == 0:
        print(f"[GROQ DEBUG] query_type={query_type} chosen_model={chosen} image={'yes' if image_data else 'no'}")

    if image_data:
        messages = [dict(m) for m in messages]
        last = messages[-1]
        content = [
            {'type': 'text', 'text': last.get('content', '')},
            {'type': 'image_url', 'image_url': {'url': image_data}},
        ]
        messages[-1] = {**last, 'content': content}

    try:
        r = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json={
                'model': chosen,
                'messages': messages,
                'temperature': 0.3,
                'max_completion_tokens': 500,
            },
            timeout=35,
        )
        if r.ok:
            data = r.json()
            answer = data['choices'][0]['message']['content']
            print(f"[GROQ SUCCESS] model={chosen} answer_len={len(answer)}")
            return answer
        else:
            print(f"[GROQ ERROR] model={chosen} status={r.status_code} body={r.text[:500]}")
            fast_model = os.getenv('GROQ_FAST_MODEL', 'llama-3.1-8b-instant')
            if chosen != fast_model and _retry < 2:
                print(f"[GROQ FALLBACK] retrying with {fast_model}")
                return groq(messages, model=fast_model, image_data=image_data, _retry=_retry + 1)
    except requests.exceptions.Timeout:
        print(f"[GROQ EXCEPTION] Timeout after 35s model={chosen}")
    except requests.exceptions.ConnectionError as e:
        print(f"[GROQ EXCEPTION] ConnectionError: {str(e)[:300]}")
    except Exception as e:
        print(f"[GROQ EXCEPTION] {type(e).__name__}: {str(e)[:500]}")

    return None

# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI(title='EstateAI - Premium AI Real Estate Sales Platform', version='5.0.0')

ALLOWED_ORIGINS = [o.strip() for o in os.getenv('ALLOWED_ORIGINS', '').split(',') if o.strip()]
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=['*'],
        allow_headers=['*'],
        allow_credentials=True,
                )# ============================================================
# PYDANTIC MODELS
# ============================================================
class Login(BaseModel):
    username: str
    password: str
    remember: bool = False


class CustomerSignup(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    phone: str = Field(min_length=5, max_length=30)
    password: str = Field(min_length=8, max_length=128)

    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        v = v.strip().lower()
        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', v):
            raise ValueError('Invalid email address')
        return v


class CustomerLogin(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)


class Chat(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = 'public'
    property_id: str = ''
    image_data: str = ''


class Lead(BaseModel):
    name: str = Field(min_length=2)
    phone: str = Field(min_length=5)
    email: str = ''
    budget: str = ''
    location: str = ''
    bhk: str = ''
    property_id: str = ''
    source: str = 'Website'
    consent: bool = True
    customer_id: str = ''


class Visit(BaseModel):
    name: str = Field(min_length=2)
    phone: str = Field(min_length=5)
    email: str = ''
    date: str
    time: str
    property_id: str = ''
    notes: str = ''
    consent: bool = True
    customer_id: str = ''

    @field_validator('date')
    @classmethod
    def validate_date(cls, v):
        try:
            d = datetime.strptime(v, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError('Date must be YYYY-MM-DD')
        if d < datetime.now(timezone.utc).date():
            raise ValueError('Date cannot be in the past')
        return v

    @field_validator('time')
    @classmethod
    def validate_time(cls, v):
        try:
            datetime.strptime(v, '%H:%M')
        except ValueError:
            raise ValueError('Time must be HH:MM')
        return v


class Settings(BaseModel):
    brand: str = 'EstateAI'
    tagline: str = ''
    owner_name: str = ''
    owner_email: str = ''
    phone: str = ''
    whatsapp: str = ''
    currency: str = '₹'
    ai_greeting: str = ''
    business_hours: str = ''
    about: str = ''
    address: str = ''
    logo_url: str = ''
    hero_image: str = ''
    ai_business_context: str = ''


class StatusUpdate(BaseModel):
    status: str

    @field_validator('status')
    @classmethod
    def validate_status(cls, v):
        allowed = {'New', 'Contacted', 'Qualified', 'Won', 'Lost', 'Pending',
                   'Confirmed', 'Rescheduled', 'Completed', 'Cancelled'}
        if v not in allowed:
            raise ValueError('Invalid status')
        return v


class CustomerProfile(BaseModel):
    name: str
    phone: str = ''
    email: str = ''


# ============================================================
# ROUTES - BASIC
# ============================================================
@app.get('/')
def home():
    return FileResponse(ROOT / 'frontend' / 'index.html')


@app.get('/api/health')
def health():
    key = os.getenv('GROQ_API_KEY', '').strip()
    return {
        'ok': True,
        'version': app.version,
        'time': now(),
        'groq_key_present': bool(key),
        'groq_key_length': len(key),
        'text_model': os.getenv('GROQ_TEXT_MODEL', 'not-set'),
        'fast_model': os.getenv('GROQ_FAST_MODEL', 'not-set'),
        'vision_model': os.getenv('GROQ_VISION_MODEL', 'not-set'),
    }


@app.get('/api/test-groq')
def test_groq():
    key = os.getenv('GROQ_API_KEY', '').strip()
    if not key:
        return {'ok': False, 'error': 'GROQ_API_KEY not set', 'key_length': 0}

    model = os.getenv('GROQ_FAST_MODEL', 'llama-3.1-8b-instant')
    try:
        r = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json={
                'model': model,
                'messages': [{'role': 'user', 'content': 'Say hello in Hindi'}],
                'max_completion_tokens': 50,
            },
            timeout=20,
        )
        return {
            'ok': r.ok,
            'status': r.status_code,
            'model': model,
            'key_length': len(key),
            'key_prefix': key[:8] + '...' if len(key) > 8 else 'too-short',
            'response': r.text[:800],
        }
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {str(e)[:500]}'}


# ============================================================
# ROUTES - AUTH
# ============================================================
@app.post('/api/auth/login')
def owner_login(x: Login):
    if x.username != OWNER_USER or not hmac.compare_digest(x.password, OWNER_PASS):
        raise HTTPException(401, 'Invalid owner username or password')
    t = make_token(x.username, 'owner', SESSION_DAYS if x.remember else 1)
    r = JSONResponse({'ok': True, 'role': 'owner', 'username': x.username})
    r.set_cookie(COOKIE, t, max_age=SESSION_DAYS * 86400 if x.remember else None,
                 httponly=True, secure=SECURE_COOKIE, samesite=COOKIE_SAMESITE, path='/')
    d = load()
    audit(d, 'auth.owner_login', {'username': x.username})
    save(d)
    return r


@app.post('/api/auth/logout')
def logout():
    r = JSONResponse({'ok': True})
    r.delete_cookie(COOKIE, path='/')
    r.delete_cookie(CUSTOMER_COOKIE, path='/')
    return r


@app.get('/api/auth/me')
def me(request: Request):
    o = verify(request.cookies.get(COOKIE, ''), 'owner')
    c = verify(request.cookies.get(CUSTOMER_COOKIE, ''), 'customer')
    d = load()
    cust = next((x for x in d['customers'] if x['id'] == c.get('id')), None) if c else None
    return {
        'authenticated': bool(o or c),
        'role': 'owner' if o else ('customer' if c else None),
        'username': o.get('u') if o else None,
        'customer': cust,
    }


@app.post('/api/customer/signup')
def customer_signup(x: CustomerSignup):
    d = load()
    if any(c.get('email', '').lower() == x.email.lower() for c in d['customers']):
        raise HTTPException(409, 'An account with this email already exists')
    c = {
        'id': uid('c_'), 'name': x.name, 'email': x.email, 'phone': x.phone,
        'password_hash': hash_password(x.password), 'saved': [], 'created_at': now(),
    }
    d['customers'].insert(0, c)
    audit(d, 'customer.signup', {'id': c['id']})
    save(d)
    r = JSONResponse({'ok': True, 'customer': {k: v for k, v in c.items() if k != 'password_hash'}})
    r.set_cookie(CUSTOMER_COOKIE, make_token(c['id'], 'customer', SESSION_DAYS),
                 max_age=SESSION_DAYS * 86400, httponly=True, secure=SECURE_COOKIE,
                 samesite=COOKIE_SAMESITE, path='/')
    return r


@app.post('/api/customer/login')
def customer_login(x: CustomerLogin):
    d = load()
    c = next((z for z in d['customers'] if z.get('email', '').lower() == x.email.lower()), None)
    if not c or not verify_password(c.get('password_hash', ''), x.password):
        raise HTTPException(401, 'Invalid customer login')
    if re.fullmatch(r'[0-9a-f]{64}', c.get('password_hash', '') or ''):
        c['password_hash'] = hash_password(x.password)
        save(d)
    r = JSONResponse({'ok': True, 'customer': {k: v for k, v in c.items() if k != 'password_hash'}})
    r.set_cookie(CUSTOMER_COOKIE, make_token(c['id'], 'customer', SESSION_DAYS),
                 max_age=SESSION_DAYS * 86400, httponly=True, secure=SECURE_COOKIE,
                 samesite=COOKIE_SAMESITE, path='/')
    return r


@app.get('/api/customer/me')
def customer_me(c=Depends(customer)):
    d = load()
    x = next((z for z in d['customers'] if z['id'] == c['u']), None)
    if not x:
        raise HTTPException(404, 'Customer not found')
    return {k: v for k, v in x.items() if k != 'password_hash'}


@app.put('/api/customer/me')
def customer_update(x: CustomerProfile, c=Depends(customer)):
    d = load()
    z = next((q for q in d['customers'] if q['id'] == c['u']), None)
    if not z:
        raise HTTPException(404, 'Customer not found')
    new_email = x.email.strip().lower()
    if new_email and any(q['id'] != z['id'] and q.get('email', '').lower() == new_email for q in d['customers']):
        raise HTTPException(409, 'An account with this email already exists')
    xdata = x.model_dump()
    xdata['email'] = new_email
    z.update(xdata)
    audit(d, 'customer.profile_updated', {'id': z['id']})
    save(d)
    return {k: v for k, v in z.items() if k != 'password_hash'}


# ============================================================
# ROUTES - PUBLIC
# ============================================================
@app.get('/api/settings')
def settings():
    s = load()['settings']
    return {k: v for k, v in s.items() if k not in ['ai_business_context']}


@app.get('/api/properties')
def properties(q: str = '', location: str = '', ptype: str = '', purpose: str = '',
               bhk: str = '', min_price: float = 0, max_price: float = 0):
    d = load()
    arr = public_props(d)
    q = q.lower().strip()
    location = location.lower().strip()
    ptype = ptype.lower().strip()
    bhk = bhk.lower().strip()
    purpose = purpose.lower().strip()
    if q:
        arr = [p for p in arr if q in json.dumps(p, ensure_ascii=False).lower()]
    if location:
        arr = [p for p in arr if location in str(p.get('location', '')).lower()
               or location in str(p.get('city', '')).lower()
               or location in str(p.get('locality', '')).lower()]
    if ptype:
        arr = [p for p in arr if ptype in str(p.get('type', '')).lower()]
    if purpose:
        arr = [p for p in arr if purpose in str(p.get('purpose', '')).lower()]
    if bhk:
        arr = [p for p in arr if bhk in str(p.get('bhk', '')).lower()]
    if min_price:
        arr = [p for p in arr if float(p.get('price', 0) or 0) >= min_price]
    if max_price:
        arr = [p for p in arr if float(p.get('price', 0) or 0) <= max_price]
    return arr


@app.get('/api/properties/{pid}')
def property_detail(pid: str):
    d = load()
    p = next((x for x in public_props(d) if x['id'] == pid), None)
    if not p:
        raise HTTPException(404, 'Property not found')
    p['views'] = p.get('views', 0) + 1
    d['metrics']['property_views'] += 1
    audit(d, 'property.viewed', {'id': pid})
    save(d)
    return p


@app.post('/api/events/{kind}')
def event(kind: str):
    d = load()
    key = {'call': 'calls', 'whatsapp': 'whatsapp', 'search': 'searches'}.get(kind)
    if key:
        d['metrics'][key] = d['metrics'].get(key, 0) + 1
        audit(d, 'public.' + kind)
    save(d)
    return {'ok': True}# ============================================================
# ROUTES - SAVED / ACTIVITY
# ============================================================
@app.post('/api/customer/saved/{pid}')
def save_property(pid: str, c=Depends(customer)):
    d = load()
    p = next((x for x in public_props(d) if x['id'] == pid), None)
    if not p:
        raise HTTPException(404, 'Property not found')
    z = next(x for x in d['customers'] if x['id'] == c['u'])
    z.setdefault('saved', [])
    if pid not in z['saved']:
        z['saved'].append(pid)
    save(d)
    return z['saved']


@app.delete('/api/customer/saved/{pid}')
def unsave_property(pid: str, c=Depends(customer)):
    d = load()
    z = next(x for x in d['customers'] if x['id'] == c['u'])
    z['saved'] = [x for x in z.get('saved', []) if x != pid]
    save(d)
    return z['saved']


@app.get('/api/customer/activity')
def customer_activity(c=Depends(customer)):
    d = load()
    cid = c['u']
    return {
        'saved': next((x.get('saved', []) for x in d['customers'] if x['id'] == cid), []),
        'leads': [x for x in d['leads'] if x.get('customer_id') == cid],
        'visits': [x for x in d['visits'] if x.get('customer_id') == cid],
        'messages': [x for x in d['messages'] if x.get('customer_id') == cid],
    }


# ============================================================
# ROUTES - LEADS / VISITS
# ============================================================
@app.post('/api/leads')
def create_lead(l: Lead, request: Request):
    if not l.consent:
        raise HTTPException(400, 'Consent is required')
    d = load()
    c = optional_customer(request)
    cid = c['u'] if c else ''
    item = {'id': uid('lead_'), **l.model_dump(), 'customer_id': cid,
            'status': 'New', 'score': 'Warm', 'created_at': now()}
    d['leads'].insert(0, item)
    d['notifications'].insert(0, {
        'id': uid('n_'), 'type': 'lead', 'title': 'New customer lead',
        'text': f'{l.name} submitted an enquiry', 'at': now(), 'read': False,
    })
    d['metrics']['enquiries'] += 1
    audit(d, 'lead.created', {'lead_id': item['id'], 'property_id': l.property_id})
    save(d)
    return item


@app.post('/api/visits')
def create_visit(v: Visit, request: Request):
    if not v.consent:
        raise HTTPException(400, 'Consent is required')
    d = load()
    c = optional_customer(request)
    cid = c['u'] if c else ''
    item = {'id': uid('visit_'), **v.model_dump(), 'customer_id': cid,
            'status': 'Pending', 'created_at': now()}
    d['visits'].insert(0, item)
    d['notifications'].insert(0, {
        'id': uid('n_'), 'type': 'visit', 'title': 'New site-visit request',
        'text': f'{v.name} requested a visit', 'at': now(), 'read': False,
    })
    d['metrics']['bookings'] += 1
    audit(d, 'visit.created', {'visit_id': item['id']})
    save(d)
    return {'ok': True, 'visit': item, 'message': 'Request submitted. The property team will confirm the visit.'}


# ============================================================
# ROUTES - AI CHAT (SMART + MULTILINGUAL)
# ============================================================
def parse_price_from_text(text: str):
    t = text.lower().replace(',', '')
    m = re.search(r'(\d+\.?\d*)\s*(lakh|lac|l\b)', t)
    if m:
        return float(m.group(1)) * 100000
    m = re.search(r'(\d+\.?\d*)\s*(crore|cr\b)', t)
    if m:
        return float(m.group(1)) * 10000000
    m = re.search(r'(\d{4,})', t)
    if m:
        return float(m.group(1))
    return None


def extract_bhk(text: str):
    m = re.search(r'(\d+)\s*(bhk|bedroom)', text.lower())
    return f"{m.group(1)} BHK" if m else None


@app.post('/api/chat')
def chat(c: Chat, request: Request):
    check_rate_limit(request.client.host if request.client else 'unknown')

    d = load()
    cust = optional_customer(request)
    published = public_props(d)
    sid, created_chat_cookie = chat_session(request)
    msg = c.message.strip()
    low = msg.lower()

    print(f"[CHAT] session={sid} msg={msg[:80]} published_count={len(published)}")

    candidates = []
    for p in published:
        blob = json.dumps(p, ensure_ascii=False).lower()
        score = 0
        for term in re.findall(r'[a-z0-9]+', low):
            if len(term) > 2 and term in blob:
                score += 1
        if score:
            candidates.append((score, p))

    price = parse_price_from_text(low)
    if price:
        filtered = [p for _, p in candidates if float(p.get('price', 0) or 0) <= price * 1.1]
        if filtered:
            candidates = [(1, p) for p in filtered]

    bhk = extract_bhk(low)
    if bhk:
        filtered = [p for _, p in candidates if bhk.lower() in str(p.get('bhk', '')).lower()]
        if filtered:
            candidates = [(1, p) for p in filtered]

    candidates = [p for _, p in sorted(candidates, key=lambda x: x[0], reverse=True)[:8]]

    context = '\n'.join([
        f"ID:{p['id']} | {p.get('title')} | {p.get('purpose','')} | {p.get('type','')} | "
        f"{p.get('bhk','')} | Rs.{p.get('price','')} | {p.get('location','')} | "
        f"{p.get('area','')} | {p.get('status','')} | Amenities:{', '.join(p.get('amenities',[]))}"
        for p in (candidates or published[:12])
    ])

    s = d['settings']
    system = (s.get('ai_business_context') or DEFAULT['settings']['ai_business_context']) + f"""

BUSINESS INFO:
- Brand: {s.get('brand')}
- Owner: {s.get('owner_name')}
- Phone: {s.get('phone')}
- WhatsApp: {s.get('whatsapp')}
- Hours: {s.get('business_hours')}
- About: {s.get('about')}

PUBLISHED PROPERTY DATA:
{context}

STRICT RULES:
1. For property-specific facts (price, availability, amenities, location), use ONLY the data above.
2. For general questions (EMI, loans, RERA, legal, market, locality), answer helpfully from your knowledge.
3. Never invent property facts. Never reveal unpublished properties or owner dashboard data.
4. LANGUAGE RULE (CRITICAL): Detect the customer's language and reply in EXACTLY the same language. Support ALL languages: Hindi, Hinglish, English, Tamil, Telugu, Marathi, Bengali, Gujarati, Punjabi, Urdu, Arabic, Spanish, French, German, Chinese, Japanese, and others. Never switch to a different language unless the customer does.
5. Keep replies short (2-5 lines). Use bullet points for multiple properties.
6. Always end with a call-to-action: call, WhatsApp, or site visit.
"""

    history = get_session_history(d, sid, limit=8)
    messages = [{'role': 'system', 'content': system}]
    for h in history:
        messages += [
            {'role': 'user', 'content': h['user']},
            {'role': 'assistant', 'content': h['assistant']},
        ]
    messages.append({'role': 'user', 'content': msg})

    qtype = detect_query_type(msg, has_image=bool(c.image_data))
    answer = groq(messages, image_data=c.image_data or None, query_type=qtype)

    if not answer:
        print(f"[CHAT FALLBACK] Groq returned None, using fallback for qtype={qtype}")
        if candidates:
            answer = (f"Maine {len(candidates)} published property option(s) dhundhe jo aapki requirement se match karte hain. "
                      f"Price, location aur amenities compare karne ke liye neeche cards dekhein. "
                      f"Aur specific chahiye toh budget, location aur BHK bata dein.")
        elif any(x in low for x in ['number', 'phone', 'contact', 'call', 'whatsapp']):
            answer = (f"Aap {s.get('owner_name') or s.get('brand')} se "
                      f"{s.get('phone') or 'website pe diye number'} pe contact kar sakte hain, "
                      f"ya WhatsApp button use kar sakte hain.")
        else:
            answer = s.get('ai_greeting') or 'Apni preferred location, budget, BHK ya property type bataiye, main matching properties dhundhne mein help karunga.'

    item = {
        'id': uid('msg_'), 'session_id': sid, 'customer_id': cust['u'] if cust else '',
        'user': msg, 'assistant': answer, 'property_id': c.property_id,
        'query_type': qtype, 'at': now(),
    }
    d['messages'].append(item)
    append_session_message(sid, item)
    d['metrics']['chat_sessions'] = len(set(x.get('session_id') for x in d['messages']))
    audit(d, 'ai.chat', {'message_id': item['id'], 'customer_id': item['customer_id'], 'qtype': qtype})
    save(d)

    resp = JSONResponse({
        'answer': answer,
        'matches': candidates[:6],
        'session_id': sid,
        'contact': {'phone': s.get('phone', ''), 'whatsapp': whatsapp_url(s.get('whatsapp', ''))},
    })
    if created_chat_cookie:
        resp.set_cookie(CHAT_COOKIE, sid, max_age=SESSION_DAYS * 86400,
                        httponly=True, secure=SECURE_COOKIE,
                        samesite=COOKIE_SAMESITE, path='/')
    return resp


# ============================================================
# ROUTES - OWNER
# ============================================================
@app.get('/api/owner/data')
def owner_data(_: dict = Depends(owner)):
    d = load()
    return {k: d[k] for k in ['settings', 'properties', 'customers', 'leads',
                              'visits', 'messages', 'notifications', 'audit', 'metrics']}


@app.post('/api/settings')
def update_settings(s: Settings, _: dict = Depends(owner)):
    d = load()
    d['settings'] = s.model_dump()
    audit(d, 'settings.updated')
    save(d)
    return d['settings']


@app.post('/api/properties')
def add_property(p: dict, _: dict = Depends(owner)):
    d = load()
    p = dict(p)
    p['id'] = uid('p_')
    p.setdefault('published', False)
    p.setdefault('views', 0)
    p.setdefault('images', [])
    p.setdefault('created_at', now())
    p.setdefault('updated_at', now())
    d['properties'].insert(0, p)
    audit(d, 'property.created', {'id': p['id']})
    save(d)
    return p


@app.put('/api/properties/{pid}')
def edit_property(pid: str, p: dict, _: dict = Depends(owner)):
    d = load()
    old = next((x for x in d['properties'] if x['id'] == pid), None)
    if not old:
        raise HTTPException(404, 'Property not found')
    p = dict(p)
    p['id'] = pid
    p['images'] = p.get('images', old.get('images', []))
    p['views'] = old.get('views', 0)
    p['created_at'] = old.get('created_at', now())
    p['updated_at'] = now()
    d['properties'][d['properties'].index(old)] = p
    audit(d, 'property.updated', {'id': pid})
    save(d)
    return p


@app.delete('/api/properties/{pid}')
def delete_property(pid: str, _: dict = Depends(owner)):
    d = load()
    before = len(d['properties'])
    d['properties'] = [p for p in d['properties'] if p['id'] != pid]
    if len(d['properties']) == before:
        raise HTTPException(404, 'Property not found')
    audit(d, 'property.deleted', {'id': pid})
    save(d)
    return {'ok': True}


@app.post('/api/properties/{pid}/publish')
def publish(pid: str, published: bool = True, _: dict = Depends(owner)):
    d = load()
    p = next((x for x in d['properties'] if x['id'] == pid), None)
    if not p:
        raise HTTPException(404, 'Property not found')
    p['published'] = published
    p['status'] = 'Published' if published and p.get('status') == 'Draft' else p.get('status', 'Ready to Move')
    p['updated_at'] = now()
    audit(d, 'property.published' if published else 'property.unpublished', {'id': pid})
    save(d)
    return p


@app.post('/api/properties/{pid}/images')
async def image_upload(pid: str, file: UploadFile = File(...), _: dict = Depends(owner)):
    d = load()
    p = next((x for x in d['properties'] if x['id'] == pid), None)
    if not p:
        raise HTTPException(404, 'Property not found')
    if not (file.content_type or '').startswith('image/'):
        raise HTTPException(400, 'Only image files are allowed')
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f'Max image size is {MAX_UPLOAD_MB} MB')
    image = {'id': uid('img_'), 'name': file.filename, 'url': '', 'data': ''}
    if CLOUDINARY_URL:
        try:
            import cloudinary, cloudinary.uploader
            cloudinary.config(cloudinary_url=CLOUDINARY_URL)
            result = cloudinary.uploader.upload(
                raw, folder='estateai/properties', resource_type='image',
                public_id=f"{Path(file.filename or 'image').stem}_{uuid.uuid4().hex[:8]}"
            )
            image['url'] = result.get('secure_url', '')
        except ImportError:
            image['data'] = f"data:{file.content_type};base64,{base64.b64encode(raw).decode()}"
        except Exception as e:
            raise HTTPException(502, f'Image storage upload failed: {e}')
    else:
        image['data'] = f"data:{file.content_type};base64,{base64.b64encode(raw).decode()}"
    p.setdefault('images', []).append(image)
    audit(d, 'property.image_uploaded', {'property_id': pid, 'name': file.filename})
    save(d)
    return p


@app.delete('/api/properties/{pid}/images/{image_id}')
def image_delete(pid: str, image_id: str, _: dict = Depends(owner)):
    d = load()
    p = next((x for x in d['properties'] if x['id'] == pid), None)
    if not p:
        raise HTTPException(404, 'Property not found')
    p['images'] = [x for x in p.get('images', []) if x.get('id') != image_id]
    save(d)
    return p


@app.post('/api/owner/demo-data/remove')
def remove_demo_data(_: dict = Depends(owner)):
    d = load()
    before = len(d['properties'])
    d['properties'] = [p for p in d['properties'] if not p.get('is_demo')]
    removed = before - len(d['properties'])
    audit(d, 'demo_data.removed', {'count': removed})
    save(d)
    return {'ok': True, 'removed': removed}


@app.post('/api/owner/leads/{lid}/status')
def lead_status(lid: str, x: StatusUpdate, _: dict = Depends(owner)):
    d = load()
    z = next((q for q in d['leads'] if q['id'] == lid), None)
    if not z:
        raise HTTPException(404, 'Lead not found')
    z['status'] = x.status
    audit(d, 'lead.status_updated', {'id': lid, 'status': x.status})
    save(d)
    return z


@app.post('/api/owner/visits/{vid}/status')
def visit_status(vid: str, x: StatusUpdate, _: dict = Depends(owner)):
    d = load()
    z = next((q for q in d['visits'] if q['id'] == vid), None)
    if not z:
        raise HTTPException(404, 'Visit not found')
    z['status'] = x.status
    audit(d, 'visit.status_updated', {'id': vid, 'status': x.status})
    save(d)
    return z


@app.post('/api/owner/notifications/read')
def notifications_read(_: dict = Depends(owner)):
    d = load()
    for n in d['notifications']:
        n['read'] = True
    save(d)
    return {'ok': True}


@app.get('/api/owner/export')
def owner_export(_: dict = Depends(owner)):
    d = load()
    safe = json.loads(json.dumps(d))
    for c in safe['customers']:
        c.pop('password_hash', None)
    return safe


@app.get('/api/owner')
def owner_page(_: dict = Depends(owner)):
    return {'ok': True}
