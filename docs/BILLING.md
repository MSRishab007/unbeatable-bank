# Billing

Project: `unbeatable-bank-XX` · Region: `us-central1`

## Accounts

| Key | Member | Billing account ID | Credit | Expires | Team roles granted? |
|---|---|---|---|---|---|
| a | | | $50 | | n/a (owner) |
| b | | | $50 | | |
| c | | | $50 | | |
| d | | | $50 | | Reserve for demo week |

## Rules

- Switch to the next account at the **$40** alert, never let one reach $50.
- Order: a → b → c, keep d for final integration, load tests and the demo.
- Run `make down` at the end of every cloud session.

## Switch log

| Date | From → To | Spend on old account | By |
|---|---|---|---|
