from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from oauthlib.oauth2 import OAuth2Error
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import ai, database, gmail_service
from .config import get_settings


app = FastAPI(title="AI Email Analyser and Auto Response System")
app.add_middleware(SessionMiddleware, secret_key=get_settings().app_secret_key)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


class ReplyRequest(BaseModel):
    reply_body: str


@app.on_event("startup")
def startup() -> None:
    database.init_db()


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    category: str | None = Query(default=None),
    mail_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    classified: int | None = Query(default=None),
    error: str | None = Query(default=None),
):
    credentials = gmail_service.credentials_from_session(request.session)
    emails = database.list_emails(
        category=category or None,
        mail_type=mail_type or None,
        status=status or None,
        search=q or None,
    )
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "emails": emails,
            "stats": database.dashboard_stats(),
            "filters": {"category": category or "", "mail_type": mail_type or "", "status": status or "", "q": q or ""},
            "classified": classified,
            "error": error,
            "connected": bool(credentials),
            "connected_email": request.session.get("google_email"),
            "oauth_available": gmail_service.oauth_available(),
            "ai_provider": get_settings().ai_provider,
        },
    )


@app.get("/auth/login")
def login(request: Request):
    if not gmail_service.oauth_available():
        raise HTTPException(status_code=400, detail="Google OAuth client secrets file was not found.")
    gmail_service.clear_google_session(request.session)
    flow = gmail_service.build_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account consent",
    )
    response = RedirectResponse(authorization_url)
    response.set_cookie("oauth_state", state, httponly=True, samesite="lax")
    return response


@app.get("/auth/logout")
def logout(request: Request):
    gmail_service.clear_google_session(request.session)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("oauth_state")
    return response


@app.get("/auth/callback")
def auth_callback(request: Request):
    state = request.cookies.get("oauth_state")
    flow = gmail_service.build_flow(state=state)
    try:
        flow.fetch_token(authorization_response=str(request.url))
    except OAuth2Error:
        gmail_service.clear_google_session(request.session)
        response = RedirectResponse(
            "/?error=Google%20rejected%20that%20account.%20Use%20an%20approved%20test%20user%20or%20remove%20the%20extra%20email%20from%20the%20OAuth%20app.",
            status_code=303,
        )
        response.delete_cookie("oauth_state")
        return response
    request.session["google_credentials"] = gmail_service.credentials_to_session(flow.credentials)
    try:
        request.session["google_email"] = gmail_service.profile_email(flow.credentials)
    except (TimeoutError, HttpError, GoogleAuthError):
        request.session["google_email"] = ""
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("oauth_state")
    return response


@app.post("/emails/sync")
def sync_emails(request: Request):
    credentials = gmail_service.credentials_from_session(request.session)
    try:
        messages = gmail_service.list_recent_messages(credentials) if credentials else gmail_service.demo_messages()
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Timed out while connecting to Gmail. Check your internet connection, VPN/proxy/firewall, then try Sync Inbox again.",
        ) from exc
    except (HttpError, GoogleAuthError) as exc:
        gmail_service.clear_google_session(request.session)
        raise HTTPException(
            status_code=401,
            detail="Gmail authorization failed. Click Connect Gmail and sign in with an approved account.",
        ) from exc
    if credentials:
        database.remove_demo_emails()
        database.prune_unsent_inbox([message["gmail_id"] for message in messages])
    for message in messages:
        database.upsert_email(message)
    return RedirectResponse("/", status_code=303)


@app.get("/emails/{email_id}", response_class=HTMLResponse)
def email_detail(request: Request, email_id: int):
    email = database.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    thread_history = database.get_thread_history(email["thread_id"], exclude_email_id=email_id)
    return templates.TemplateResponse(
        "detail.html",
        {"request": request, "email": email, "thread_history": thread_history},
    )


@app.post("/emails/{email_id}/classify")
def classify_email(email_id: int):
    email = database.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    result = ai.classify_email(dict(email))
    database.update_classification(email_id, result["category"], result["confidence"], result["mail_type"])
    return RedirectResponse(f"/emails/{email_id}", status_code=303)


@app.post("/emails/{email_id}/draft")
def generate_draft(email_id: int):
    email = database.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    if not email["category"] or not email["mail_type"]:
        result = ai.classify_email(dict(email))
        print("result", result)
        database.update_classification(email_id, result["category"], result["confidence"], result["mail_type"])
        email = database.get_email(email_id)
    thread_history = [dict(row) for row in database.get_thread_history(email["thread_id"], exclude_email_id=email_id)]
    try:
        draft_reply = ai.generate_draft(dict(email), thread_history)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AI draft generation failed: {type(exc).__name__}: {str(exc)[:400]}",
        ) from exc
    database.update_draft(email_id, draft_reply)
    return RedirectResponse(f"/emails/{email_id}", status_code=303)


@app.post("/emails/classify-pending")
def classify_pending():
    count = 0
    for email in database.get_pending_emails():
        result = ai.classify_email(dict(email))
        database.update_classification(email["id"], result["category"], result["confidence"], result["mail_type"])
        count += 1
    return RedirectResponse(f"/?classified={count}", status_code=303)


@app.post("/emails/{email_id}/send")
def approve_and_send(request: Request, email_id: int, reply_body: str = Form(...)):
    email = database.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    credentials = gmail_service.credentials_from_session(request.session)
    gmail_message_id = None
    if credentials:
        try:
            gmail_message_id = gmail_service.send_reply(
                credentials,
                to=email["sender"],
                subject=email["subject"],
                body=reply_body,
                thread_id=email["thread_id"],
            )
        except (HttpError, GoogleAuthError) as exc:
            gmail_service.clear_google_session(request.session)
            raise HTTPException(
                status_code=401,
                detail="Gmail authorization failed. Reconnect Gmail with an approved account before sending.",
            ) from exc
    database.mark_sent(email_id, reply_body, gmail_message_id or "demo-not-sent")
    return RedirectResponse(f"/emails/{email_id}", status_code=303)


@app.get("/api/emails")
def api_list_emails():
    return [dict(row) for row in database.list_emails()]


@app.get("/api/emails/{email_id}")
def api_get_email(email_id: int):
    email = database.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return dict(email)


@app.post("/api/emails/{email_id}/classify")
def api_classify_email(email_id: int):
    email = database.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    result = ai.classify_email(dict(email))
    database.update_classification(email_id, result["category"], result["confidence"], result["mail_type"])
    return result


@app.post("/api/emails/{email_id}/draft")
def api_generate_draft(email_id: int):
    email = database.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    if not email["category"] or not email["mail_type"]:
        result = ai.classify_email(dict(email))
        database.update_classification(email_id, result["category"], result["confidence"], result["mail_type"])
        email = database.get_email(email_id)
    thread_history = [dict(row) for row in database.get_thread_history(email["thread_id"], exclude_email_id=email_id)]
    try:
        draft_reply = ai.generate_draft(dict(email), thread_history)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AI draft generation failed: {type(exc).__name__}: {str(exc)[:400]}",
        ) from exc
    database.update_draft(email_id, draft_reply)
    return {"email_id": email_id, "draft_reply": draft_reply}


@app.post("/api/emails/{email_id}/send")
def api_send_reply(request: Request, email_id: int, payload: ReplyRequest):
    email = database.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    credentials = gmail_service.credentials_from_session(request.session)
    gmail_message_id = None
    if credentials:
        try:
            gmail_message_id = gmail_service.send_reply(
                credentials,
                to=email["sender"],
                subject=email["subject"],
                body=payload.reply_body,
                thread_id=email["thread_id"],
            )
        except (HttpError, GoogleAuthError) as exc:
            gmail_service.clear_google_session(request.session)
            raise HTTPException(
                status_code=401,
                detail="Gmail authorization failed. Reconnect Gmail with an approved account before sending.",
            ) from exc
    database.mark_sent(email_id, payload.reply_body, gmail_message_id or "demo-not-sent")
    return {"status": "sent", "gmail_message_id": gmail_message_id or "demo-not-sent"}


@app.get("/logs", response_class=HTMLResponse)
def reply_logs(request: Request):
    return templates.TemplateResponse(
        "logs.html",
        {"request": request, "logs": database.list_reply_logs()},
    )


@app.get("/api/logs")
def api_reply_logs():
    return [dict(row) for row in database.list_reply_logs()]


@app.get("/api/stats")
def api_dashboard_stats():
    stats = database.dashboard_stats()
    return {
        **stats,
        "categories": [dict(row) for row in stats["categories"]],
    }


@app.post("/api/emails/classify-pending")
def api_classify_pending():
    results = []
    for email in database.get_pending_emails():
        result = ai.classify_email(dict(email))
        database.update_classification(email["id"], result["category"], result["confidence"], result["mail_type"])
        results.append({"email_id": email["id"], **result})
    return {"classified": len(results), "results": results}
