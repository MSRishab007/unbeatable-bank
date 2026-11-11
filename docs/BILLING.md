# Billing

Project: `unbeatable-bank-XX` · Region: `us-central1`

## Accounts

| Key | Member | Credit | Expires | Billing roles granted? |
|---|---|---|---|---|
| a | | Free trial, about ₹24,000 | 29 Jan 2027 | |
| b | | $50 education (budget $40) | | |
| c | | $50 education (budget $40) | | |
| d | | $50 education (budget $40) | | Reserve for demo week |

## Rules

- Budgets only send emails; nothing stops by itself until a credit runs out.
- Education accounts (USD): budget $40, alerts at $20 / $30 / $36 / $40. Switch to the
  next account at the $40 email.
- Free-trial account (INR): budget is the whole remaining credit, alerts at 50 / 75 / 90 /
  100%. Switch at the 100% email. Its free-trial limits may refuse the Dataproc step;
  run Flink on an education account.
- Switch by changing `active_billing` in `infra/bootstrap/terraform.tfvars`, then
  `terraform apply` in `infra/bootstrap`, and log it below.
- Keep the last account for final integration, load tests and the demo.
- Run `make down` at the end of every cloud session.
- Keep billing account IDs out of this file: the repository is public.

## Switch log

| Date | From → To | Spend on old account | By |
|---|---|---|---|
