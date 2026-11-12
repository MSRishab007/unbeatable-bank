"""Check the Gmail settings from Cloud Shell, step by step, before blaming the cloud.

    python scripts/check_email.py [send-to@example.com]      (or: make email-test)

Reads email_from and smtp_password from infra/main/terraform.tfvars (never prints the
password), connects to smtp.gmail.com:587, starts TLS, signs in, and sends one test
email (to email_from unless an address is given). If this works but the Notification
function's log still says it failed, the problem is on the function's side; if this
fails too, the step it stops at says what to fix.
"""

from __future__ import annotations

import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

TFVARS = Path(__file__).resolve().parents[1] / "infra" / "main" / "terraform.tfvars"


def tfvar(text: str, name: str) -> str:
    m = re.search(rf'^\s*{name}\s*=\s*"([^"]*)"', text, re.M)
    return m.group(1).strip() if m else ""


def main() -> int:
    if not TFVARS.exists():
        print(f"{TFVARS} not found. In Cloud Shell: gsutil cp gs://<project>-tfstate/tfvars/main.tfvars {TFVARS}")
        return 1
    text = TFVARS.read_text()
    sender, password, brevo = tfvar(text, "email_from"), tfvar(text, "smtp_password"), tfvar(text, "brevo_api_key")
    problems = []
    if not sender or "@" not in sender:
        problems.append("email_from is missing or not an email address")
    if not password:
        problems.append("smtp_password is missing")
    for name, value in (("email_from", sender), ("smtp_password", password), ("brevo_api_key", brevo)):
        if value and (not value.isascii() or "..." in value):
            problems.append(f"{name} still contains a placeholder like '…' from the instructions")
    if password and len(password.replace(" ", "")) != 16:
        problems.append(f"smtp_password has {len(password.replace(' ', ''))} letters; a Gmail app password has 16")
    if brevo and not brevo.startswith("xkeysib-"):
        problems.append("brevo_api_key doesn't start with xkeysib- (leave the line out until you have one)")
    for p in problems:
        print("PROBLEM:", p)
    if problems:
        return 1
    to = sys.argv[1] if len(sys.argv) > 1 else sender
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"Unbeatable Bank <{sender}>", to, "Unbeatable Bank: email test"
    msg.set_content("If you can read this, the bank's Gmail settings work.")
    step = "connecting to smtp.gmail.com:587"
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
            print("ok   ", step)
            step = "starting TLS"
            smtp.starttls(context=ssl.create_default_context())
            print("ok   ", step)
            step = f"signing in as {sender}"
            smtp.login(sender, password.replace(" ", ""))
            print("ok   ", step)
            step = f"sending a test email to {to}"
            smtp.send_message(msg)
            print("ok   ", step)
    except smtplib.SMTPAuthenticationError as e:
        print(f"FAILED {step}: Gmail refused the app password ({e.smtp_code}).")
        print("       Check: 2-Step Verification is on, the app password was created for THIS account,")
        print("       and it is copied exactly (16 letters). Create a new one if unsure.")
        return 1
    except Exception as e:  # noqa: BLE001 - report whatever it is
        print(f"FAILED {step}: {type(e).__name__}: {e}")
        return 1
    print("Done. Look in the inbox (and Spam) of", to)
    return 0


if __name__ == "__main__":
    sys.exit(main())
