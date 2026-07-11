# AI-Powered Email Analyser and Automatic Response System

This is a 2-week internship project web app built with FastAPI, Jinja2, SQLite, Gmail OAuth, and optional AI providers. It demonstrates the full classify -> draft -> review -> approve -> send pipeline with a human in the loop.

## Features

- Gmail OAuth 2.0 connection using the Gmail API
- Inbox sync into a local SQLite database
- Email classification: urgent, spam, general enquiry, follow-up
- Contextual draft reply generation using OpenAI, Gemini, or demo heuristics
- Jinja2 web UI for reviewing and editing drafts before sending
- Dashboard metrics, search, status filters, and category filters
- Bulk classification for pending emails
- Thread history passed to the AI prompt when messages share a Gmail thread
- Reply log page for audit/demo review
- REST endpoints for listing, retrieving, and classifying emails
- Reply logs stored locally

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Open http://localhost:8000.

## Gmail OAuth

1. Create a Google Cloud project.
2. Enable the Gmail API.
3. Create OAuth 2.0 credentials.
4. Add `http://localhost:8000/auth/callback` as the redirect URI.
5. Download the client JSON and save it as `client_secret.json` in this project root.
6. Set `GOOGLE_CLIENT_SECRETS_FILE=client_secret.json` in `.env`.

If `client_secret.json` is missing, the app runs in demo mode and loads sample emails.

## AI Providers

The default `AI_PROVIDER=demo` works without any external API key.

For OpenAI:

```text
AI_PROVIDER=openai
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4o-mini
```

For Gemini:

```text
AI_PROVIDER=gemini
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-1.5-flash
```

## Main Routes

- `GET /` - inbox dashboard
- `POST /emails/sync` - sync Gmail inbox or demo emails
- `POST /emails/classify-pending` - classify all pending emails
- `GET /emails/{id}` - review one email
- `POST /emails/{id}/classify` - classify and generate a draft
- `POST /emails/{id}/send` - approve and send/log a reply
- `GET /logs` - view approved reply logs
- `GET /api/emails` - JSON list of emails
- `GET /api/emails/{id}` - JSON email details
- `POST /api/emails/{id}/classify` - JSON classification result
- `POST /api/emails/classify-pending` - JSON bulk classification result
- `GET /api/stats` - JSON dashboard counts
- `GET /api/logs` - JSON reply logs
