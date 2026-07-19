"""
HCCC Library Book Registration Sync
====================================
Monitors Google Form responses, checks member registration via API,
and updates the tracking Google Sheet with barcode + member details.

SETUP INSTRUCTIONS:
1. pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
2. Fill in the CONFIG section below
3. python hccc_library_sync.py
"""

import time
import json
import sys
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIGURATION — EDIT THESE BEFORE RUNNING
# ─────────────────────────────────────────────
CONFIG = {
    # Path to your Google service account credentials JSON file
    # Get it from: Google Cloud Console → IAM → Service Accounts → Keys
    "credentials_file": "client_secret.json",

    # Google Sheet ID where Form responses land
    # Open the response sheet → copy the ID from the URL:
    # https://docs.google.com/spreadsheets/d/  <<<THIS PART>>>  /edit
    "response_sheet_id": "1Tk8k8W4zTch4JL60cdplrAPOGN3GwDzKMttf1tV4QxU",

    # Name of the tab/sheet that holds form responses (usually "Form Responses 1")
    "response_tab": "Form Responses 1",

    # Google Sheet ID for your TRACKING sheet (can be same sheet, different tab)
    # This is where barcode + member details get written
    "tracking_sheet_id": "1Tk8k8W4zTch4JL60cdplrAPOGN3GwDzKMttf1tV4QxU",
    "tracking_tab": "Books Issued",

    # Column index (0-based) in the response sheet that holds the Member ID
    # Run with --diagnose first to see all columns
    "member_id_column": 4,  # Phone Number — used to check registration via API

    # Name of the tab that holds registered members (column B = names)
    "registered_members_tab": "Registered Members",

    # How often to poll for new responses (seconds)
    "poll_interval_seconds": 30,
}
# ─────────────────────────────────────────────


def get_sheets_service(creds_file):
    """Authenticate using OAuth token.json (same as your existing scripts)."""
    import os
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = None

    # Load existing token if available
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # Refresh or re-authenticate if needed
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())

    return build("sheets", "v4", credentials=creds)


def read_sheet(service, sheet_id, tab, range_="A:Z"):
    """Read all data from a sheet tab."""
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{tab}'!{range_}")
        .execute()
    )
    return result.get("values", [])


def append_row(service, sheet_id, tab, row_data):
    """Append a row to a sheet tab."""
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row_data]},
    ).execute()


def check_devotee_registered(firstname, lastname, email="", phone=""):
    """Call the HCCC temple API to check if devotee is registered. Returns True/False."""
    import urllib.request
    import urllib.parse
    import ssl

    TEMPLE_API_URL = "https://livermoretemple.org:9003/devotee-management/devotees/existence-details/public"
    TEMPLE_API_KEY = "UyN9Dema5gR5DQ5fY2hc4bC5Zg8we6cN"

    params = {}
    if firstname: params["firstname"]    = firstname
    if lastname:  params["lastName"]     = lastname
    if email:     params["email"]        = email
    if phone:     params["phoneNumber"]  = phone

    url = TEMPLE_API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-API-KEY": TEMPLE_API_KEY, "accept": "*/*"})

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            raw = resp.read().decode().strip().lower()
            return raw == "true"
    except Exception as e:
        print(f"  ⚠ Temple API error: {e} — marking as Not Registered")
        return False


def diagnose(service):
    """Print sheet structure to help identify column names and data."""
    print("\n" + "="*60)
    print("🔍 DIAGNOSIS MODE")
    print("="*60)

    print(f"\nReading: '{CONFIG['response_tab']}' tab...")
    rows = read_sheet(service, CONFIG["response_sheet_id"], CONFIG["response_tab"])

    if not rows:
        print("❌ NO DATA FOUND. Possible causes:")
        print("   1. Wrong response_sheet_id in CONFIG")
        print("   2. Wrong response_tab name (check exact spelling)")
        print("   3. Google Form not linked to this sheet")
        print("   4. Service account not shared on the sheet")
        print("\nTo fix #4: Open the Google Sheet → Share → add your")
        print("service account email (found inside credentials.json)")
        return

    print(f"\n✅ Found {len(rows)} rows\n")
    print("COLUMNS:")
    if rows:
        for i, col in enumerate(rows[0]):
            print(f"  [{i}] {col}")

    print("\nLAST 3 ROWS OF DATA:")
    for row in rows[-3:]:
        print(f"  {row}")

    print("\n" + "="*60)
    print("Update CONFIG['member_id_column'] to the correct index above.")
    print("="*60 + "\n")


def ensure_tracking_header(service):
    """Make sure the tracking sheet has a header row."""
    rows = read_sheet(service, CONFIG["tracking_sheet_id"], CONFIG["tracking_tab"])
    if not rows:
        append_row(service, CONFIG["tracking_sheet_id"], CONFIG["tracking_tab"], [
            "Date", "Member Name", "Email",
            "Phone Number", "Book Title", "Registration Status", "Message"
        ])
        print("✅ Created tracking sheet header")


def get_processed_row_count(service):
    """Return how many form responses have already been written to Books Issued."""
    rows = read_sheet(service, CONFIG["tracking_sheet_id"], CONFIG["tracking_tab"])
    return max(0, len(rows) - 1)  # subtract header row


def process_new_responses(service, last_processed_count):
    """Check form responses, verify registration, update tracking sheet."""
    rows = read_sheet(service, CONFIG["response_sheet_id"], CONFIG["response_tab"])

    if not rows or len(rows) <= 1:
        return last_processed_count  # No data or only header

    data_rows = rows[1:]
    total = len(data_rows)

    if total <= last_processed_count:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] No new submissions.")
        return last_processed_count

    # Only process rows we haven't handled yet
    new_rows = data_rows[last_processed_count:]
    print(f"\n{'─'*50}")
    print(f"📋 {len(new_rows)} new submission(s) found!")

    new_count = 0

    for row in new_rows:
        if not row or len(row) < 3:
            last_processed_count += 1
            continue

        form_timestamp = row[0].strip() if len(row) > 0 else ""

        # Extract date and format as YYYY-MM-DD so it sorts correctly
        try:
            date_part = form_timestamp.split(" ")[0]
            date_only = datetime.strptime(date_part, "%m/%d/%Y").strftime("%Y-%m-%d")
        except Exception:
            date_only = form_timestamp.split(" ")[0]

        # [0] Timestamp, [1] First Name, [2] Last Name, [3] Email, [4] Phone, [5] Terms, [6] Book Title
        first_name  = row[1].strip() if len(row) > 1 else ""
        last_name   = row[2].strip() if len(row) > 2 else ""
        full_name   = f"{first_name} {last_name}".strip()
        email       = row[3].strip() if len(row) > 3 else ""
        phone       = row[4].strip() if len(row) > 4 else ""
        book_title  = row[6].strip() if len(row) > 6 else ""

        # Check registration via temple API
        is_registered = check_devotee_registered(first_name, last_name, email, phone)
        print(f"   {full_name} | {'✅ Registered' if is_registered else '❌ Not Registered'}")

        status_text = "Registered" if is_registered else "Not Registered"
        message = "Can issue book" if is_registered else "Must register first"

        append_row(service, CONFIG["tracking_sheet_id"], CONFIG["tracking_tab"], [
            date_only, full_name, email, phone, book_title, status_text, message,
        ])

        last_processed_count += 1
        new_count += 1

    if new_count == 0:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] No new submissions.")
    else:
        print(f"\n✅ Processed {new_count} new submission(s)")

    return last_processed_count


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("HCCC Library Sync — Starting up")
    print("="*60)

    # Validate config
    if "YOUR_" in CONFIG["response_sheet_id"]:
        print("❌ ERROR: Set response_sheet_id in CONFIG first!")
        sys.exit(1)

    # Connect to Google Sheets
    try:
        service = get_sheets_service(CONFIG["credentials_file"])
        print("✅ Authenticated with Google Sheets API")
    except FileNotFoundError:
        print(f"❌ ERROR: credentials file not found at '{CONFIG['credentials_file']}'")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Auth error: {e}")
        sys.exit(1)

    # Run diagnosis if requested
    if "--diagnose" in sys.argv:
        diagnose(service)
        sys.exit(0)

    # Normal monitoring mode
    ensure_tracking_header(service)
    processed_count = get_processed_row_count(service)
    print(f"✅ {processed_count} rows already in Books Issued — watching for new ones")
    print(f"📡 Monitoring every {CONFIG['poll_interval_seconds']}s — Ctrl+C to stop\n")

    try:
        while True:
            processed_count = process_new_responses(service, processed_count)
            time.sleep(CONFIG["poll_interval_seconds"])
    except KeyboardInterrupt:
        print("\n\nStopped. Bye!")
