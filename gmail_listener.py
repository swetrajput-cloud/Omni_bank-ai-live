"""
OmniBank AI — Gmail Listener
Polls Gmail inbox every 30 seconds for new emails and forwards them
to the live backend /webhook/email endpoint.

Setup:
1. Enable Gmail API at https://console.cloud.google.com/
2. Create OAuth2 credentials → download as credentials.json
3. Run once manually to authenticate (opens browser):
      python gmail_listener.py --auth
4. Then run normally (runs forever):
      python gmail_listener.py

Requires: credentials.json in same directory.
On first run it creates token.json for future logins.
"""

import os, json, base64, time, argparse, requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

SCOPES            = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE  = Path(__file__).parent / "credentials.json"
TOKEN_FILE        = Path(__file__).parent / "token.json"
BACKEND_URL       = os.getenv("BACKEND_URL", "http://localhost:8000")
POLL_INTERVAL     = int(os.getenv("GMAIL_POLL_INTERVAL", "30"))  # seconds
SEEN_IDS_FILE     = Path(__file__).parent / "seen_email_ids.json"

def load_seen_ids():
    if SEEN_IDS_FILE.exists():
        return set(json.loads(SEEN_IDS_FILE.read_text()))
    return set()

def save_seen_ids(ids: set):
    SEEN_IDS_FILE.write_text(json.dumps(list(ids)))

def get_gmail_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    "credentials.json not found.\n"
                    "Download OAuth2 credentials from Google Cloud Console.\n"
                    "See: https://developers.google.com/gmail/api/quickstart/python"
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)

def decode_body(payload):
    """Extract plain text body from Gmail message payload."""
    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain":
                data = part["body"].get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        # Recurse into nested parts
        for part in payload["parts"]:
            result = decode_body(part)
            if result:
                return result
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return ""

def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""

def process_new_emails(service, seen_ids: set) -> set:
    """Fetch unread emails, forward new ones to backend, return updated seen_ids."""
    try:
        result = service.users().messages().list(
            userId="me",
            labelIds=["INBOX", "UNREAD"],
            maxResults=20
        ).execute()
    except Exception as e:
        print(f"[Gmail] Error listing messages: {e}")
        return seen_ids

    messages = result.get("messages", [])
    new_count = 0

    for msg_ref in messages:
        msg_id = msg_ref["id"]
        if msg_id in seen_ids:
            continue

        try:
            msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()
        except Exception as e:
            print(f"[Gmail] Error fetching message {msg_id}: {e}")
            continue

        seen_ids.add(msg_id)
        headers = msg.get("payload", {}).get("headers", [])
        from_header    = get_header(headers, "From")
        subject_header = get_header(headers, "Subject") or "(No Subject)"
        date_header    = get_header(headers, "Date")
        body           = decode_body(msg.get("payload", {}))

        # Extract just the email address from "Name <email@domain.com>"
        from_email = from_header
        if "<" in from_header and ">" in from_header:
            from_email = from_header.split("<")[1].split(">")[0].strip()

        # Skip if from ourselves or automated
        if any(skip in from_email.lower() for skip in ["noreply", "no-reply", "mailer-daemon"]):
            continue

        payload = {
            "from_email": from_email,
            "subject": subject_header,
            "body": body[:5000],  # cap at 5KB
            "message_id": msg_id,
            "received_at": date_header or datetime.now().isoformat()
        }

        try:
            resp = requests.post(
                f"{BACKEND_URL}/webhook/email",
                json=payload,
                timeout=10
            )
            if resp.status_code == 200:
                print(f"[Gmail] ✅ Processed: from={from_email} subject='{subject_header[:50]}'")
                new_count += 1
            else:
                print(f"[Gmail] ⚠️  Backend returned {resp.status_code}: {resp.text[:100]}")
        except requests.exceptions.RequestException as e:
            print(f"[Gmail] ❌ Failed to send to backend: {e}")

    if new_count:
        print(f"[Gmail] Processed {new_count} new email(s) at {datetime.now().strftime('%H:%M:%S')}")
    
    save_seen_ids(seen_ids)
    return seen_ids


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", action="store_true", help="Authenticate with Google and exit")
    args = parser.parse_args()

    print("🔑 Authenticating with Gmail...")
    service = get_gmail_service()
    print("✅ Gmail authenticated!")

    if args.auth:
        print("Authentication complete. token.json saved. Run without --auth to start polling.")
        return

    seen_ids = load_seen_ids()
    print(f"📬 Gmail Listener started — polling every {POLL_INTERVAL}s")
    print(f"   Backend: {BACKEND_URL}/webhook/email")
    print(f"   Already seen: {len(seen_ids)} emails")
    print("   Press Ctrl+C to stop\n")

    while True:
        try:
            seen_ids = process_new_emails(service, seen_ids)
        except Exception as e:
            print(f"[Gmail] Unexpected error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
