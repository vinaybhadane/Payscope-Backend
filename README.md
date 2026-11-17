# PayScope API

Standalone Python/FastAPI backend for the PayScope Android app.

For hosting, follow [Deploy to Render](DEPLOY_RENDER.md). The included render.yaml deploys this directory as its own repository. Neon stores transactions and analysis; Neo4j Aura stores graph projections; Firebase verifies identities; Gemini extracts/reviews statements.

## Run locally

Copy .env.example to .env and fill it with your existing working credentials. Then, from this directory:

```powershell
uv sync --extra dev
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Keep credentials in .env locally and in Render environment variables when hosted. No Android project files are required to run this backend.

## Behavior

- Imports validate and deduplicate PDF, CSV, XLSX or JSON transactions before saving them.
- A completed import remains visible on the dashboard even when no recurring subscription is identified.
- Gemini confirms recurrence from at least two observed payment dates. Single-charge services can appear as low-confidence candidates with an estimated billing cycle. Candidates are excluded from confirmed recurring totals and renewal dates until the user confirms them.
- Analytics uses all successful statement debits, including one-off purchases. Categories and merchant labels come from the review; unclassified payments remain visible as Uncategorized if AI is unavailable.
- Savings can include candidates only through an explicit planning request. The server recalculates amounts and stores that the scenario contains estimates.
- Transient Gemini failures are retried with bounded backoff. AI failure leaves imported records and spending analytics available, with a retry action.
- Workspace APIs require a verified Firebase ID token or signed guest token.
- Readiness checks PostgreSQL. Neo4j outages use a clearly labelled relational graph projection.
- Migrations run before startup. Neon migrations use the direct endpoint; ordinary queries use the configured runtime endpoint.
- Statement debit/credit totals retain each currency. Recurring dashboard totals and savings scenarios aggregate INR only.

## Email

The Render Blueprint disables email until its optional Brevo credentials are configured. Timed reminders need an always-running worker or separate scheduler; a sleeping free service cannot guarantee them. Delivery preferences and outbox state are persisted in PostgreSQL.

## Validation

Offline provider/deployment checks:

```powershell
uv run python -m unittest discover -s checks -v
```

Database regression tests require TEST_DATABASE_URL pointing to the separate mandatelens_test database:

```powershell
uv run pytest -q tests/test_mobile_api.py tests/test_ai_import_review.py tests/test_workspace_auth.py
```

The optional live smoke test uses only a disposable synthetic guest workspace and clears it afterwards:

```powershell
uv run python checks/live_pdf_smoke.py --live
```
