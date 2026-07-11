import json
import re

from .config import get_settings


CATEGORIES = {"urgent", "spam", "general enquiry", "follow-up"}
MAIL_TYPES = {"reply", "no-reply", "notification", "promotional", "transactional"}


def classify_email(email: dict) -> dict:
    result = _analyse_demo(email)
    return {
        "category": result["category"],
        "confidence": result["confidence"],
        "mail_type": detect_mail_type(email),
    }


def generate_draft(email: dict, thread_history: list[dict] | None = None) -> str:
    settings = get_settings()
    if settings.ai_provider == "openai" and settings.openai_api_key:
        return _analyse_with_openai(email, thread_history or [])["draft_reply"]
    if settings.ai_provider == "gemini" and settings.gemini_api_key:
        return _analyse_with_gemini(email, thread_history or [])["draft_reply"]
    category = email.get("category") or _analyse_demo(email)["category"]
    return _demo_reply(email, category)


def analyse_email(email: dict, thread_history: list[dict] | None = None) -> dict:
    result = classify_email(email)
    result["draft_reply"] = generate_draft({**email, **result}, thread_history or [])
    return result


# def _prompt(email: dict, thread_history: list[dict]) -> str:
#     history = "\n\n".join(
#         f"From: {item.get('sender', '')}\nSubject: {item.get('subject', '')}\n{item.get('body', '')}"
#         for item in thread_history[-5:]
#     )
#     return f"""
# Classify the email as one of: urgent, spam, general enquiry, follow-up.
# Then write a concise, professional, tone-aware draft reply.
# Return strict JSON with keys: category, confidence, draft_reply.

# Thread history:
# {history or 'No previous thread history available.'}

# Current email:
# From: {email.get('sender', '')}
# Subject: {email.get('subject', '')}
# Body:
# {email.get('body', '')}
# """

def _prompt(email: dict, thread_history: list[dict]) -> str:
    history = "\n\n".join(
        f"From: {item.get('sender', '')}\nSubject: {item.get('subject', '')}\n{item.get('body', '')}"
        for item in thread_history[-5:]
    )

    return f"""
You are an AI email assistant.

Tasks:
1. Classify the email into exactly one of:
   - urgent
   - spam
   - general enquiry
   - follow-up

2. Write a concise, professional, context-aware reply.

Return ONLY valid JSON.

The JSON MUST have exactly these keys:

{{
  "category": "urgent | spam | general enquiry | follow-up",
  "confidence": 0.91,
  "draft_reply": "..."
}}

Rules:
- confidence MUST be a decimal number between 0.0 and 1.0.
- Do NOT use words like "low", "medium", or "high".
- Do NOT include markdown.
- Do NOT include explanations.
- Output JSON only.

Thread history:
{history or "No previous thread history available."}

Current email:

From: {email.get("sender","")}
Subject: {email.get("subject","")}

Body:
{email.get("body","")}
"""



# def _parse_ai_json(text: str) -> dict:
#     match = re.search(r"\{.*\}", text, re.DOTALL)
#     payload = json.loads(match.group(0) if match else text)
#     category = str(payload.get("category", "general enquiry")).lower()
#     if category not in CATEGORIES:
#         category = "general enquiry"
#     return {
#         "category": category,
#         "confidence": float(payload.get("confidence", 0.7)),
#         "draft_reply": str(payload.get("draft_reply", "")).strip(),
#     }

def _parse_ai_json(text: str) -> dict:
    import json
    import re

    # Extract JSON if the AI wraps it in extra text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    payload = json.loads(match.group(0) if match else text)

    # Validate category
    category = str(payload.get("category", "general enquiry")).lower()
    if category not in CATEGORIES:
        category = "general enquiry"

    # Handle confidence robustly
    confidence = payload.get("confidence", 0.7)

    if isinstance(confidence, str):
        confidence = confidence.strip().lower()

        mapping = {
            "very low": 0.20,
            "low": 0.40,
            "medium": 0.70,
            "high": 0.90,
            "very high": 0.98,
        }

        confidence = mapping.get(confidence, confidence)

    try:
        confidence = float(confidence)
    except (ValueError, TypeError):
        confidence = 0.70

    # Keep confidence within 0-1
    confidence = max(0.0, min(confidence, 1.0))

    return {
        "category": category,
        "confidence": confidence,
        "draft_reply": str(payload.get("draft_reply", "")).strip(),
    }

def detect_mail_type(email: dict) -> str:
    sender = (email.get("sender") or "").lower()
    subject = (email.get("subject") or "").lower()
    snippet = (email.get("snippet") or "").lower()
    body = (email.get("body") or "").lower()
    combined = f"{sender} {subject} {snippet} {body}"

    if any(token in sender for token in ["no-reply", "noreply", "donotreply", "do-not-reply"]):
        return "no-reply"
    if any(token in combined for token in ["newsletter", "unsubscribe", "promotion", "offer", "sale", "discount", "deal"]):
        return "promotional"
    if any(token in sender for token in ["notification", "notifications", "alerts", "updates"]):
        return "notification"
    if any(token in combined for token in ["receipt", "invoice", "payment", "order", "booking", "transaction"]):
        return "transactional"
    return "reply"


def _analyse_with_openai(email: dict, thread_history: list[dict]) -> dict:
    from openai import OpenAI

    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "You are an email triage assistant that returns strict JSON only."},
            {"role": "user", "content": _prompt(email, thread_history)},
        ],
        temperature=0.3,
    )
    return _parse_ai_json(response.choices[0].message.content or "{}")


def _analyse_with_gemini(email: dict, thread_history: list[dict]) -> dict:
    import google.generativeai as genai

    settings = get_settings()
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)
    response = model.generate_content(_prompt(email, thread_history))
    return _parse_ai_json(response.text)


def _analyse_demo(email: dict) -> dict:
    subject = (email.get("subject") or "").lower()
    body = (email.get("body") or "").lower()
    combined = f"{subject} {body}"

    spam_score = _score(
        combined,
        {
            "winner": 3,
            "lottery": 3,
            "free money": 4,
            "click here": 3,
            "claim": 2,
            "limited offer": 3,
            "reward": 2,
            "congratulations": 2,
            "expires soon": 2,
        },
    )
    urgent_score = _score(
        combined,
        {
            "urgent": 4,
            "asap": 4,
            "immediately": 4,
            "critical": 4,
            "deadline": 3,
            "today": 2,
            "before the deadline": 4,
            "action required": 4,
            "need your response": 4,
            "respond today": 4,
            "review meeting": 2,
            "starting soon": 3,
            "blocked": 3,
            "high priority": 4,
            "time sensitive": 4,
            "final submission": 2,
            "fix it": 2,
        },
    )
    follow_up_score = _score(
        combined,
        {
            "follow up": 4,
            "following up": 4,
            "checking in": 4,
            "reminder": 4,
            "earlier message": 3,
            "previous email": 3,
            "any update": 3,
            "status": 2,
            "pending response": 3,
            "blockers": 2,
        },
    )

    def generate_draft(email: dict, thread_history: list[dict] | None = None) -> str:
        settings = get_settings()
        print("AI Provider:", settings.ai_provider)
        print("OpenAI key exists:", bool(settings.openai_api_key))
        print("Gemini key exists:", bool(settings.gemini_api_key))

    if spam_score >= 3 and spam_score >= urgent_score:
        category = "spam"
        confidence = _confidence(spam_score, base=0.78)
    elif urgent_score >= 3 and urgent_score >= follow_up_score:
        category = "urgent"
        confidence = _confidence(urgent_score, base=0.76)
    elif follow_up_score >= 3:
        category = "follow-up"
        confidence = _confidence(follow_up_score, base=0.74)
    else:
        category = "general enquiry"
        confidence = _general_confidence(combined)

    draft = _demo_reply(email, category)
    return {"category": category, "confidence": confidence, "draft_reply": draft}


def _score(text: str, weights: dict[str, int]) -> int:
    return sum(weight for phrase, weight in weights.items() if phrase in text)


def _confidence(score: int, base: float) -> float:
    return min(round(base + (score * 0.025), 2), 0.96)


def _general_confidence(text: str) -> float:
    score = 0.72
    if any(word in text for word in ["question", "can you", "could you", "please", "details"]):
        score += 0.06
    if any(word in text for word in ["documentation", "setup", "meeting", "project", "update"]):
        score += 0.04
    if len(text) > 160:
        score += 0.03
    return min(round(score, 2), 0.89)


def _demo_reply(email: dict, category: str) -> str:
    sender_name = (email.get("sender") or "there").split("<")[0].strip().strip('"') or "there"
    subject = email.get("subject") or "your email"
    if category == "spam":
        return "Thank you for your message. I will review it and respond only if further action is required."
    if category == "urgent":
        return (
            f"Hi {sender_name},\n\n"
            f"Thanks for flagging this. I understand that \"{subject}\" needs urgent attention. "
            "I am reviewing the details now and will get back to you with the next steps shortly.\n\n"
            "Best regards"
        )
    if category == "follow-up":
        return (
            f"Hi {sender_name},\n\n"
            "Thanks for following up. I appreciate the reminder and will review the previous context before responding with an update.\n\n"
            "Best regards"
        )
    return (
        f"Hi {sender_name},\n\n"
        "Thank you for reaching out. I have received your message and will review the details before getting back to you with a clear response.\n\n"
        "Best regards"
    )
