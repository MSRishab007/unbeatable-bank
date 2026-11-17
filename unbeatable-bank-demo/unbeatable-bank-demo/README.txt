Unbeatable Bank: offline demo
=============================

This folder is the customer app with built-in demo data. It needs no cloud, no backend
and no install. Nothing here talks to a real bank.

Start it
  Windows   double-click  start-demo.bat
  Mac/Linux run           ./start-demo.sh
Then open http://localhost:8000 (the script opens it for you).

It needs Python 3 (already on Mac and most Linux; on Windows get it from python.org or
the Microsoft Store). Any other static file server pointed at the "app" folder works too,
for example:  npx serve app

Why a server and not a double-click on index.html: browsers block app scripts and the
camera on file:// pages.

What to type
  Sign in         any email and any password
                  (password "wrong" shows the error; an email containing "new" asks for
                   a device code, which is 123456)
  Payment PIN     123456
  App PIN         1234       (unlocks the lock screen, approves deposits)
  One-time code   654321
  Pay to          5001 0002 0001  (Ravi), 0002 (Meera), 0003, 0004, 0005
                  or the pay ID  ravi@unbeatable
  Amounts         Rs 7,000 asks for a one-time code
                  Rs 20,000 is held for review
                  over Rs 50,000 is declined
                  anything else goes through

The dark "Demo data" card in the bottom-left corner of the app repeats these.

Try the QR scanner
  1. Open Receive, add an amount, press "Save picture".
  2. Open Pay, press "Scan a QR code", then "Upload a picture of a QR code" and choose
     the saved picture. Or hold a phone showing the code up to the webcam.
  The camera works on http://localhost. From a phone on the same Wi-Fi the page opens
  at http://<this computer's IP>:8000 but the camera is blocked there (it needs HTTPS),
  so use the upload button.

Limits of the demo
  - Data resets when the page is reloaded.
  - The staff console and "Statements and nominees" belong to the classic app and need
    the real backend, so those links do not work here.
