"""
HCCC Library Local Server
=========================
Runs on http://localhost:5000  (or PORT env var for cloud hosting)
Handles registration checks and book issuance from the HTML form.

Run: python hccc_server.py
"""

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from datetime import datetime, timedelta

# ─── CONFIG ───────────────────────────────────────────────────────────────────
SHEET_ID            = "1Tk8k8W4zTch4JL60cdplrAPOGN3GwDzKMttf1tV4QxU"
BOOKS_ISSUED_TAB    = "Books Issued"
CREDENTIALS_FILE    = "client_secret.json"       # OAuth (local use)
SERVICE_ACCOUNT_FILE = "service_account.json"    # Service account (cloud/Render)
TOKEN_FILE          = "token.json"
PORT                = int(os.environ.get("PORT", 5000))
LOAN_DAYS           = 15

# Temple Devotee API
TEMPLE_API_URL  = "https://livermoretemple.org:9003/devotee-management/devotees/existence/public"
TEMPLE_API_KEY  = "UyN9Dema5gR5DQ5fY2hc4bC5Zg8we6cN"
# ──────────────────────────────────────────────────────────────────────────────

os.chdir(os.path.dirname(os.path.abspath(__file__)))

def check_devotee_registered(firstname, lastname, email="", phone=""):
    """Call the HCCC temple API to check if devotee is registered."""
    import requests
    params = {}
    if firstname: params["firstname"]   = firstname
    if lastname:  params["lastName"]    = lastname
    if email:     params["email"]       = email
    if phone:     params["phoneNumber"] = phone

    try:
        resp = requests.get(
            TEMPLE_API_URL,
            params=params,
            headers={"X-API-KEY": TEMPLE_API_KEY, "accept": "*/*"},
            verify=False,
            timeout=10
        )
        raw = resp.text.strip()
        print(f"  Temple API response: {raw}")
        raw_lower = raw.lower()
        if "multiple accounts" in raw_lower:
            return True
        return raw_lower == "true"
    except Exception as e:
        print(f"  Temple API error: {e}")
        return False

def get_sheets_service():
    """Authenticate with Google Sheets.
    Uses service_account.json if present (cloud/Render),
    otherwise falls back to OAuth token.json (local)."""
    from googleapiclient.discovery import build
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    # ── Service Account from environment variable (Railway/cloud) ──
    sa_json = os.environ.get("SERVICE_ACCOUNT_JSON")
    if sa_json:
        import json as _json
        from google.oauth2.service_account import Credentials
        sa_info = _json.loads(sa_json)
        creds = Credentials.from_service_account_info(sa_info, scopes=SCOPES)
        return build("sheets", "v4", credentials=creds)

    # ── Service Account from file ──
    if os.path.exists(SERVICE_ACCOUNT_FILE):
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        return build("sheets", "v4", credentials=creds)

    # ── OAuth (local laptop) ──
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)

def write_to_books_issued(service, row_data):
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"'{BOOKS_ISSUED_TAB}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row_data]}
    ).execute()

def read_books_issued(service):
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{BOOKS_ISSUED_TAB}'!A:J"
    ).execute()
    return result.get("values", [])

# ─── Request Handler ──────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}")

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_json({})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json({"status": "running"})

        elif parsed.path == "/report":
            self.handle_report()

        elif parsed.path in ("/", "/form"):
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hccc_library_form.html")
            try:
                with open(html_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", len(content))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_json({"error": "Form file not found"}, 404)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        parsed = urlparse(self.path)

        if parsed.path == "/check":
            self.handle_check(body)
        elif parsed.path == "/issue":
            self.handle_issue(body)
        else:
            self.send_json({"error": "Not found"}, 404)

    # GET /report — returns all rows from Books Issued sheet
    def handle_report(self):
        try:
            service = get_sheets_service()
            rows = read_books_issued(service)
            self.send_json({"success": True, "rows": rows})
        except Exception as e:
            print(f"  Error reading sheet: {e}")
            self.send_json({"success": False, "error": str(e)}, 500)

    # POST /check — { firstName, lastName, phone, email }
    def handle_check(self, body):
        firstname = body.get("firstName", "").strip()
        lastname  = body.get("lastName", "").strip()
        phone     = body.get("phone", "").strip()
        email     = body.get("email", "").strip()

        if not firstname and not lastname:
            self.send_json({"registered": 0, "message": "First name and last name are required"})
            return
        try:
            is_registered = check_devotee_registered(firstname, lastname, email, phone)
            print(f"  Check: '{firstname} {lastname}' → {'REGISTERED ✅' if is_registered else 'NOT REGISTERED ❌'}")
            self.send_json({
                "registered": 1 if is_registered else 0,
                "message": "Member found in temple database" if is_registered else "Member not found in temple database"
            })
        except Exception as e:
            print(f"  Error: {e}")
            self.send_json({"registered": 0, "message": f"Server error: {str(e)}"})

    # POST /issue — { member: {...}, book: {...} }
    def handle_issue(self, body):
        member = body.get("member", {})
        book   = body.get("book", {})

        today      = datetime.now()
        return_by  = today + timedelta(days=LOAN_DAYS)
        date_str   = today.strftime("%Y-%m-%d")
        return_str = return_by.strftime("%Y-%m-%d")

        row = [
            date_str,
            member.get("fullName", ""),
            member.get("email", ""),
            member.get("phone", ""),
            book.get("title", ""),
            book.get("author", ""),
            "Registered",
            return_str,
        ]

        try:
            service = get_sheets_service()
            write_to_books_issued(service, row)
            print(f"  Issued: '{book.get('title')}' to {member.get('fullName')} (return by {return_str})")
            self.send_json({"success": True, "returnDate": return_str})
        except Exception as e:
            print(f"  Error writing to sheet: {e}")
            self.send_json({"success": False, "error": str(e)}, 500)

# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import socket
    local_ip = socket.gethostbyname(socket.gethostname())
    print("=" * 50)
    print("HCCC Library Server — Starting")
    print(f"On THIS computer: http://localhost:{PORT}")
    print(f"On OTHER computers (same WiFi): http://{local_ip}:{PORT}")
    print("=" * 50)
    try:
        server = HTTPServer(("0.0.0.0", PORT), Handler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
