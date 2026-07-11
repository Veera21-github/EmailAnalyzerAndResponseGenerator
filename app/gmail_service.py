import base64
import email.message
import os
from datetime import datetime, timezone
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.exceptions import GoogleAuthError, RefreshError, TransportError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import google_auth_httplib2
import httplib2

from .config import get_settings


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
REQUEST_TIMEOUT_SECONDS = 20


def oauth_available() -> bool:
    return Path(get_settings().google_client_secrets_file).exists()


def build_flow(state: str | None = None) -> Flow:
    settings = get_settings()
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    return Flow.from_client_secrets_file(
        settings.google_client_secrets_file,
        scopes=SCOPES,
        redirect_uri=settings.google_redirect_uri,
        state=state,
    )


def credentials_from_session(session: dict) -> Credentials | None:
    data = session.get("google_credentials")
    if not data:
        return None
    credentials = Credentials(**data)
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(GoogleAuthRequest())
        except (RefreshError, TransportError, GoogleAuthError):
            clear_google_session(session)
            return None
        session["google_credentials"] = credentials_to_session(credentials)
    return credentials


def clear_google_session(session: dict) -> None:
    session.pop("google_credentials", None)
    session.pop("google_email", None)


def credentials_to_session(credentials: Credentials) -> dict:
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }


def profile_email(credentials: Credentials) -> str:
    service = _build_gmail_service(credentials)
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "")


def list_recent_messages(credentials: Credentials, max_results: int | None = None) -> list[dict]:
    max_results = max_results or get_settings().gmail_max_results
    service = _build_gmail_service(credentials)
    response = service.users().messages().list(userId="me", labelIds=["INBOX"], maxResults=max_results).execute()
    messages = response.get("messages", [])
    return [_message_to_email(service.users().messages().get(userId="me", id=item["id"], format="full").execute()) for item in messages]


def send_reply(credentials: Credentials, to: str, subject: str, body: str, thread_id: str | None = None) -> str:
    service = _build_gmail_service(credentials)
    message = email.message.EmailMessage()
    message["To"] = to
    message["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    payload = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    sent = service.users().messages().send(userId="me", body=payload).execute()
    return sent.get("id", "")


def _build_gmail_service(credentials: Credentials):
    http = httplib2.Http(timeout=REQUEST_TIMEOUT_SECONDS)
    authorized_http = google_auth_httplib2.AuthorizedHttp(credentials, http=http)
    return build("gmail", "v1", http=authorized_http, cache_discovery=False)


def demo_messages() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "gmail_id": "demo-urgent-1",
            "thread_id": "demo-thread-urgent",
            "sender": "Project Mentor <mentor@example.com>",
            "recipient": "me@example.com",
            "subject": "Urgent: Internship demo review today",
            "snippet": "Please share the working demo before the review meeting.",
            "body": "Hi, please share the working demo before today's review meeting. This is urgent because the panel needs to validate the workflow.",
            "received_at": now,
        },
        {
            "gmail_id": "demo-followup-1",
            "thread_id": "demo-thread-followup",
            "sender": "Client Team <client@example.com>",
            "recipient": "me@example.com",
            "subject": "Following up on API integration",
            "snippet": "Checking in on the Gmail API integration progress.",
            "body": "Hello, I am following up on the Gmail API integration. Could you update us on the current status and blockers?",
            "received_at": now,
        },
        {
            "gmail_id": "demo-followup-previous",
            "thread_id": "demo-thread-followup",
            "sender": "You <me@example.com>",
            "recipient": "Client Team <client@example.com>",
            "subject": "Re: Following up on API integration",
            "snippet": "I will complete the OAuth flow and send an update soon.",
            "body": "Hi, I will complete the OAuth flow and send an update soon. The classification and draft screens are already working in demo mode.",
            "received_at": now,
            "category": "follow-up",
            "confidence": 0.8,
            "status": "sent",
        },
        {
            "gmail_id": "demo-general-1",
            "thread_id": "demo-thread-general",
            "sender": "Student Coordinator <coordinator@example.com>",
            "recipient": "me@example.com",
            "subject": "Question about project documentation",
            "snippet": "Can you include setup steps in the README?",
            "body": "Hi, can you include setup steps, API details, and screenshots in the README for the final submission?",
            "received_at": now,
        },
        {
            "gmail_id": "demo-spam-1",
            "thread_id": "demo-thread-spam",
            "sender": "Rewards Desk <promo@example.com>",
            "recipient": "me@example.com",
            "subject": "Winner selected: click here for free money",
            "snippet": "Limited offer, claim your reward now.",
            "body": "Congratulations, you are a winner. Click here for free money and claim this limited offer immediately.",
            "received_at": now,
        },
    ]


def _message_to_email(message: dict) -> dict:
    headers = {item["name"].lower(): item["value"] for item in message.get("payload", {}).get("headers", [])}
    return {
        "gmail_id": message["id"],
        "thread_id": message.get("threadId"),
        "sender": headers.get("from", ""),
        "recipient": headers.get("to", ""),
        "subject": headers.get("subject", "(no subject)"),
        "snippet": message.get("snippet", ""),
        "body": _extract_body(message.get("payload", {})),
        "received_at": _gmail_timestamp(message.get("internalDate")),
    }


def _extract_body(payload: dict) -> str:
    if payload.get("body", {}).get("data"):
        return _decode_part(payload["body"]["data"])
    for part in payload.get("parts", []) or []:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return _decode_part(part["body"]["data"])
        nested = _extract_body(part)
        if nested:
            return nested
    return ""


def _decode_part(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode(errors="replace")


def _gmail_timestamp(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
