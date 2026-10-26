"""Check delivery status of recent Twilio messages."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv(".env")
from sms_service import _get_client

c = _get_client()
messages = c.messages.list(limit=5)
for m in messages:
    print(f"SID: ...{m.sid[-10:]}")
    print(f"  To: {m.to}")
    print(f"  Status: {m.status}")
    print(f"  ErrorCode: {m.error_code}")
    print(f"  ErrorMsg: {m.error_message}")
    print(f"  Body preview: {m.body[:80]}")
    print(f"  Date: {m.date_sent}")
    print()
