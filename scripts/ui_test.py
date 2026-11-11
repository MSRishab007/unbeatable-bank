"""Browser test of the web app: drives a real Chromium through the gateway.

    pip install playwright httpx && playwright install chromium
    python scripts/ui_test.py                      # stack on http://localhost:8080
    python scripts/ui_test.py --base-url http://<ip> --shots /tmp/shots

It plays a customer (sign-up, KYC documents, account, PINs, QR payment, a new device
that needs a code, OTP challenge, deposits, nominees, payees, the lock screen, Block
everything) and bank staff (document review, cash deposit, fraud desk, DynaStore
fault injection), on a
desktop and a phone-sized screen, and fails on any JavaScript error.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect, sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base-url", default=os.getenv("BASE_URL", "http://localhost:8080"))
ap.add_argument("--shots", default="", help="folder for screenshots (optional)")
ap.add_argument("--headed", action="store_true")
args = ap.parse_args()
BASE = args.base_url.rstrip("/")
SHOTS = Path(args.shots) if args.shots else None
if SHOTS:
    SHOTS.mkdir(parents=True, exist_ok=True)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin-dev-pass")
RUN = int(time.time()) % 10_000_000
PASSWORD = "ui-test-pass-1"
PAY_PIN, APP_PIN = "640282", "3917"
passed = failed = 0
errors: list[str] = []


def step(name: str, fn) -> None:
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  ok    {name}")
    except Exception as e:  # noqa: BLE001 - report and carry on
        failed += 1
        print(f"  FAIL  {name}\n        {str(e).splitlines()[0][:300]}")
        if SHOTS and "page" in globals():                 # what the customer's screen showed
            try:
                globals()["page"].screenshot(path=str(SHOTS / f"FAILED-{failed:02d}.png"))
            except Exception:  # noqa: BLE001
                pass


def shot(page: Page, name: str) -> None:
    if SHOTS:
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def watch(page: Page, who: str) -> None:
    page.on("pageerror", lambda e: errors.append(f"{who}: {e}"))
    page.on("console", lambda m: errors.append(f"{who} console: {m.text}")
            if m.type == "error" and "fonts.g" not in m.text and "Failed to load resource" not in m.text else None)


api = httpx.Client(base_url=BASE, timeout=30)


def png_image(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A real PNG of one colour, so the browser can display what was uploaded."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


PDF_FILE = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF\n")


def make_customer_by_api(name: str, n: int) -> dict:
    """A second, ready-to-use customer (the payee), created through the API."""
    email = f"ui{RUN}p{n}@example.com"
    r = api.post("/api/auth/register", json={"full_name": name, "date_of_birth": "1988-03-09",
                                             "phone": f"+9196{RUN:07d}{n}", "email": email, "password": PASSWORD})
    r.raise_for_status()
    staff = api.post("/api/staff/login", json={"staff_id": "admin", "password": ADMIN_PASSWORD}).json()
    admin = {"Authorization": f"Bearer {staff['access_token']}"}
    api.post(f"/api/staff/kyc/{r.json()['customer_id']}/verify", headers=admin).raise_for_status()
    login = api.post("/api/auth/login", json={"identifier": email, "password": PASSWORD,
                                              "device_fingerprint": f"ui-test-payee-{RUN}"}).json()
    auth = {"Authorization": f"Bearer {login['access_token']}"}
    for kind, pin in (("payment", PAY_PIN), ("app", APP_PIN)):
        api.put(f"/api/auth/pins/{kind}", json={"pin": pin, "password": PASSWORD}, headers=auth).raise_for_status()
    account = api.post("/api/accounts", json={"product_type": "SAVINGS"}, headers=auth).json()
    return {"email": email, "auth": auth, **account}


def payee_browser(**kw):
    """A browser the payee has used before: same device id as their API sign-in, so no device code."""
    ctx = browser.new_context(**kw)
    ctx.add_init_script(f"localStorage.setItem('ub.device', 'ui-test-payee-{RUN}')")
    return ctx


def enter_pin(p: Page, pin: str, kind: str = "payment") -> None:
    dialog = p.locator("dialog").filter(has=p.get_by_role("heading", name=f"Enter your {kind} PIN"))
    expect(dialog).to_be_visible()
    dialog.locator("input.pin").fill(pin)
    dialog.get_by_role("button", name="Confirm").click()
    expect(dialog).to_have_count(0)


def inbox_code(p: Page, about: str) -> str:
    """The code in the newest inbox message containing `about` (it may take a moment to arrive)."""
    p.get_by_role("link", name="Inbox").click()
    deadline = time.time() + 10
    while True:
        found = p.locator("li[data-type=OTP]").filter(has_text=about)
        if found.count() or time.time() > deadline:
            break
        p.wait_for_timeout(500)
        p.reload()
    return re.search(r"\b(\d{6})\b", found.first.inner_text()).group(1)


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=not args.headed)
    customer_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    staff_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = customer_ctx.new_page()
    staff = staff_ctx.new_page()
    watch(page, "customer")
    watch(staff, "staff")
    page.set_default_timeout(10_000)
    staff.set_default_timeout(10_000)
    expect.set_options(timeout=10_000)

    email = f"ui{RUN}@example.com"
    phone = f"98{RUN:07d}0"
    payee = make_customer_by_api("Ravi Kumar", 1)
    landlord = make_customer_by_api("Meera Shah", 3)

    print(f"\nWeb app at {BASE}\n\n1. Sign-up and KYC")

    def sign_up():
        page.goto(BASE + "/")
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
        shot(page, "01-sign-in")
        page.get_by_role("button", name="Open an account").click()
        page.get_by_label("Full name").fill("Asha Rao")
        page.get_by_label("Date of birth").fill("1994-06-21")
        page.get_by_label("Mobile number").fill(phone)
        page.get_by_label("Email").fill(email)
        page.get_by_label("Password").fill(PASSWORD)
        page.get_by_role("button", name="Create my login").click()
        expect(page.get_by_role("heading", name="Namaste, Asha")).to_be_visible()
        expect(page.get_by_text("Your KYC is waiting for the branch")).to_be_visible()
    step("a new customer signs up and lands on their accounts", sign_up)

    def open_account():
        page.get_by_role("button", name="Open your first account").click()
        page.get_by_role("button", name="Open account", exact=True).click()
        expect(page.get_by_role("heading", name="Savings account")).to_be_visible()
        expect(page.get_by_text("Waiting for KYC")).to_be_visible()
    step("opens a savings account; it waits for KYC", open_account)

    def wrong_password():
        other = customer_ctx.new_page()
        other.goto(BASE + "/")
        other.get_by_label("Email or mobile number").fill(email)
        other.get_by_label("Password").fill("not-the-password")
        other.get_by_role("button", name="Sign in", exact=True).last.click()
        expect(other.get_by_role("alert")).not_to_be_empty()
        other.get_by_role("button", name="Forgot your password?").click()
        expect(other.locator("dialog")).to_contain_text("Reset your password")
        other.locator("dialog").get_by_role("button", name="Cancel").click()
        other.close()
    step("a wrong password shows an error and stays on the sign-in page; 'forgot' is offered", wrong_password)

    files = Path(os.getenv("TMPDIR", "/tmp")) / f"ui-test-files-{RUN}"
    files.mkdir(parents=True, exist_ok=True)
    (files / "pan.png").write_bytes(png_image(320, 200, (27, 42, 107)))
    (files / "pan-clear.png").write_bytes(png_image(320, 200, (31, 122, 77)))
    (files / "address.pdf").write_bytes(PDF_FILE)
    (files / "virus.png").write_bytes(b"MZ this is a program, not a picture" * 20)

    def upload_documents():
        page.get_by_role("link", name="Upload your documents").click()
        expect(page.get_by_role("heading", name="Identity documents")).to_be_visible()
        page.locator("[data-kyc-file=PAN]").set_input_files(str(files / "virus.png"))
        expect(page.locator(".toast.bad")).to_contain_text("JPEG or PNG")          # judged by content, not by name
        expect(page.locator("[data-kyc=PAN]")).to_contain_text("Needed")
        page.locator("[data-kyc-file=PAN]").set_input_files(str(files / "pan.png"))
        expect(page.locator("[data-kyc=PAN]")).to_contain_text("Being reviewed")
        page.locator("[data-kyc-file=ADDRESS_PROOF]").set_input_files(str(files / "address.pdf"))
        expect(page.locator("[data-kyc=ADDRESS_PROOF]")).to_contain_text("Being reviewed")
        page.locator("[data-kyc=PAN]").get_by_role("button", name="View").click()
        expect(page.locator("dialog img.file-view")).to_be_visible()
        assert page.locator("dialog img.file-view").evaluate("img => img.naturalWidth") == 320
        shot(page, "02a-kyc-upload")
        page.locator("dialog").get_by_role("button", name="Close").click()
    step("customer uploads PAN and address proof; a disguised program is refused", upload_documents)

    def staff_review():
        staff.goto(BASE + "/#/staff")
        staff.get_by_label("Staff ID").fill("kyc1")
        staff.get_by_label("Password").fill(os.getenv("KYC_PASSWORD", "kyc-dev-pass"))
        staff.get_by_role("button", name="Sign in to the staff console").click()
        expect(staff.locator("#branch-heading")).to_contain_text("to review")
        card = staff.locator("[data-customer]").filter(has_text=email)
        card.locator("[data-doc-type=PAN]").get_by_role("button", name="View").click()
        expect(staff.locator("dialog img.file-view")).to_be_visible()
        shot(staff, "10a-kyc-review")
        staff.locator("dialog").get_by_role("button", name="Close").click()
        card.locator("[data-doc-type=PAN]").get_by_role("button", name="Reject").click()
        staff.locator("dialog input[name=reason]").fill("The card number is not readable")
        staff.get_by_role("button", name="Reject and delete the file").click()
        expect(card.locator("[data-doc-type=PAN]")).to_contain_text("Rejected")
        card.locator("[data-doc-type=ADDRESS_PROOF]").get_by_role("button", name="Approve").click()
        expect(card.locator("[data-doc-type=ADDRESS_PROOF]")).to_contain_text("Approved")
        expect(card.get_by_text("Pending").first).to_be_visible()                    # one accepted document is not enough
    step("KYC officer opens the PAN, rejects it with a reason, accepts the address proof", staff_review)

    def upload_again():
        page.reload()
        expect(page.locator("[data-kyc=PAN]")).to_contain_text("Not accepted: The card number is not readable")
        expect(page.locator("[data-kyc=ADDRESS_PROOF]")).to_contain_text("Accepted")
        expect(page.locator("[data-kyc=ADDRESS_PROOF]").get_by_role("button", name="Replace")).to_have_count(0)
        page.get_by_role("link", name="Inbox").click()
        expect(page.locator("main")).to_contain_text("Please upload your PAN card again")
        page.get_by_role("link", name="Security", exact=True).click()
        page.locator("[data-kyc-file=PAN]").set_input_files(str(files / "pan-clear.png"))
        expect(page.locator("[data-kyc=PAN]")).to_contain_text("Being reviewed")
    step("customer sees the reason, gets a message, and uploads a better picture", upload_again)

    def staff_kyc():
        staff.reload()
        card = staff.locator("[data-customer]").filter(has_text=email)
        card.locator("[data-doc-type=PAN]").get_by_role("button", name="Approve").first.click()
        expect(staff.get_by_text("All documents accepted: Asha Rao is verified")).to_be_visible()
        staff.locator(".mobile-top, nav.rail").get_by_text("Sign out").first.click()
        staff.get_by_label("Staff ID").fill("admin")
        staff.get_by_label("Password").fill(ADMIN_PASSWORD)
        staff.get_by_role("button", name="Sign in to the staff console").click()
        staff.get_by_role("link", name="Branch desk").click()
        staff.get_by_label("Find a customer").fill(email)
        staff.get_by_role("button", name="Find").click()
        card = staff.locator("[data-customer]").filter(has_text=email)
        expect(card.get_by_text("Verified")).to_be_visible()
        expect(card.get_by_role("button", name="Verify in person")).to_have_count(0)
        shot(staff, "10-staff-branch")
    step("the second approval verifies the customer; staff find them by email", staff_kyc)

    def cash():
        card = staff.locator("[data-customer]").filter(has_text=email)
        card.get_by_role("button", name="Cash deposit").click()
        staff.locator("dialog input[name=amount]").fill("50000")
        staff.get_by_role("button", name="Deposit cash").click()
        expect(staff.locator("dialog")).to_have_count(0)
        card.get_by_role("button", name="Check audit trail").click()
        expect(staff.get_by_text("Audit trail intact")).to_be_visible()
    step("teller deposits Rs 50,000 in cash; audit trail verifies", cash)

    print("\n2. Accounts and passbook")

    def passbook():
        page.get_by_role("link", name="Accounts", exact=True).click()
        page.locator("a.cover").first.click()
        expect(page.locator("#available")).to_have_text("₹50,000.00")
        expect(page.locator("table.passbook tbody tr")).to_have_count(1)
        expect(page.locator("table.passbook")).to_contain_text("Cash deposit")
        shot(page, "02-account")
    step("the account is active and the passbook shows the deposit", passbook)

    def pay_id():
        page.get_by_role("button", name="Set a pay ID").click()
        page.locator("dialog input[name=alias]").fill(f"asha{RUN}")
        page.get_by_role("button", name="Save pay ID").click()
        expect(page.get_by_text(f"Your pay ID is asha{RUN}@unbeatable")).to_be_visible()
    step("sets a pay ID", pay_id)

    def home():
        page.get_by_role("link", name="Accounts", exact=True).click()
        expect(page.locator(".cover .balance").first).to_have_text("₹50,000.00")
        shot(page, "03-home")
    step("home shows the account with its balance", home)

    print("\n3. Payments")

    qr_png = Path(os.getenv("TMPDIR", "/tmp")) / f"ui-test-qr-{RUN}.png"

    def make_qr():
        # The payee's QR code, drawn by the same app in the payee's own browser session.
        ctx = payee_browser()
        p2 = ctx.new_page()
        watch(p2, "payee")
        p2.goto(BASE + "/")
        p2.get_by_label("Email or mobile number").fill(payee["email"])
        p2.get_by_label("Password").fill(PASSWORD)
        p2.get_by_role("button", name="Sign in", exact=True).last.click()
        expect(p2.get_by_role("heading", name="Namaste, Ravi")).to_be_visible()
        p2.goto(BASE + "/#/receive")
        p2.get_by_label("Amount").fill("250")
        expect(p2.locator("#pay-link")).to_have_value(re.compile(r"#/pay\?to=" + payee["account_number"] + r"&amount=250\.00"))
        p2.locator("#qr").screenshot(path=str(qr_png))
        shot(p2, "04-receive")
        ctx.close()
    step("the payee makes a QR code asking for Rs 250", make_qr)

    def scan_and_pay():
        page.get_by_role("link", name="Pay", exact=True).click()
        page.get_by_role("button", name="Scan QR").click()
        page.locator("#qr-file").set_input_files(str(qr_png))
        expect(page.locator("input[name=payee]")).to_have_value(payee["account_number"])
        expect(page.locator("#pay-form input[name=amount]")).to_have_value("250.00")
        expect(page.get_by_text("Paying Ravi K")).to_be_visible()
        page.get_by_role("button", name="Pay", exact=True).click()
        # First payment ever: the app asks Asha to set a payment PIN, then for it.
        setup = page.locator("dialog").filter(has=page.get_by_role("heading", name="Set your payment PIN"))
        expect(setup).to_be_visible()
        setup.locator("input[name=password]").fill(PASSWORD)
        setup.locator("input[name=pin]").fill("123456")
        setup.locator("input[name=again]").fill("123456")
        setup.get_by_role("button", name="Save PIN").click()
        expect(setup.get_by_role("alert")).to_contain_text("too easy")                   # refused, dialog stays
        setup.locator("input[name=pin]").fill(PAY_PIN)
        setup.locator("input[name=again]").fill(PAY_PIN)
        shot(page, "05a-set-pin")
        setup.get_by_role("button", name="Save PIN").click()
        enter_pin(page, PAY_PIN)
        expect(page.locator("#payment-result .stamp")).to_have_text("Paid")
        expect(page.locator("#payment-result")).to_contain_text("paid by QR")
        expect(page.locator("#payment-result .checks li.pass")).to_have_count(6)
        shot(page, "05-paid")
    step("scan the QR, check the name, set a payment PIN when asked, pay: stamped Paid, six checks passed", scan_and_pay)

    def wrong_pin():
        page.locator("input[name=payee]").fill(payee["account_number"])
        page.locator("input[name=payee]").dispatch_event("change")
        page.locator("#pay-form input[name=amount]").fill("5")
        page.get_by_role("button", name="Pay", exact=True).click()
        enter_pin(page, "999999")
        expect(page.locator("dialog").get_by_role("alert")).to_contain_text("2 tries left")    # asked again
        shot(page, "05b-wrong-pin")
        page.locator("dialog").get_by_role("button", name="Cancel").click()
        expect(page.locator("#pay-form .help.bad").first).to_contain_text("Nothing was done")
        expect(page.locator("#payment-history li")).to_have_count(1)
    step("a wrong PIN is asked again with tries left; cancelling pays nothing", wrong_pin)

    def bad_payee():
        page.locator("input[name=payee]").fill("500100000000")
        page.locator("#pay-form input[name=amount]").fill("10")
        page.get_by_role("button", name="Pay", exact=True).click()
        expect(page.locator("#pay-form .help.bad").first).not_to_be_empty()
        expect(page.locator("#payment-history li")).to_have_count(1)
    step("a mistyped account number is caught before any payment is made", bad_payee)

    def challenge():
        # The same customer on a laptop the bank has never seen, paying someone new.
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        laptop = ctx.new_page()
        globals()["laptop"] = laptop
        watch(laptop, "laptop")
        laptop.set_default_timeout(10_000)
        laptop.goto(BASE + "/#/pay")
        laptop.get_by_label("Email or mobile number").fill(phone)          # mobile number works as well as email
        laptop.get_by_label("Password").fill(PASSWORD)
        laptop.get_by_role("button", name="Sign in", exact=True).last.click()
        expect(laptop.get_by_role("heading", name="Is this you?")).to_be_visible()    # password alone isn't enough
        shot(laptop, "06a-device-code")
        laptop.get_by_label("6-digit code").fill(inbox_code(page, "new device"))                   # sent to her known device
        laptop.get_by_role("button", name="Confirm and sign in").click()
        expect(laptop.get_by_text("New device confirmed")).to_be_visible()
        expect(laptop.get_by_role("heading", name="Pay", exact=True)).to_be_visible()   # back where the link pointed
        globals()["laptop_device"] = laptop.evaluate("localStorage.getItem('ub.device')")
        laptop.locator("input[name=payee]").fill(landlord["account_number"])
        laptop.locator("#pay-form input[name=amount]").fill("7000")
        laptop.locator("input[name=note]").fill("rent")
        laptop.get_by_role("button", name="Pay", exact=True).click()
        enter_pin(laptop, PAY_PIN)
        expect(laptop.locator("#payment-result .stamp")).to_have_text("Confirm it's you")
        shot(laptop, "06-challenge")
        laptop.locator("#payment-result input[name=otp]").fill("000000")
        laptop.get_by_role("button", name="Confirm payment").click()
        expect(laptop.locator("#payment-result form .help.bad")).not_to_be_empty()
    step("a new laptop needs a code to sign in; Rs 7,000 to a new payee from it asks for another; a wrong one is refused", challenge)

    def confirm():
        code = inbox_code(page, "7,000")                   # the code arrives on the customer's own, known device
        shot(page, "07-inbox")
        laptop.locator("#payment-result input[name=otp]").fill(code)
        laptop.get_by_role("button", name="Confirm payment").click()
        expect(laptop.locator("#payment-result .stamp")).to_have_text("Paid")
        laptop.context.close()
    step("the code from the inbox completes the payment", confirm)

    def over_limit():
        page.get_by_role("link", name="Pay", exact=True).click()
        page.locator("input[name=payee]").fill(payee["account_number"])
        page.locator("#pay-form input[name=amount]").fill("99000")
        page.get_by_role("button", name="Pay", exact=True).click()
        enter_pin(page, PAY_PIN)
        expect(page.locator("#payment-result .stamp")).to_have_text("Declined")
        expect(page.locator("#payment-result .checks li.fail")).to_have_count(1)
        expect(page.locator("#payment-result .checks li.fail")).to_contain_text("Your limits")
        shot(page, "08-declined")
    step("Rs 99,000 is over the limit: stamped Declined at the limits check", over_limit)

    def balance_after():
        page.get_by_role("link", name="Accounts", exact=True).click()
        expect(page.locator(".cover .balance").first).to_have_text("₹42,750.00")
    step("balance is Rs 42,750: exactly the two completed payments left", balance_after)

    def saved_payee():
        page.get_by_role("link", name="Pay", exact=True).click()
        page.get_by_role("button", name="Add a payee").click()
        page.locator("dialog input[name=number]").fill(landlord["account_number"])
        page.locator("dialog input[name=number]").dispatch_event("change")
        expect(page.locator("dialog")).to_contain_text("Account holder: Meera S")
        page.locator("dialog input[name=nickname]").fill("Landlord")
        page.get_by_role("button", name="Save payee").click()
        enter_pin(page, PAY_PIN)
        row = page.locator("#payees li").filter(has_text="Landlord")
        expect(row).to_contain_text("up to ₹10,000")
        row.get_by_role("button", name="Pay").click()
        expect(page.locator("input[name=payee]")).to_have_value(landlord["account_number"])
        shot(page, "08a-payees")
    step("saves the landlord as a payee with the payment PIN; it shows its cooling period", saved_payee)

    print("\n4. Deposits")

    def set_app_pin():
        page.get_by_role("link", name="Security", exact=True).click()
        row = page.locator("[data-pin=APP]")
        expect(row).to_contain_text("Not set")
        row.get_by_role("button", name="Set").click()
        page.locator("dialog input[name=password]").fill(PASSWORD)
        page.locator("dialog input[name=pin]").fill(PAY_PIN[:4])                      # the start of the payment PIN
        page.locator("dialog input[name=again]").fill(PAY_PIN[:4])
        page.get_by_role("button", name="Save PIN").click()
        expect(page.locator("dialog").get_by_role("alert")).to_contain_text("different")
        page.locator("dialog input[name=pin]").fill(APP_PIN)
        page.locator("dialog input[name=again]").fill(APP_PIN)
        page.get_by_role("button", name="Save PIN").click()
        expect(row).to_contain_text("Set")
        expect(page.locator("[data-pin=PAYMENT]")).to_contain_text("Set")
    step("sets a 4-digit app PIN on the Security page; one that matches the payment PIN is refused", set_app_pin)

    def book_fd():
        page.get_by_role("link", name="Deposits", exact=True).click()
        expect(page.locator("#quote")).to_contain_text("You get back")
        page.locator("#deposit-form input[name=amount]").fill("10000")
        expect(page.locator("#quote")).to_contain_text("₹10,000.00")
        page.get_by_role("button", name="Book fixed deposit").click()
        enter_pin(page, APP_PIN, "app")
        expect(page.locator("[data-deposit]")).to_have_count(1)
        expect(page.locator("[data-deposit]")).to_contain_text("Fixed deposit of ₹10,000.00")
    step("books a Rs 10,000 fixed deposit from a live quote", book_fd)

    def book_rd():
        page.get_by_role("button", name="Recurring deposit").click()
        page.locator("#deposit-form input[name=amount]").fill("2000")
        expect(page.locator("#quote")).to_contain_text("You put in")
        page.get_by_role("button", name="Start recurring deposit").click()
        enter_pin(page, APP_PIN, "app")
        expect(page.locator("[data-deposit]")).to_have_count(2)
        shot(page, "09-deposits")
    step("starts a Rs 2,000 a month recurring deposit alongside it", book_rd)

    def close_fd():
        page.locator("[data-deposit]").filter(has_text="Fixed deposit").get_by_role("button", name="Close early").click()
        expect(page.locator("dialog")).to_contain_text("Early-closure rate")
        page.locator("dialog button[type=submit]").click()
        enter_pin(page, APP_PIN, "app")
        expect(page.get_by_text("Deposit closed")).to_be_visible()
        expect(page.locator("[data-deposit]").filter(has_text="Fixed deposit")).to_contain_text("Closed")
    step("early closure shows the reduced payout, then closes", close_fd)

    def nominee():
        page.locator("[data-deposit]").filter(has_text="Recurring deposit").get_by_role("link", name="Nominees").click()
        expect(page.get_by_role("heading", name="Nominees")).to_be_visible()
        page.get_by_role("button", name="Add a nominee").click()
        page.locator("dialog input[name=full_name]").fill("Kabir Rao")
        page.locator("dialog select[name=relation]").select_option("CHILD")
        page.locator("dialog input[name=date_of_birth]").fill("2015-04-02")
        page.locator("dialog input[name=share_pct]").fill("60")
        page.get_by_role("button", name="Add nominee").click()
        enter_pin(page, APP_PIN, "app")
        expect(page.locator("dialog").get_by_role("alert")).to_contain_text("guardian")    # a child needs one
        page.locator("dialog input[name=guardian_name]").fill("Asha Rao")
        page.get_by_role("button", name="Add nominee").click()
        enter_pin(page, APP_PIN, "app")
        expect(page.locator("#nominees")).to_contain_text("Kabir Rao")
        expect(page.locator("main")).to_contain_text("40% isn't given to anyone yet")
        shot(page, "09a-nominee")
    step("names a child as nominee for the RD: a guardian is required; shares are tracked", nominee)

    print("\n5. Security")

    def security():
        page.get_by_role("link", name="Security", exact=True).click()
        expect(page.get_by_role("heading", name="Devices you've used")).to_be_visible()
        expect(page.locator("#sessions")).to_contain_text("This device")
        expect(page.locator("main")).to_contain_text("Chrome on")
        expect(page.locator("main table tbody tr").first).to_be_visible()
        shot(page, "11-security")
    step("security page lists this device, the session and sign-in history", security)

    def session_survives_reload():
        page.reload()
        expect(page.get_by_role("heading", name="Security")).to_be_visible()
    step("a reload keeps the session", session_survives_reload)

    def lock_screen():
        page.get_by_role("button", name="Lock now").click()
        lock = page.locator("dialog.lock")
        expect(lock).to_be_visible()
        expect(lock.get_by_role("heading", name="Locked")).to_be_visible()
        page.keyboard.press("Escape")
        expect(lock).to_be_visible()                                                   # Escape doesn't get you in
        shot(page, "11c-locked")
        lock.locator("input").fill("0000")
        lock.get_by_role("button", name="Unlock").click()
        expect(lock.get_by_role("alert")).to_contain_text("tries left")
        lock.locator("input").fill(APP_PIN)
        lock.get_by_role("button", name="Unlock").click()
        expect(lock).to_have_count(0)
        page.get_by_role("link", name="Accounts", exact=True).click()
        expect(page.locator("a.cover").first).to_be_visible()
    step("locks the app; Escape doesn't open it; a wrong PIN is refused, the app PIN unlocks it", lock_screen)

    def locked_elsewhere():
        # The server locks the session itself (here: as if 5 minutes had passed); the next
        # click brings up the lock screen, and the page carries on once unlocked.
        token = page.evaluate("JSON.parse(sessionStorage.getItem('ub.session')).access")
        api.post("/api/auth/lock", headers={"Authorization": f"Bearer {token}"}).raise_for_status()
        page.get_by_role("link", name="Pay", exact=True).click()
        lock = page.locator("dialog.lock")
        expect(lock).to_be_visible()
        lock.get_by_role("button", name="Use my password instead").click()
        lock.locator("input").fill(PASSWORD)
        lock.get_by_role("button", name="Unlock").click()
        expect(page.get_by_role("heading", name="Pay", exact=True)).to_be_visible()
    step("a session locked by the server brings up the lock screen; the password works too", locked_elsewhere)

    print("\n5b. Statements")

    def statements():
        # A month has to be over before it has a statement: move the bank's calendar forward.
        admin = api.post("/api/staff/login", json={"staff_id": "admin", "password": ADMIN_PASSWORD}).json()
        api.post("/api/staff/ops/eod/fast-forward", json={"days": 35}, timeout=600,
                 headers={"Authorization": f"Bearer {admin['access_token']}"}).raise_for_status()
        page.get_by_role("link", name="Accounts", exact=True).click()
        page.locator("a.cover").first.click()
        expect(page.get_by_role("heading", name="Statements")).to_be_visible()
        if page.locator("#more-statements").count():
            page.locator("#more-statements").click()
        first = page.locator("[data-statement]").last                     # the month the account opened in
        expect(first).to_contain_text("Not downloaded yet")
        first.get_by_role("button", name="Open").click()
        expect(page.locator("dialog iframe.file-view")).to_be_visible()
        expect(page.locator("dialog a[download]")).to_have_attribute("download", re.compile(r"^statement-\d{4}-\d{4}-\d{2}\.pdf$"))
        shot(page, "11a-statement")
        page.locator("dialog").get_by_role("button", name="Close").click()
        first.get_by_role("button", name="Check").click()
        expect(page.locator(".toast").filter(has_text="matches the ledger")).to_be_visible()
        page.locator("#interim-form").get_by_role("button", name="Get statement").click()
        expect(page.locator("dialog iframe.file-view")).to_be_visible()
        page.locator("dialog").get_by_role("button", name="Close").click()
    step("opens a monthly statement, checks it against the ledger, gets one for any dates", statements)

    def check_statement_is_genuine():
        token = page.evaluate("JSON.parse(sessionStorage.getItem('ub.session')).access")
        period = page.locator("[data-statement]").last.get_attribute("data-statement")
        account_id = page.url.rsplit("/", 1)[-1]
        pdf = api.get(f"/api/accounts/{account_id}/statements/{period}", headers={"Authorization": f"Bearer {token}"}).content
        (files / "statement.pdf").write_bytes(pdf)
        (files / "forged.pdf").write_bytes(pdf[:len(pdf) // 2] + bytes([pdf[len(pdf) // 2] ^ 1]) + pdf[len(pdf) // 2 + 1:])
        ctx = browser.new_context()                                        # a lender: not signed in
        lender = ctx.new_page()
        watch(lender, "lender")
        lender.goto(BASE + "/")
        lender.get_by_role("button", name="Check it is genuine").click()
        lender.locator("#statement-file").set_input_files(str(files / "statement.pdf"))
        expect(lender.locator("#statement-check-result")).to_contain_text("Genuine")
        expect(lender.locator("#statement-check-result")).to_contain_text("xxxxxxxx")          # account number masked
        shot(lender, "11b-statement-genuine")
        lender.locator("#statement-file").set_input_files(str(files / "forged.pdf"))
        expect(lender.locator("#statement-check-result")).to_contain_text("Not recognised")
        ctx.close()
    step("someone given the PDF confirms it is genuine without signing in; an edited copy fails", check_statement_is_genuine)

    print("\n6. Staff console")

    def fraud_desk():
        staff.get_by_role("link", name="Fraud desk").click()
        expect(staff.get_by_role("heading", name="Fraud desk")).to_be_visible()
        expect(staff.locator(".stat")).to_have_count(4)
        shot(staff, "12-fraud-desk")
    step("fraud desk loads with its counters", fraud_desk)

    def takeover_case():
        # A thief with only the password can't sign in on their own device any more:
        stolen = api.post("/api/auth/login", headers={"X-Sim-Ip": "185.220.101.9", "X-Geo": "55.75,37.61"},
                          json={"identifier": email, "password": PASSWORD, "device_fingerprint": f"thief-{RUN}"})
        assert stolen.status_code == 202 and "access_token" not in stolen.json(), stolen.text
        # So take the harder case: someone with the laptop confirmed today (and the PIN), signing
        # in from the other side of the world and paying someone new. Held, and a case opens.
        login = api.post("/api/auth/login", headers={"X-Sim-Ip": "185.220.101.9", "X-Geo": "55.75,37.61"},
                         json={"identifier": email, "password": PASSWORD, "device_fingerprint": laptop_device}).json()
        auth = {"Authorization": f"Bearer {login['access_token']}", "X-Sim-Ip": "185.220.101.9",
                "Idempotency-Key": str(uuid.uuid4()), "X-Pin": PAY_PIN}
        accounts = api.get("/api/me/accounts", headers=auth).json()
        src = next(a for a in accounts if a["product_type"] == "SAVINGS")
        thief = make_customer_by_api("Mule Account", 2)
        r = api.post("/api/payments", headers=auth, json={"from_account_id": src["account_id"],
                                                          "payee": thief["account_number"], "amount": 900000})
        assert r.json()["status"] in ("HELD", "DECLINED", "CHALLENGE"), r.text
        globals()["takeover"] = r.json()
        deadline = time.time() + 15
        while time.time() < deadline:
            staff.reload()
            expect(staff.get_by_role("heading", name="Fraud desk")).to_be_visible()
            if staff.locator("table tbody tr").filter(has_text="9,000.00").count():
                break
            time.sleep(1)
        if takeover["status"] == "HELD":
            staff.locator("table tbody tr").filter(has_text="9,000.00").first.locator("a").click()
            expect(staff.locator(".slip .stamp")).to_have_text("Under review")
            shot(staff, "13-case")
            staff.get_by_label("Note for the record").fill("Customer confirmed by phone they did not make this payment.")
            staff.get_by_role("button", name=re.compile("^Fraud")).click()
            expect(staff.locator("main").get_by_text("Resolved").first).to_be_visible()
    step("a thief with only the password is stopped at sign-in; a far-away payment from a new device becomes a case", takeover_case)

    def customer_sees_it():
        page.get_by_role("link", name="Inbox").click()
        expect(page.locator("main ul.list li").first).to_be_visible()
        page.get_by_role("link", name="Accounts", exact=True).click()
        if globals().get("takeover", {}).get("status") == "HELD":
            expect(page.locator(".cover.frozen")).to_have_count(1)
    step("the customer sees the alert, and the account is frozen after confirmed fraud", customer_sees_it)

    def approvals():
        staff.get_by_role("link", name="Approvals").click()
        expect(staff.get_by_role("heading", name="Approvals")).to_be_visible()
        if globals().get("takeover", {}).get("status") != "HELD":
            return
        staff.get_by_role("button", name="Ask to unfreeze an account").click()
        staff.locator("dialog input[name=target_id]").fill(takeover["from_account_id"])
        staff.locator("dialog textarea[name=reason]").fill("Customer verified in branch, password reset.")
        staff.get_by_role("button", name="Send for approval").click()
        row = staff.locator("[data-approval]").first
        row.get_by_role("button", name="Approve").click()
        expect(staff.locator(".toast.bad")).to_be_visible()      # the maker cannot also be the checker
        shot(staff, "14-approvals")
    step("unfreeze needs a second officer: the requester cannot approve their own request", approvals)

    def system():
        staff.get_by_role("link", name="System").click()
        expect(staff.locator(".node")).to_have_count(3)
        expect(staff.locator("#recon")).to_contain_text("The ledger balances")
        shot(staff, "15-system")
        staff.locator(".node:not(.down)").get_by_role("button", name="Cut the link").first.click()
        expect(staff.locator(".node.down")).to_have_count(1)
        staff.get_by_role("button", name="Clear all faults").click()
        expect(staff.locator(".node.down")).to_have_count(0)
    step("system page: three DynaStore nodes, ledger balances, cut a link and heal it", system)

    def role_limits():
        ctx = browser.new_context()
        t = ctx.new_page()
        watch(t, "teller")
        t.goto(BASE + "/#/staff")
        t.get_by_label("Staff ID").fill("teller1")
        t.get_by_label("Password").fill(os.getenv("TELLER_PASSWORD", "teller-dev-pass"))
        t.get_by_role("button", name="Sign in to the staff console").click()
        expect(t.get_by_role("heading", name="Branch desk")).to_be_visible()
        expect(t.get_by_role("link", name="Fraud desk")).to_have_count(0)
        ctx.close()
    step("a teller gets the branch desk but not the fraud desk", role_limits)

    print("\n7. Phone-sized screen")

    def phone_layout():
        ctx = payee_browser(**pw.devices["Pixel 7"])
        m = ctx.new_page()
        watch(m, "phone")
        m.goto(BASE + "/")
        shot(m, "20-phone-sign-in")
        m.get_by_label("Email or mobile number").fill(payee["email"])
        m.get_by_label("Password").fill(PASSWORD)
        m.get_by_role("button", name="Sign in", exact=True).last.click()
        expect(m.get_by_role("heading", name="Namaste, Ravi")).to_be_visible()
        for path, name in [("accounts", "21-phone-home"), ("pay", "22-phone-pay"), ("deposits", "23-phone-deposits"),
                           (f"accounts/{payee['account_id']}", "24-phone-passbook"), ("security", "25-phone-security")]:
            m.goto(f"{BASE}/#/{path}")
            expect(m.locator("main h1")).to_be_visible()
            m.wait_for_timeout(300)
            overflow = m.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert overflow <= 1, f"/{path} scrolls sideways by {overflow}px"
            shot(m, name)
        expect(m.locator("nav.rail a:visible")).to_have_count(5)
        m.get_by_role("button", name="Sign out").first.click()
        expect(m.get_by_role("heading", name="Sign in")).to_be_visible()
        ctx.close()
    step("every customer page fits a phone without sideways scrolling; sign-out works", phone_layout)

    print("\n8. Block everything")

    def block_everything():
        ctx = payee_browser(viewport={"width": 1280, "height": 900})
        r = ctx.new_page()
        watch(r, "ravi")
        r.goto(BASE + "/")
        r.get_by_label("Email or mobile number").fill(payee["email"])
        r.get_by_label("Password").fill(PASSWORD)
        r.get_by_role("button", name="Sign in", exact=True).last.click()
        expect(r.get_by_role("heading", name="Namaste, Ravi")).to_be_visible()
        r.goto(BASE + "/#/security")
        r.get_by_role("button", name="Block everything").click()
        r.locator("dialog input[name=sure]").check()
        shot(r, "26-block-everything")
        r.locator("dialog").get_by_role("button", name="Block everything").click()
        expect(r.get_by_text(re.compile("Blocked. 1 account frozen"))).to_be_visible()
        expect(r.get_by_role("heading", name="Sign in")).to_be_visible()             # signed out here too
        assert api.get("/api/me", headers=payee["auth"]).status_code == 401           # and everywhere else
        ctx.close()
    step("Block everything freezes the account, opens a case and signs out every device", block_everything)

    step("no JavaScript errors anywhere", lambda: (_ for _ in ()).throw(AssertionError("; ".join(errors[:5]))) if errors else None)
    browser.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
