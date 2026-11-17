# Deploy PayScope's backend to Render

This backend is independent of the Android project. Keep Neon, Neo4j, Firebase and Gemini as your existing connected services. Render hosts only this Python API.

## 1. Create a separate backend repository

The prepared `payscope-backend.zip` contains the backend source and deployment files. Extract it into a new folder, then upload those extracted files to a new private GitHub repository, for example `payscope-backend`.

At the repository root you should see:

```text
app/
alembic/
checks/
tests/
pyproject.toml
uv.lock
alembic.ini
render.yaml
.python-version
.gitignore
.env.example
README.md
DEPLOY_RENDER.md
```

Upload the extracted contents, not the ZIP itself and not an extra enclosing `backend` directory. The archive excludes the real `.env`, virtual environments, local statements and Android code. Set credentials in Render's Environment settings.

You can regenerate the archive from the Android workspace with:

```powershell
python scripts/package_render_backend.py
```

## 2. Create the Render service

1. Sign in at [Render](https://dashboard.render.com/).
2. Select **New > Blueprint** and connect the new backend repository.
3. Render reads `render.yaml` from the repository root.
4. Confirm the service `payscope-api`, region **Singapore**, and **Free** plan.
5. Fill in the environment values requested below and deploy.

The Blueprint sets these automatically:

| Setting | Value |
|---|---|
| Runtime | Python 3.12.10 |
| Build | `uv sync --frozen --no-dev --no-editable` |
| Start | `.venv/bin/python -m app.render_server` |
| Health check | `/api/v1/ready` |
| Port | Render's `PORT` environment variable |
| Workers | 1 |
| Database pool | 3 connections, up to 2 additional connections |
| Automatic deployment | On commits to the connected branch |

If using **New > Web Service** instead, enter the same settings manually and leave **Root Directory** blank for the separate repository. If deploying the entire Android repository instead, set **Root Directory** to `backend`.

## 3. Add environment variables

Copy the working values from your existing local `backend/.env` into Render. Do not put real values in `render.yaml` or `.env.example`.

| Variable | What to enter |
|---|---|
| `DATABASE_URL` | Existing Neon PostgreSQL connection string, including `sslmode=require` |
| `FIREBASE_PROJECT_ID` | The Firebase project already used by your Android app |
| `GEMINI_API_KEY` | Your working server-side Gemini key |
| `GEMINI_MODEL` | The working model name from your local `.env` |
| `NEO4J_URI` | Your Aura connection URI, normally `neo4j+s://...` |
| `NEO4J_PASSWORD` | Your Aura password |
| `NEO4J_DATABASE` | Your existing Aura database name |

The Blueprint generates `API_SESSION_SECRET` automatically. Keep that generated value stable across future deployments; changing it invalidates guest sessions. `NEO4J_USERNAME` defaults to `neo4j`; change it if your existing account uses a different username.

Optional `DATABASE_URL_UNPOOLED`: your direct Neon connection string. When omitted, the app converts a Neon `-pooler` hostname to its matching direct hostname for migrations only. Normal application queries keep using `DATABASE_URL`.

Startup runs Alembic migrations before accepting traffic. Existing records remain in your existing Neon database. No application database migration was executed as part of preparing this deployment; verify future schema changes against your separate Neon test branch before deploying them.

## 4. Verify the deployed backend

After Render reports **Live**, copy the actual public URL shown on the service page. It will resemble `https://payscope-api-xxxx.onrender.com`.

Open these URLs:

```text
https://YOUR-SERVICE.onrender.com/api/v1/health
https://YOUR-SERVICE.onrender.com/api/v1/ready
https://YOUR-SERVICE.onrender.com/api/v1/docs
```

Expected health: `status: ok`. Expected readiness: `status: ready`, `database: connected`. Workspace endpoints require a Firebase or signed guest bearer token; a `401` without one is expected.

Use a sample workspace or a synthetic PDF to check uploads. The API saves validated records, analyzes them, and returns dashboard data even if no recurring subscriptions are found.

## 5. Connect Android separately

The current Android source configuration is unchanged. Once the backend works, run this from `D:\PayScope App` using its actual URL:

```powershell
.\scripts\build-render-apk.ps1 -ApiUrl 'https://YOUR-SERVICE.onrender.com/'
```

This script checks health/readiness and builds a separate copy at:

```text
output/android/payscope-render-debug.apk
```

Install that APK on the phone. It uses HTTPS to reach Render, so it no longer requires USB forwarding or a backend running on your PC. The existing Firebase project and Android signing configuration continue to apply. Recreate guest sample sessions when switching from the local backend; Firebase users keep access to their records when the same Firebase project and Neon database are used.

## Free-plan behavior and email

Render Free sleeps after 15 minutes without incoming traffic. The next request can take about a minute to wake it. The Android client already allows longer requests for PDF/Gemini processing. Heavy PDFs or concurrent users can still exceed free-instance capacity; check Render logs and memory metrics if this happens.

Render's filesystem is temporary. Financial records are stored in Neon and graph projections in Aura, so they do not depend on files on the Render server. No separate Render PostgreSQL database is needed.

Email delivery is disabled in this Blueprint until you configure it. To enable it, add `BREVO_API_KEY` and `BREVO_SENDER_EMAIL` for your verified sender, then set `EMAIL_WORKER_ENABLED=true`. The worker can process queued reports while the service runs, but a sleeping free service cannot guarantee timed renewal reminders. Reliable scheduled reminders require an always-running worker or separately scheduled job; this Blueprint does not create a paid worker or cron service.

## Troubleshooting

| Symptom | Check |
|---|---|
| Build fails | Root Directory, committed `uv.lock`, Python version, and build logs |
| Startup asks for variables | Add the named values under Render > Environment, then redeploy |
| Database readiness fails | Neon URL/SSL, network access rules, and migration logs |
| Gemini review fails | Working key/model, quota and server logs; imported records remain saved |
| Graph uses relational records | Aura URI/password/database, TLS and Aura network access rules |
| Phone still uses localhost | Install the APK built with the Render URL |
| First request is slow | Free service is waking from sleep |

Reference: [Render FastAPI deployment](https://render.com/docs/deploy-fastapi), [Blueprint specification](https://render.com/docs/blueprint-spec), [Python versions](https://render.com/docs/python-version), [free-plan limits](https://render.com/docs/free).
