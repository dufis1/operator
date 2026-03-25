"""
Test script — verifies Google Calendar API credentials and lists upcoming events
from the Operator Google account.
"""

import datetime
import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "..", "credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "token.json")


def get_calendar_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def main():
    print("Connecting to Google Calendar...")
    service = get_calendar_service()

    now = datetime.datetime.utcnow().isoformat() + "Z"
    print("Fetching upcoming events...")
    events_result = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=10,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = events_result.get("items", [])
    if not events:
        print("No upcoming events found.")
    else:
        print(f"Found {len(events)} upcoming event(s):")
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            summary = event.get("summary", "(no title)")
            meet_link = event.get("hangoutLink", "")
            meet_str = f"  Meet: {meet_link}" if meet_link else ""
            print(f"  {start} — {summary}{meet_str}")


if __name__ == "__main__":
    main()
