"""
OmniBank AI — AI Engine Pipeline
Handles: Sentiment Analysis, Intent Detection, Summarization,
Response Generation, Churn Risk, Channel Orchestration, Compliance

All modules are keyword/rule-based for reliability and explainability —
fully auditable, no black-box decisions. Architecture supports swapping
any module for an LLM (Claude, GPT-4, Gemini) at the API layer.

RBI FREE-AI aligned: Explainability and Governance pillars.
"""

import random
from datetime import datetime
from typing import List, Dict, Any, Optional

# ══════════════════════════════════════════════════════════
# 1. SENTIMENT ANALYSIS
# ══════════════════════════════════════════════════════════

ANGRY_KEYWORDS = [
    "frustrated", "angry", "furious", "unacceptable", "terrible", "horrible",
    "worst", "disgusted", "outrage", "ridiculous", "pathetic", "useless",
    "unauthorized", "fraud", "scam", "cheated", "stolen", "blocked", "locked",
    "no accountability", "demand", "escalate", "very upset", "extremely",
    "not working", "still not", "no resolution", "absolutely", "disgusting",
    "harassment", "incompetent", "negligence", "disaster"
]

POSITIVE_KEYWORDS = [
    "thank you", "thanks", "great", "excellent", "satisfied", "happy",
    "resolved", "perfect", "wonderful", "appreciate", "helpful", "good service",
    "fast", "quickly", "superb", "amazing", "outstanding", "pleased"
]

def analyze_sentiment(text: str) -> Dict[str, Any]:
    t = text.lower()
    angry_signals   = [kw for kw in ANGRY_KEYWORDS   if kw in t]
    positive_signals = [kw for kw in POSITIVE_KEYWORDS if kw in t]
    a, p = len(angry_signals), len(positive_signals)

    if a >= 2:
        return {"sentiment": "Angry",     "emoji": "🔴", "color": "#ff3b5c",
                "confidence": round(min(0.97, 0.60 + a * 0.10), 2),
                "angry_signals": angry_signals, "positive_signals": positive_signals}
    if a == 1:
        return {"sentiment": "Concerned", "emoji": "🟠", "color": "#ff6b2b",
                "confidence": 0.58,
                "angry_signals": angry_signals, "positive_signals": positive_signals}
    if p >= 1:
        return {"sentiment": "Satisfied", "emoji": "🟢", "color": "#00e676",
                "confidence": round(min(0.95, 0.70 + p * 0.10), 2),
                "angry_signals": [], "positive_signals": positive_signals}
    return {"sentiment": "Neutral", "emoji": "🟡", "color": "#f5c842",
            "confidence": 0.50, "angry_signals": [], "positive_signals": []}


def analyze_sentiment_timeline(interactions: List[Dict]) -> List[Dict]:
    return [
        {"interaction_id": i["interaction_id"], "channel": i["channel"],
         "timestamp": i["timestamp"], **analyze_sentiment(i.get("message", ""))}
        for i in interactions if i.get("direction") == "inbound"
    ]


# ══════════════════════════════════════════════════════════
# 2. INTENT DETECTION
# ══════════════════════════════════════════════════════════

INTENT_PATTERNS = {
    "Complaint": {
        "keywords": ["complaint","issue","problem","error","wrong","unauthorized",
                     "dispute","fraud","not working","failed","blocked","locked",
                     "charged","deducted","missing","not received","lost"],
        "weight": 1.0, "icon": "⚠️"
    },
    "Escalation": {
        "keywords": ["escalate","supervisor","manager","legal action","court",
                     "consumer forum","media","very angry","no resolution",
                     "unacceptable","rbi","ombudsman","social media"],
        "weight": 1.5, "icon": "🚨"
    },
    "Query": {
        "keywords": ["what is","how to","want to know","inform me","what are",
                     "please explain","clarify","details","information",
                     "interest rate","eligibility","rates","tenure"],
        "weight": 1.0, "icon": "❓"
    },
    "Request": {
        "keywords": ["apply","request","open","close","update","change","reset",
                     "block","unblock","generate","transfer","activate","loan","card"],
        "weight": 1.0, "icon": "📋"
    },
    "Follow-up": {
        "keywords": ["follow up","followup","status","update on","any news",
                     "resolved yet","waiting","still pending","reference",
                     "case number","ticket","earlier"],
        "weight": 1.0, "icon": "🔄"
    }
}

def detect_intent(text: str) -> Dict[str, Any]:
    t = text.lower()
    scores, matched = {}, {}
    for intent, cfg in INTENT_PATTERNS.items():
        hits = [kw for kw in cfg["keywords"] if kw in t]
        scores[intent]  = len(hits) * cfg["weight"]
        matched[intent] = hits

    if not any(v > 0 for v in scores.values()):
        return {"primary_intent": "General Inquiry", "icon": "💬",
                "confidence": 0.50, "urgency": "Normal",
                "secondary_intents": [], "matched_keywords": []}

    primary   = max(scores, key=scores.get)
    total     = sum(scores.values())
    conf      = round(min(0.97, scores[primary] / total), 2) if total else 0.50
    secondary = [k for k, v in sorted(scores.items(), key=lambda x: -x[1])
                 if v > 0 and k != primary][:2]
    urgency   = "High" if primary in ["Complaint", "Escalation"] else "Normal"

    return {"primary_intent": primary, "icon": INTENT_PATTERNS[primary]["icon"],
            "confidence": conf, "urgency": urgency,
            "secondary_intents": secondary, "matched_keywords": matched[primary]}


# ══════════════════════════════════════════════════════════
# 3. CONVERSATION SUMMARIZATION
# ══════════════════════════════════════════════════════════

def generate_summary(interactions: List[Dict], customer_name: str) -> Dict[str, Any]:
    if not interactions:
        return {"summary": "No interactions found.", "key_points": [], "open_issues": []}

    inbound    = [i for i in interactions if i.get("direction") == "inbound"]
    channels   = list(set(i["channel"] for i in interactions))
    all_tags   = [t for i in interactions for t in (i.get("tags") or [])]
    open_issues = [i for i in interactions if i.get("status") in ["pending","in-progress","escalated","open"]]
    resolved   = [i for i in interactions if i.get("status") == "resolved"]
    timestamps = [i["timestamp"] for i in interactions]
    first, last = timestamps[0][:10], timestamps[-1][:10]

    key_points = []
    if "unauthorized" in all_tags or "dispute" in all_tags:
        key_points.append("Customer raised an unauthorized transaction dispute")
    if "escalation" in all_tags or any(i.get("status") == "escalated" for i in interactions):
        key_points.append("Interaction escalated — high dissatisfaction recorded")
    if "card-blocked" in all_tags or "card-issue" in all_tags:
        key_points.append("Card issue reported across multiple channels")
    if "loan-inquiry" in all_tags or "loan-application" in all_tags:
        key_points.append("Customer exploring personal loan options")
    if "netbanking" in all_tags:
        key_points.append("Net banking / account access issue reported")
    if not key_points:
        key_points.append("General service interactions across multiple channels")

    summary = (
        f"{customer_name} has {len(interactions)} interaction(s) across "
        f"{', '.join(channels)} between {first} and {last}. "
        f"{len(inbound)} inbound and {len(interactions)-len(inbound)} outbound. "
    )
    if open_issues: summary += f"{len(open_issues)} issue(s) remain open. "
    if resolved:    summary += f"{len(resolved)} resolved. "
    if any(i.get("status") == "escalated" for i in interactions):
        summary += "⚠️ Customer escalated at least once — handle with priority."

    return {
        "summary": summary, "key_points": key_points,
        "open_issues": [
            {"interaction_id": i["interaction_id"], "channel": i["channel"],
             "status": i["status"], "preview": i["message"][:80] + "..."}
            for i in open_issues
        ],
        "resolved_count": len(resolved),
        "channels_used": channels,
        "date_range": {"from": first, "to": last}
    }


# ══════════════════════════════════════════════════════════
# 4. RESPONSE GENERATION
# ══════════════════════════════════════════════════════════

RESPONSE_TEMPLATES = {
    "Complaint": {
        "Angry": (
            "Dear {name}, I sincerely apologize for the inconvenience. I understand your frustration "
            "and want to assure you this is our absolute top priority. Your case has been escalated "
            "to our specialized resolution team (Reference: {ref}). A senior manager will call you "
            "within 2 hours. Thank you for your patience."
        ),
        "Concerned": (
            "Dear {name}, Thank you for bringing this to our attention. We have registered your "
            "complaint (Reference: {ref}) and our team is actively investigating. We will keep "
            "you updated and resolve this within 48 hours."
        ),
        "default": (
            "Dear {name}, Thank you for contacting us. We have noted your concern (Reference: {ref}) "
            "and our team will resolve it within 2–3 working days."
        )
    },
    "Escalation": {
        "default": (
            "Dear {name}, I completely understand your frustration and sincerely apologize. Your case "
            "(Reference: {ref}) has been escalated to senior management. A dedicated relationship "
            "manager will contact you within 2 hours."
        )
    },
    "Query": {
        "default": (
            "Dear {name}, Thank you for your inquiry. I would be happy to provide the information "
            "you need. [Agent: fill in specific product/rate details here.] Please feel free to "
            "reach out if you have additional questions."
        )
    },
    "Request": {
        "default": (
            "Dear {name}, Thank you for your request. We have initiated the process (Reference: {ref}). "
            "Please allow 1–3 working days for completion."
        )
    },
    "Follow-up": {
        "default": (
            "Dear {name}, Thank you for following up. I can see your previous interaction on file. "
            "[Agent: check system and update customer with current status.] Thank you for your patience."
        )
    },
    "General Inquiry": {
        "default": (
            "Dear {name}, Thank you for reaching out to OmniBank. We are here to help. Could you "
            "please provide more details? Alternatively, call our 24x7 helpline: 1800-XXX-XXXX."
        )
    }
}

HINDI_TEMPLATES = {
    "Complaint":  "प्रिय {name} जी, आपकी शिकायत के लिए खेद है। आपकी समस्या (संदर्भ: {ref}) को हमारी विशेष टीम को सौंपा गया है। हम 48 घंटों में समाधान प्रदान करेंगे।",
    "Escalation": "प्रिय {name} जी, हम आपकी परेशानी समझते हैं। आपका केस (संदर्भ: {ref}) वरिष्ठ प्रबंधन को भेजा गया है। 2 घंटे में संपर्क होगा।",
    "default":    "प्रिय {name} जी, OmniBank में संपर्क के लिए धन्यवाद। हम आपकी सहायता के लिए यहाँ हैं। कृपया अपनी समस्या विस्तार से बताएं।"
}

def generate_ref() -> str:
    d = datetime.now()
    return f"REF-{d.year}{d.month:02d}{d.day:02d}-{random.randint(1000,9999)}"

def generate_response(customer_name: str, intent: Dict, sentiment: Dict,
                      last_channel: str = "Email", language: str = "en") -> Dict[str, Any]:
    ref   = generate_ref()
    first = customer_name.split()[0]

    if language == "hi":
        tmpl = HINDI_TEMPLATES.get(intent.get("primary_intent",""), HINDI_TEMPLATES["default"])
    else:
        templates = RESPONSE_TEMPLATES.get(intent.get("primary_intent",""), RESPONSE_TEMPLATES["General Inquiry"])
        tmpl      = templates.get(sentiment.get("sentiment",""), templates.get("default",""))

    text = tmpl.format(name=first, ref=ref)
    if last_channel == "SMS":
        text = text[:160]

    actions = []
    intent_label    = intent.get("primary_intent","")
    sentiment_label = sentiment.get("sentiment","")
    if intent_label == "Complaint":   actions += ["Log complaint in CRM", "Check transaction history"]
    if intent_label == "Escalation" or sentiment_label == "Angry":
        actions += ["Escalate to senior agent", "Flag for priority handling"]
    if intent_label == "Follow-up":  actions += ["Pull previous case", "Check resolution status"]
    if intent_label == "Request":    actions.append("Verify KYC before processing")
    if intent_label == "Query":      actions.append("Share product brochure / rate card")
    if not actions:                   actions.append("Document interaction in CRM")

    return {
        "draft_response": text,
        "reference_number": ref,
        "tone": "Empathetic" if sentiment_label in ["Angry","Concerned"] else "Professional",
        "suggested_actions": actions,
        "requires_review": intent_label in ["Escalation","Complaint"],
        "channel": last_channel
    }


# ══════════════════════════════════════════════════════════
# 5. CHURN RISK ENGINE
# ══════════════════════════════════════════════════════════

def calculate_churn_risk(customer: Dict, interactions: List[Dict]) -> Dict[str, Any]:
    score    = 0
    inbound  = [i for i in interactions if i["direction"] == "inbound"]
    all_tags = [t for i in interactions for t in (i.get("tags") or [])]

    score += len([i for i in interactions if i.get("status") == "escalated"]) * 22
    score += len([i for i in inbound if analyze_sentiment(i["message"])["sentiment"] == "Angry"])    * 18
    score += len([i for i in inbound if analyze_sentiment(i["message"])["sentiment"] == "Concerned"]) * 8
    score += len([i for i in interactions if i.get("status") in ["pending","in-progress","open"]]) * 12
    if "unauthorized" in all_tags or "fraud" in all_tags: score += 15
    if "card-blocked" in all_tags:    score += 10
    if not customer.get("consent_given"): score += 8
    if customer.get("dnd_registered"):    score += 5
    score = min(99, score)

    if   score >= 70: level, color = "Critical", "#ff3b5c"
    elif score >= 45: level, color = "High",     "#ff6b2b"
    elif score >= 25: level, color = "Medium",   "#f5c842"
    else:             level, color = "Low",      "#00e676"

    advice = {
        "Critical": "Assign dedicated relationship manager immediately",
        "High":     "Schedule proactive callback within 24 hours",
        "Medium":   "Send satisfaction survey, monitor closely",
        "Low":      "Customer relationship appears stable"
    }[level]

    return {
        "score": score, "level": level, "color": color, "advice": advice,
        "signals": {
            "escalations":    len([i for i in interactions if i.get("status") == "escalated"]),
            "angry_messages": len([i for i in inbound if analyze_sentiment(i["message"])["sentiment"] == "Angry"]),
            "open_issues":    len([i for i in interactions if i.get("status") in ["pending","in-progress","open"]])
        }
    }


# ══════════════════════════════════════════════════════════
# 6. CHANNEL ORCHESTRATION
# ══════════════════════════════════════════════════════════

def recommend_channel(customer: Dict, intent: Dict, sentiment: Dict) -> Dict[str, Any]:
    preferred   = customer.get("preferred_channel", "Email")
    urgency     = intent.get("urgency", "Normal")
    sent_label  = sentiment.get("sentiment", "Neutral")

    if urgency == "High" or sent_label in ["Angry","Concerned"]:
        recommended = "Call" if preferred != "Call" else preferred
        reason      = "High urgency — direct voice contact recommended"
    else:
        recommended = preferred
        reason      = f"Customer's preferred channel: {preferred}"

    if customer.get("dnd_registered") and recommended == "SMS":
        recommended = "Email"
        reason      = "SMS blocked (DND registered) — switched to Email"

    return {"recommended_channel": recommended, "reason": reason,
            "fallback": "Email" if recommended != "Email" else "SMS",
            "customer_preferred": preferred}


# ══════════════════════════════════════════════════════════
# 7. COMPLIANCE ENGINE
# ══════════════════════════════════════════════════════════

def check_compliance(customer: Dict, channel: str, message_type: str,
                     rules: List[Dict] = None) -> Dict[str, Any]:
    if rules is None:
        rules = [
            {"rule_name": "DND Check",            "applies_to": ["SMS","WhatsApp"], "message_types": ["marketing","promotional"], "action": "block"},
            {"rule_name": "Consent Validation",   "applies_to": ["SMS","Email","WhatsApp"], "message_types": ["marketing"], "action": "block"},
            {"rule_name": "KYC Required",         "applies_to": ["SMS","Email","Call"], "message_types": ["financial_offer"], "action": "block"},
            {"rule_name": "Transactional Allowed","applies_to": ["SMS","Email","Call","WhatsApp","Chat"], "message_types": ["transactional"], "action": "allow"},
        ]

    violations, passed = [], []

    for rule in rules:
        applies_to    = rule.get("applies_to", [])
        message_types = rule.get("message_types", [])
        if channel not in applies_to:      continue
        if message_type not in message_types: continue

        name   = rule["rule_name"]
        action = rule["action"]

        if name == "DND Check":
            if customer.get("dnd_registered") and action == "block":
                violations.append(f"{name} — Customer is DND registered")
            else: passed.append(name)
        elif name == "Consent Validation":
            if not customer.get("consent_given") and action == "block":
                violations.append(f"{name} — No consent on record")
            else: passed.append(name)
        elif name == "KYC Required":
            if customer.get("kyc_status") != "Verified" and action == "block":
                violations.append(f"{name} — KYC not verified")
            else: passed.append(name)
        else:
            passed.append(f"{name} (always allowed)")

    compliant = len(violations) == 0
    return {
        "is_compliant":  compliant,
        "status":        "✅ Approved" if compliant else "❌ Blocked",
        "violations":    violations,
        "passed_rules":  passed,
        "recommendation": "Message can be sent." if compliant else "Do not send. Resolve violations first."
    }


# ══════════════════════════════════════════════════════════
# 8. FULL AI PIPELINE (single entry point)
# ══════════════════════════════════════════════════════════

def run_ai_pipeline(customer: Dict, interactions: List[Dict], latest_message: str) -> Dict[str, Any]:
    sentiment   = analyze_sentiment(latest_message)
    intent      = detect_intent(latest_message)
    summary     = generate_summary(interactions, customer.get("name","Customer"))
    churn       = calculate_churn_risk(customer, interactions)
    channel_rec = recommend_channel(customer, intent, sentiment)
    last_ch     = (next((i for i in reversed(interactions) if i["direction"]=="inbound"), None) or {}).get("channel","Email")
    response    = generate_response(customer.get("name","Customer"), intent, sentiment, last_ch)
    timeline    = analyze_sentiment_timeline(interactions)

    return {
        "sentiment":              sentiment,
        "intent":                 intent,
        "summary":                summary,
        "churn_risk":             churn,
        "channel_recommendation": channel_rec,
        "response":               response,
        "sentiment_timeline":     timeline,
        "processed_at":           datetime.now().isoformat()
    }
