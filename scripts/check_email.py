"""Check the email settings from Cloud Shell, step by step, before blaming the cloud.

    python scripts/check_email.py [send-to@example.com]      (or: make email-test TO=...)

Reads email_from, smtp_password and brevo_api_key from infra/main/terraform.tfvars
(never prints them) and tests each one that is set:
    Gmail   connect to smtp.gmail.com:587, start TLS, sign in, send a test email
    Brevo   send a test email through Brevo's API
The test email goes to email_from unless an address is given. If this works but the
Notification function's log still says it failed, the problem is on the function's
side; if this fails too, the step it stops at says what to fix.
"""

from __future__ import annotations

import json
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path

TFVARS = Path(__file__).resolve().parents[1] / "infra" / "main" / "terraform.tfvars"


def tfvar(text: str, name: str) -> str:
    m = re.search(rf'^\s*{name}\s*=\s*"([^"]*)"', text, re.M)
    return m.group(1).strip() if m else ""


def check_gmail(sender: str, password: str, to: str) -> bool:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"Unbeatable Bank <{sender}>", to, "Unbeatable Bank: email test (Gmail)"
    msg.set_content("If you can read this, the bank's Gmail settings work.")
    step = "Gmail: connecting to smtp.gmail.com:587"
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
            print("ok    ", step)
            step = "Gmail: starting TLS"
            smtp.starttls(context=ssl.create_default_context())
            print("ok    ", step)
            step = f"Gmail: signing in as {sender}"
            smtp.login(sender, password.replace(" ", ""))
            print("ok    ", step)
            step = f"Gmail: sending a test email to {to}"
            smtp.send_message(msg)
            print("ok    ", step)
            return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"FAILED {step}: Gmail refused the app password ({e.smtp_code}).")
        print("       Check: 2-Step Verification is on, the app password was created for THIS account,")
        print("       and it is copied exactly (16 letters). Create a new one if unsure.")
    except Exception as e:  # noqa: BLE001 - report whatever it is
        print(f"FAILED {step}: {type(e).__name__}: {e}")
    return False


def check_brevo(sender: str, key: str, to: str) -> bool:
    step = f"Brevo: sending a test email to {to}"
    request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps({"sender": {"email": sender, "name": "Unbeatable Bank"}, "to": [{"email": to}],
                         "subject": "Unbeatable Bank: email test (Brevo)",
                         "textContent": "If you can read this, the bank's Brevo settings work."}).encode(),
        headers={"api-key": key, "content-type": "application/json", "accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15):
            print("ok    ", step)
            return True
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="replace")
        print(f"FAILED {step}: Brevo answered {e.code}: {detail}")
        if e.code == 401:
            print("       The key is wrong or was deleted: create a new API key under SMTP & API > API Keys.")
        elif "sender" in detail.lower():
            print(f"       {sender} isn't a verified sender in Brevo yet: Senders > Add a sender, then click the")
            print("       link Brevo emails to that address.")
    except Exception as e:  # noqa: BLE001
        print(f"FAILED {step}: {type(e).__name__}: {e}")
    return False


def main() -> int:
    if not TFVARS.exists():
        print(f"{TFVARS} not found. In Cloud Shell: gsutil cp gs://<project>-tfstate/tfvars/main.tfvars {TFVARS}")
        return 1
    text = TFVARS.read_text()
    sender, password, brevo = tfvar(text, "email_from"), tfvar(text, "smtp_password"), tfvar(text, "brevo_api_key")
    problems = []
    if not sender or "@" not in sender:
        problems.append("email_from is missing or not an email address")
    if not password and not brevo:
        problems.append("neither smtp_password (Gmail) nor brevo_api_key (Brevo) is set")
    for name, value in (("email_from", sender), ("smtp_password", password), ("brevo_api_key", brevo)):
        if value and (not value.isascii() or "..." in value):
            problems.append(f"{name} still contains a placeholder like '…' from the instructions")
    if password and len(password.replace(" ", "")) != 16:
        problems.append(f"smtp_password has {len(password.replace(' ', ''))} letters; a Gmail app password has 16")
    if brevo.startswith("xsmtpsib-"):
        problems.append("brevo_api_key is Brevo's SMTP key (xsmtpsib-...); the bank needs an API key (xkeysib-...): "
                        "SMTP & API > API Keys > Generate a new API key")
    elif brevo and not brevo.startswith("xkeysib-"):
        problems.append("brevo_api_key doesn't start with xkeysib- (Brevo API keys do; leave the line out until you have one)")
    for p in problems:
        print("PROBLEM:", p)
    if problems:
        return 1
    to = sys.argv[1] if len(sys.argv) > 1 else sender
    results = {}
    if password:
        results["Gmail"] = check_gmail(sender, password, to)
    if brevo:
        results["Brevo"] = check_brevo(sender, brevo, to)
    working = [name for name, ok in results.items() if ok]
    if working:
        print(f"Working: {', '.join(working)}. Look in the inbox (and Spam) of {to}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
