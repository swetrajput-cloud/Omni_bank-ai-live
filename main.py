"""
OmniBank AI — LIVE Backend
Replaces all simulate_* functions with real Twilio + Gmail integrations.

Real capabilities:
  - Incoming Twilio calls  → /twilio/voice  (TwiML webhook)
  - Incoming Twilio SMS    → /twilio/sms    (webhook)
  - Incoming Gmail email   → polled every 30s by gmail_listener.py
  - Outbound SMS/Email/Call via real APIs
  - SQLite persistence (swap to PostgreSQL in prod)
  - Server-Sent Events for live dashboard push

Run:
  uvicorn main:app --reload --port 8000

Set env vars in .env (see .env.example)
"""

import json, uuid, os, sqlite3, asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# ── Third-party (install via requirements.txt) ─────────────
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.twiml.messaging_response import MessagingResponse

load_dotenv()

# ── Config from .env ───────────────────────────────────────
TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")   # e.g. +1415XXXXXXX
BACKEND_URL         = os.getenv("BACKEND_URL", "http://localhost:8000")  # your public URL (ngrok/Railway)

twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None

DB_PATH = Path(__file__).parent / "omnibank.db"

# ── SSE broadcast queue ────────────────────────────────────
_sse_queues: List[asyncio.Queue] = []

async def broadcast(event: dict):
    """Push a live event to all connected dashboard clients."""
    for q in list(_sse_queues):
        try:
            await q.put(event)
        except Exception:
            pass

# ── Database setup ─────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            name TEXT, email TEXT, phone TEXT,
            account_type TEXT, kyc_status TEXT,
            dnd_registered INTEGER DEFAULT 0,
            consent_given INTEGER DEFAULT 1,
            preferred_channel TEXT DEFAULT 'Email',
            churn_score INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS interactions (
            interaction_id TEXT PRIMARY KEY,
            customer_id TEXT,
            channel TEXT,
            direction TEXT,
            timestamp TEXT,
            subject TEXT,
            message TEXT,
            agent_id TEXT,
            status TEXT DEFAULT 'open',
            tags TEXT DEFAULT '[]',
            sentiment TEXT,
            intent TEXT,
            ai_processed INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS complaints (
            complaint_id TEXT PRIMARY KEY,
            customer_id TEXT,
            description TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT,
            rbi_reference TEXT
        );

        CREATE TABLE IF NOT EXISTS call_sessions (
            call_sid TEXT PRIMARY KEY,
            customer_phone TEXT,
            customer_id TEXT,
            status TEXT,
            started_at TEXT,
            ended_at TEXT,
            transcript TEXT DEFAULT '',
            recording_url TEXT
        );
    """)
    db.commit()
    db.close()
    _seed_demo_customers()

def _seed_demo_customers():
    """Insert sample customers if DB is empty."""
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    if count == 0:
        demo = [
            ("CUST001","Priya Sharma","priya.sharma@email.com","+919876543210","Savings","Verified",0,1,"Call",72,datetime.now().isoformat()),
            ("CUST002","Rajesh Kumar","rajesh.kumar@gmail.com","+919812345678","Current","Verified",0,1,"Email",35,datetime.now().isoformat()),
            ("CUST003","Anjali Desai","anjali.desai@yahoo.com","+919765432109","Savings","Pending",1,0,"SMS",15,datetime.now().isoformat()),
            ("CUST004","Vikram Singh","vikram.singh@outlook.com","+919988776655","Premium","Verified",0,1,"WhatsApp",8,datetime.now().isoformat()),
        ]
        db.executemany(
            "INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?)", demo
        )
        db.commit()
    db.close()

# ── Lifespan ───────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("✅ OmniBank AI Live Backend started")
    print(f"📞 Twilio: {'Connected' if twilio_client else 'Not configured (set TWILIO_* env vars)'}")
    yield

app = FastAPI(
    title="OmniBank AI — Live Backend",
    description="Real Twilio + Gmail integration. Not a demo.",
    version="3.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth ───────────────────────────────────────────────────
VALID_TOKENS = {
    "AGENT_TOKEN_001": {"role": "agent",  "agent_id": "AGT001", "name": "Rohan Kumar"},
    "AGENT_TOKEN_002": {"role": "agent",  "agent_id": "AGT002", "name": "Meena Iyer"},
    "ADMIN_TOKEN_001": {"role": "admin",  "agent_id": "ADMIN01","name": "Admin User"},
    "SYSTEM_TOKEN":    {"role": "system", "agent_id": "SYSTEM", "name": "System"},
}
def authenticate(x_auth_token: str = Header(default="AGENT_TOKEN_001")):
    info = VALID_TOKENS.get(x_auth_token)
    if not info:
        raise HTTPException(401, "Invalid X-Auth-Token")
    return info

# ── AI Pipeline (imported from your existing file) ─────────
import sys
sys.path.insert(0, str(Path(__file__).parent))
try:
    from ai_pipeline import run_ai_pipeline, check_compliance, analyze_sentiment, detect_intent
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False
    def run_ai_pipeline(c, i, m): return {"sentiment":{"sentiment":"Neutral"},"intent":{"primary_intent":"Query"},"churn_risk":{"score":0,"level":"Low"}}
    def analyze_sentiment(t): return {"sentiment":"Neutral","emoji":"🟡"}
    def detect_intent(t): return {"primary_intent":"Query","urgency":"Normal"}

# ── Helpers ────────────────────────────────────────────────
def _get_customer(customer_id: str, db=None):
    close = db is None
    if db is None: db = get_db()
    row = db.execute("SELECT * FROM customers WHERE customer_id=?", (customer_id,)).fetchone()
    if close: db.close()
    return dict(row) if row else None

def _find_customer_by_phone(phone: str, db=None):
    close = db is None
    if db is None: db = get_db()
    # Normalize: strip spaces/dashes, try last 10 digits
    clean = phone.replace(" ","").replace("-","").replace("+","")
    row = db.execute(
        "SELECT * FROM customers WHERE replace(replace(replace(phone,' ',''),'-',''),'+','') LIKE ?",
        (f"%{clean[-10:]}",)
    ).fetchone()
    if close: db.close()
    return dict(row) if row else None

def _new_interaction_id(db):
    count = db.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
    return f"INT{count+1:04d}"

def _store_interaction(db, customer_id, channel, direction, message,
                       subject=None, agent_id="SYSTEM", status="open", tags=None,
                       sentiment=None, intent=None):
    iid = _new_interaction_id(db)
    db.execute(
        """INSERT INTO interactions
           (interaction_id,customer_id,channel,direction,timestamp,subject,
            message,agent_id,status,tags,sentiment,intent,ai_processed)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (iid, customer_id, channel, direction, datetime.now().isoformat(),
         subject, message, agent_id, status,
         json.dumps(tags or []), sentiment, intent, 1 if sentiment else 0)
    )
    db.commit()
    return iid

# ════════════════════════════════════════════════════════════
# TWILIO VOICE WEBHOOK — called when someone rings your number
# ════════════════════════════════════════════════════════════
@app.post("/twilio/voice", response_class=Response)
async def twilio_voice_incoming(request: Request):
    """
    Twilio calls this URL when a call arrives on your number.
    Configure in Twilio Console → Phone Numbers → Voice webhook → POST this URL.
    """
    form = await request.form()
    call_sid    = form.get("CallSid", "")
    from_phone  = form.get("From", "")
    call_status = form.get("CallStatus", "ringing")

    db = get_db()

    # Look up customer by caller ID
    customer = _find_customer_by_phone(from_phone, db)
    customer_id = customer["customer_id"] if customer else "UNKNOWN"
    customer_name = customer["name"] if customer else "Unknown Caller"

    # Log call session
    db.execute(
        """INSERT OR REPLACE INTO call_sessions
           (call_sid, customer_phone, customer_id, status, started_at)
           VALUES (?,?,?,?,?)""",
        (call_sid, from_phone, customer_id, call_status, datetime.now().isoformat())
    )
    db.commit()

    # Store interaction
    iid = _store_interaction(
        db, customer_id, "Call", "inbound",
        f"Incoming call from {from_phone}",
        subject="Incoming Call",
        tags=["call", "inbound-call"],
        status="in-progress"
    )
    db.close()

    # Broadcast to dashboard via SSE
    await broadcast({
        "type": "new_interaction",
        "interaction_id": iid,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "channel": "Call",
        "direction": "inbound",
        "message": f"📞 Incoming call from {from_phone}",
        "call_sid": call_sid,
        "timestamp": datetime.now().isoformat()
    })

    # TwiML response — greet caller and record speech
    vr = VoiceResponse()
    gather = Gather(
        input="speech",
        action=f"{BACKEND_URL}/twilio/voice/gather",
        method="POST",
        speech_timeout="auto",
        language="en-IN"
    )
    gather.say(
        f"Welcome to OmniBank. {'Hello ' + customer_name + '.' if customer else ''} "
        "Please describe your concern and we will connect you to the right agent.",
        voice="Polly.Aditi",
        language="en-IN"
    )
    vr.append(gather)
    vr.say("We did not receive your input. Please call back. Goodbye.")

    return Response(content=str(vr), media_type="application/xml")


@app.post("/twilio/voice/gather", response_class=Response)
async def twilio_voice_gather(request: Request):
    """
    Receives the caller's speech transcription from Twilio.
    Runs AI pipeline and updates the dashboard in real time.
    """
    form = await request.form()
    call_sid    = form.get("CallSid", "")
    speech_text = form.get("SpeechResult", "")
    from_phone  = form.get("From", "")
    confidence  = float(form.get("Confidence", 0.0))

    db = get_db()
    customer = _find_customer_by_phone(from_phone, db)
    customer_id   = customer["customer_id"] if customer else "UNKNOWN"
    customer_name = customer["name"] if customer else "Unknown"

    # Run AI on transcribed speech
    interactions = db.execute(
        "SELECT * FROM interactions WHERE customer_id=? ORDER BY timestamp",
        (customer_id,)
    ).fetchall()
    interactions_list = [dict(r) for r in interactions]

    ai_result = run_ai_pipeline(
        customer or {"name": customer_name, "customer_id": customer_id},
        interactions_list,
        speech_text
    )
    sentiment = ai_result["sentiment"]["sentiment"]
    intent    = ai_result["intent"]["primary_intent"]

    # Update interaction with transcription + AI
    iid = _store_interaction(
        db, customer_id, "Call", "inbound",
        f"[VOICE TRANSCRIPT] {speech_text}",
        subject="Call Transcript",
        tags=["call", "transcript", "ai-processed"],
        sentiment=sentiment,
        intent=intent,
        status="open"
    )

    # Update call session transcript
    db.execute(
        "UPDATE call_sessions SET status='in-progress' WHERE call_sid=?",
        (call_sid,)
    )
    db.commit()
    db.close()

    # Push full AI result to dashboard
    await broadcast({
        "type": "ai_analysis",
        "interaction_id": iid,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "channel": "Call",
        "message": speech_text,
        "transcript_confidence": confidence,
        "ai": {
            "sentiment": ai_result["sentiment"],
            "intent": ai_result["intent"],
            "churn_risk": ai_result["churn_risk"],
            "response": ai_result["response"],
        },
        "timestamp": datetime.now().isoformat()
    })

    # TwiML — acknowledge and route
    vr = VoiceResponse()
    if intent in ["Complaint", "Escalation"]:
        vr.say(
            "I understand your concern. I am connecting you to a senior agent right away. "
            "Please hold for a moment.",
            voice="Polly.Aditi", language="en-IN"
        )
    else:
        vr.say(
            "Thank you. Your request has been noted. An agent will assist you shortly.",
            voice="Polly.Aditi", language="en-IN"
        )
    vr.record(
        action=f"{BACKEND_URL}/twilio/voice/recording",
        recording_status_callback=f"{BACKEND_URL}/twilio/voice/recording-status",
        max_length=300,
        transcribe=True,
        transcribe_callback=f"{BACKEND_URL}/twilio/voice/transcription"
    )
    return Response(content=str(vr), media_type="application/xml")


@app.post("/twilio/voice/recording", response_class=Response)
async def twilio_voice_recording(request: Request):
    form = await request.form()
    call_sid      = form.get("CallSid", "")
    recording_url = form.get("RecordingUrl", "")
    db = get_db()
    db.execute(
        "UPDATE call_sessions SET recording_url=?, ended_at=?, status='completed' WHERE call_sid=?",
        (recording_url, datetime.now().isoformat(), call_sid)
    )
    db.commit()
    db.close()
    await broadcast({"type": "call_recording", "call_sid": call_sid, "recording_url": recording_url})
    vr = VoiceResponse()
    vr.say("Your call has been recorded. Thank you for contacting OmniBank. Goodbye.", voice="Polly.Aditi")
    vr.hangup()
    return Response(content=str(vr), media_type="application/xml")


@app.post("/twilio/voice/transcription")
async def twilio_voice_transcription(request: Request):
    """Receives full call transcription from Twilio after call ends."""
    form = await request.form()
    call_sid   = form.get("CallSid", "")
    transcript = form.get("TranscriptionText", "")
    db = get_db()
    db.execute(
        "UPDATE call_sessions SET transcript=? WHERE call_sid=?",
        (transcript, call_sid)
    )
    db.commit()
    db.close()
    await broadcast({"type": "call_transcription", "call_sid": call_sid, "transcript": transcript})
    return {"status": "ok"}


@app.post("/twilio/voice/status")
async def twilio_call_status(request: Request):
    """Twilio call status callback (ringing → in-progress → completed)."""
    form = await request.form()
    call_sid    = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    db = get_db()
    if call_status == "completed":
        db.execute(
            "UPDATE call_sessions SET status='completed', ended_at=? WHERE call_sid=?",
            (datetime.now().isoformat(), call_sid)
        )
        db.commit()
    db.close()
    await broadcast({"type": "call_status", "call_sid": call_sid, "status": call_status})
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════
# TWILIO SMS WEBHOOK — called when an SMS arrives
# ════════════════════════════════════════════════════════════
@app.post("/twilio/sms", response_class=Response)
async def twilio_sms_incoming(request: Request):
    """
    Configure in Twilio Console → Phone Numbers → Messaging webhook → POST this URL.
    """
    form = await request.form()
    from_phone = form.get("From", "")
    body       = form.get("Body", "").strip()
    sms_sid    = form.get("SmsSid", "")

    db = get_db()
    customer = _find_customer_by_phone(from_phone, db)
    customer_id   = customer["customer_id"] if customer else "UNKNOWN"
    customer_name = customer["name"] if customer else from_phone

    interactions_list = [dict(r) for r in db.execute(
        "SELECT * FROM interactions WHERE customer_id=? ORDER BY timestamp",
        (customer_id,)
    ).fetchall()]

    ai_result = run_ai_pipeline(
        customer or {"name": customer_name, "customer_id": customer_id},
        interactions_list, body
    )
    sentiment = ai_result["sentiment"]["sentiment"]
    intent    = ai_result["intent"]["primary_intent"]

    iid = _store_interaction(
        db, customer_id, "SMS", "inbound",
        body, subject="SMS",
        tags=["sms", "live-sms"],
        sentiment=sentiment, intent=intent
    )
    db.commit()
    db.close()

    await broadcast({
        "type": "new_interaction",
        "interaction_id": iid,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "channel": "SMS",
        "direction": "inbound",
        "message": body,
        "ai": {
            "sentiment": ai_result["sentiment"],
            "intent": ai_result["intent"],
            "churn_risk": ai_result["churn_risk"],
        },
        "timestamp": datetime.now().isoformat()
    })

    # Auto-acknowledge SMS
    mr = MessagingResponse()
    mr.message(
        f"Thank you for contacting OmniBank. Your message has been received (Ref: {iid}). "
        "An agent will respond shortly."
    )
    return Response(content=str(mr), media_type="application/xml")


# ════════════════════════════════════════════════════════════
# EMAIL WEBHOOK — called by gmail_listener.py
# ════════════════════════════════════════════════════════════
class EmailPayload(BaseModel):
    from_email: str
    subject: str
    body: str
    message_id: str
    received_at: str

@app.post("/webhook/email")
async def email_incoming(payload: EmailPayload):
    """
    Called by gmail_listener.py when a new email arrives.
    Runs AI pipeline + broadcasts to dashboard.
    """
    db = get_db()
    # Match customer by email
    row = db.execute(
        "SELECT * FROM customers WHERE lower(email)=?",
        (payload.from_email.lower().strip(),)
    ).fetchone()
    customer = dict(row) if row else None
    customer_id   = customer["customer_id"] if customer else "UNKNOWN"
    customer_name = customer["name"] if customer else payload.from_email

    interactions_list = [dict(r) for r in db.execute(
        "SELECT * FROM interactions WHERE customer_id=? ORDER BY timestamp",
        (customer_id,)
    ).fetchall()]

    ai_result = run_ai_pipeline(
        customer or {"name": customer_name, "customer_id": customer_id},
        interactions_list, payload.body
    )
    sentiment = ai_result["sentiment"]["sentiment"]
    intent    = ai_result["intent"]["primary_intent"]

    iid = _store_interaction(
        db, customer_id, "Email", "inbound",
        payload.body, subject=payload.subject,
        tags=["email", "live-email"],
        sentiment=sentiment, intent=intent
    )
    db.commit()
    db.close()

    await broadcast({
        "type": "new_interaction",
        "interaction_id": iid,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "channel": "Email",
        "direction": "inbound",
        "subject": payload.subject,
        "message": payload.body[:200],
        "ai": {
            "sentiment": ai_result["sentiment"],
            "intent": ai_result["intent"],
            "churn_risk": ai_result["churn_risk"],
            "response": ai_result["response"],
        },
        "timestamp": datetime.now().isoformat()
    })
    return {"status": "ok", "interaction_id": iid}


# ════════════════════════════════════════════════════════════
# OUTBOUND — Real SMS via Twilio
# ════════════════════════════════════════════════════════════
class SendSMSRequest(BaseModel):
    customer_id: str
    message: str

@app.post("/send/sms", tags=["Outbound"])
async def send_sms(req: SendSMSRequest, auth: dict = Depends(authenticate)):
    customer = _get_customer(req.customer_id)
    if not customer:
        raise HTTPException(404, "Customer not found")
    if customer.get("dnd_registered"):
        return {"status": "blocked", "reason": "Customer is DND registered"}
    if not twilio_client:
        raise HTTPException(503, "Twilio not configured. Set TWILIO_* env vars.")

    msg = twilio_client.messages.create(
        body=req.message,
        from_=TWILIO_PHONE_NUMBER,
        to=customer["phone"]
    )
    db = get_db()
    iid = _store_interaction(
        db, req.customer_id, "SMS", "outbound",
        req.message, agent_id=auth["agent_id"],
        tags=["sms", "outbound-sms"], status="sent"
    )
    db.close()
    await broadcast({
        "type": "outbound_sent",
        "channel": "SMS",
        "customer_id": req.customer_id,
        "message": req.message,
        "twilio_sid": msg.sid,
        "timestamp": datetime.now().isoformat()
    })
    return {"status": "sent", "twilio_sid": msg.sid, "interaction_id": iid}


# ════════════════════════════════════════════════════════════
# OUTBOUND — Make a call (Click-to-Call)
# ════════════════════════════════════════════════════════════
class CallRequest(BaseModel):
    customer_id: str
    message: str = "Hello, this is OmniBank. An agent will speak with you now."

@app.post("/call/initiate", tags=["Outbound"])
async def initiate_call(req: CallRequest, auth: dict = Depends(authenticate)):
    customer = _get_customer(req.customer_id)
    if not customer:
        raise HTTPException(404, "Customer not found")
    if not twilio_client:
        raise HTTPException(503, "Twilio not configured.")

    call = twilio_client.calls.create(
        to=customer["phone"],
        from_=TWILIO_PHONE_NUMBER,
        url=f"{BACKEND_URL}/twilio/voice/outbound-twiml?message={req.message}",
        status_callback=f"{BACKEND_URL}/twilio/voice/status",
        status_callback_method="POST"
    )
    db = get_db()
    db.execute(
        "INSERT INTO call_sessions (call_sid, customer_phone, customer_id, status, started_at) VALUES (?,?,?,?,?)",
        (call.sid, customer["phone"], req.customer_id, "initiated", datetime.now().isoformat())
    )
    _store_interaction(db, req.customer_id, "Call", "outbound",
        f"Outbound call initiated by {auth['name']}",
        agent_id=auth["agent_id"], tags=["call", "outbound-call"])
    db.close()
    return {"status": "initiated", "call_sid": call.sid, "to": customer["phone"]}


@app.get("/twilio/voice/outbound-twiml", response_class=Response)
async def outbound_twiml(message: str = "Hello from OmniBank."):
    vr = VoiceResponse()
    vr.say(message, voice="Polly.Aditi", language="en-IN")
    return Response(content=str(vr), media_type="application/xml")


# ════════════════════════════════════════════════════════════
# SERVER-SENT EVENTS — Live dashboard push
# ════════════════════════════════════════════════════════════
@app.get("/events", tags=["Live"])
async def sse_stream(request: Request):
    """
    Connect the dashboard to this endpoint for live updates.
    EventSource('http://localhost:8000/events') in the frontend.
    """
    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues.append(queue)

    async def event_generator():
        try:
            yield "data: {\"type\": \"connected\"}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_queues.remove(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ════════════════════════════════════════════════════════════
# STANDARD API ENDPOINTS (same as original, now DB-backed)
# ════════════════════════════════════════════════════════════
@app.get("/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "twilio": "connected" if twilio_client else "not_configured",
        "ai_pipeline": AI_AVAILABLE,
        "db": str(DB_PATH)
    }

@app.get("/getCustomerProfile")
def get_customer_profile(customer_id: str, auth: dict = Depends(authenticate)):
    db = get_db()
    customer = _get_customer(customer_id, db)
    if not customer:
        raise HTTPException(404, f"Customer {customer_id} not found")
    interactions = [dict(r) for r in db.execute(
        "SELECT * FROM interactions WHERE customer_id=? ORDER BY timestamp",
        (customer_id,)
    ).fetchall()]
    complaints = [dict(r) for r in db.execute(
        "SELECT * FROM complaints WHERE customer_id=?", (customer_id,)
    ).fetchall()]
    db.close()
    return {
        "status": "success", "customer": customer,
        "interaction_summary": {
            "total": len(interactions),
            "open": len([i for i in interactions if i["status"] in ["open","in-progress","escalated"]]),
            "last_contact": interactions[-1]["timestamp"] if interactions else None,
        },
        "complaints": complaints
    }

@app.get("/getCommunicationHistory")
def get_communication_history(customer_id: str, channel: Optional[str] = None,
                               limit: int = 50, auth: dict = Depends(authenticate)):
    db = get_db()
    if channel:
        rows = db.execute(
            "SELECT * FROM interactions WHERE customer_id=? AND channel=? ORDER BY timestamp LIMIT ?",
            (customer_id, channel, limit)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM interactions WHERE customer_id=? ORDER BY timestamp LIMIT ?",
            (customer_id, limit)
        ).fetchall()
    db.close()
    interactions = [dict(r) for r in rows]
    for i in interactions:
        i["tags"] = json.loads(i.get("tags") or "[]")
    return {"status": "success", "customer_id": customer_id,
            "total": len(interactions), "interactions": interactions}

@app.get("/getDashboardData")
def get_dashboard_data(customer_id: str, auth: dict = Depends(authenticate)):
    db = get_db()
    customer = _get_customer(customer_id, db)
    if not customer:
        raise HTTPException(404, "Customer not found")
    interactions = [dict(r) for r in db.execute(
        "SELECT * FROM interactions WHERE customer_id=? ORDER BY timestamp",
        (customer_id,)
    ).fetchall()]
    for i in interactions:
        i["tags"] = json.loads(i.get("tags") or "[]")
    complaints = [dict(r) for r in db.execute(
        "SELECT * FROM complaints WHERE customer_id=?", (customer_id,)
    ).fetchall()]
    latest_inbound = next((i for i in reversed(interactions) if i["direction"] == "inbound"), None)
    latest_msg = latest_inbound["message"] if latest_inbound else ""
    ai_result = run_ai_pipeline(customer, interactions, latest_msg) if latest_msg else {}
    db.close()
    return {
        "status": "success", "agent": auth, "customer": customer,
        "interactions": interactions, "complaints": complaints,
        "ai_analysis": ai_result
    }

@app.get("/searchCustomer")
def search_customer(query: str, auth: dict = Depends(authenticate)):
    db = get_db()
    q = f"%{query.lower()}%"
    rows = db.execute(
        "SELECT * FROM customers WHERE lower(name) LIKE ? OR lower(email) LIKE ? OR phone LIKE ? OR customer_id LIKE ?",
        (q, q, q, q)
    ).fetchall()
    db.close()
    return {"status": "success", "results": [dict(r) for r in rows]}

@app.get("/listCustomers")
def list_customers(auth: dict = Depends(authenticate)):
    db = get_db()
    rows = db.execute("SELECT * FROM customers").fetchall()
    db.close()
    return {"status": "success", "customers": [dict(r) for r in rows]}

@app.get("/")
def root():
    return {
        "platform": "OmniBank AI — Live Backend v3.0",
        "status": "operational",
        "endpoints": {
            "twilio_voice": f"{BACKEND_URL}/twilio/voice",
            "twilio_sms": f"{BACKEND_URL}/twilio/sms",
            "email_webhook": f"{BACKEND_URL}/webhook/email",
            "live_events": f"{BACKEND_URL}/events",
            "docs": f"{BACKEND_URL}/docs"
        }
    }
