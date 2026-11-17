# Unbeatable Bank customer app

React 18, TypeScript, Vite 6, Tailwind CSS 4 and Motion. `npm run build` writes the app to
`services/gateway/static/`, which is committed (the gateway serves it at `/`).

## Try it with no backend and no cloud

    cd web
    npm ci
    npm run demo        # or `make demo` from the repo root

That opens the app at http://localhost:5173 with a built-in demo bank (`src/mocks`) answering
every `/api` call. Sign in with any email and password. Payment PIN `123456`, app PIN `1234`,
one-time code `654321`. Pay to `5001 0002 0001`. Paying ₹7,000 is challenged, ₹20,000 is held,
more than ₹50,000 is declined. A dark "Demo data" card in the corner of the app repeats this.

`npm run demo:build` writes a self-contained copy to `web/demo-dist` (not committed). Any static
file server can serve that folder, for example `python3 -m http.server` inside it, so it can be
handed to someone who has neither Node nor the repository. `npm run demo:preview` serves it.

The demo bank is bundled only in demo mode (`vite --mode demo`, or `VITE_MOCK=1`). `npm run build`
never sets that flag, so the bundle the gateway serves does not contain it. The staff console
and the other classic-app links need the real backend and do not work in the demo.

## Commands

    npm run dev          # against a real gateway on localhost:8080 (python scripts/dev_stack.py)
    npm run demo         # no backend: built-in demo data
    npm run typecheck
    npm run build        # or `make web` from the repo root; commit services/gateway/static

## Changing the look

Every colour, radius, shadow and timing is a token in the `@theme` block at the top of
`src/index.css`. Tailwind's default palette is switched off, so components can only use the
utilities those tokens produce (`bg-accent`, `text-ink`, `rounded-card`, `shadow-float`,
`ease-spring`). Edit that block to re-skin the whole app. Three things live outside it because
they cannot read CSS variables: `public/favicon.svg`, the `theme-color` meta tag in
`index.html`, and the spring presets in `src/lib/motion.ts`.

The landing hero and the sign-in panel use `hero`, a lighter orange than `accent`.

Rules the palette follows: green (`pay`, `success`) only ever means money moving; orange
(`accent`) is never used as text on a light surface, use `accent-text` there; no decorative
gradients (the one gradient is the fill of the swipe track).

## Where things are

| Path | What |
|---|---|
| `src/components/Headline.tsx` | The landing headline: ten lines, one effect per final word |
| `src/components/Logo.tsx` | The "un" mark (`Mark`: tile or bare, three tones), the bolt, the wordmark |
| `src/components/SwipeButton.tsx` | Swipe to pay |
| `src/components/Scanner.tsx`, `src/lib/qr.ts`, `src/lib/paycode.ts` | QR scanner: camera, picture upload, decoding, pay-code rules |
| `src/components/Payment.tsx` | Payment rows, the six checks, the result slip in each state |
| `src/components/Prompts.tsx` | PIN sheet, set-PIN sheet, lock screen (`PinBoxes` uses `input-otp`) |
| `src/components/AppShell.tsx` | Phone tab bar, desktop side rail, page transitions |
| `src/components/ui/` | Button, Card, Modal (sheet on phones, dialog from 640px), inputs, CountUp |
| `src/mocks/` | The development-only mock bank |

The staff console is still the classic app at `/classic/#/staff`; staff sessions are sent there.
