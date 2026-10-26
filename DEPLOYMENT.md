# Zero UI — Deployment Guide (Oracle VPS, self-hosted)

> **Scope of this file:** everything *you* do when moving the backend + dashboard
> to your Oracle VPS. No deployment automation is included on purpose — you run
> each step yourself. Nothing in this repository connects to or configures your
> server.

## 0. What ships

| Piece | Where | Notes |
|---|---|---|
| Backend API | `backend/` (FastAPI) | Single Python process. Serves the dashboard at `/dashboard/` and the phone endpoints under `/api/*`. |
| SQLite database | configurable (`ZEROUI_DB_PATH`) | One file; `backend/data/` by default. |
| Audio segments | configurable (`ZEROUI_AUDIO_DIR`) | Written by the backend. Persistent disk required. |
| Dashboard | `dashboard/` (static) | Served by the backend container/process. |
| Mobile app | `mobile-app/` (Flutter + native Kotlin) | Release APK built on your Windows machine (see §4). |

## 1. Prerequisites on the VPS (you configure)

- Ubuntu 22.04/24.04 with Python 3.11+.
- A domain with A/AAAA record → your VPS public IP.
- TLS: nginx (or Caddy) with Let's Encrypt certificates. **HTTPS is required in
  production** — the app records microphone/location, and browsers only grant
  those to secure origins.
- A dedicated unprivileged user for the service, e.g. `zeroui`.

## 2. Environment variables (backend/.env — no real secrets here)

Copy `backend/.env.example` → `backend/.env` on the VPS and set:

```ini
ZEROUI_ENV=production
ZEROUI_DASHBOARD_TOKEN=<long random string>     # rotate from the dev default
ZEROUI_ENROLLMENT_TOKEN=<long random string>    # the phone's one-time enrolment secret
ZEROUI_JWT_SECRET=<32+ random bytes>
ZEROUI_CORS_ORIGINS=https://your-domain.example
ZEROUI_DATA_DIR=/var/lib/zeroui/data            # persistent, backed-up location
ZEROUI_AUDIO_DIR=/var/lib/zeroui/data/audio
ZEROUI_DB_PATH=/var/lib/zeroui/data/emergencies.db
# Optional SMS (leave unset to disable):
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
ZEROUI_DASHBOARD_URL=https://your-domain.example/dashboard/
```

The production validator refuses to start with dev defaults or `CORS_*`
wildcards, so misconfiguration fails fast and loudly.

## 3. Run the backend

```bash
cd backend
python -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000   # behind nginx/caddy
```

Recommended systemd unit (excerpt):

```ini
[Service]
User=zeroui
WorkingDirectory=/opt/zeroui/backend
ExecStart=/opt/zeroui/backend/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
```

nginx reverse proxy must include WebSocket upgrade headers for `/ws/`:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}
```

## 4. Mobile app — production build

The app's API URL is **baked at build time** and overridable in-app (Settings →
Backend URL), so the release build must NOT point at a LAN address:

```powershell
cd mobile-app
flutter build apk --release `
  --dart-define=ZEROUI_API_BASE=https://your-domain.example `
  --dart-define=ZEROUI_ENROLLMENT_TOKEN=<the same production enrollment token>
```

`usesCleartextTraffic` stays *disabled-safe* for a production https:// URL; it is
only needed while pointing at `http://` dev servers.

## 5. First-run checklist

- [ ] Domain resolves; TLS certificate valid (check with any browser + curl).
- [ ] `GET /health` returns `ok` and shows `env: production`.
- [ ] `/dashboard/` loads; sign-in works with the production dashboard token.
- [ ] Phone enrols against the production enrolment token (Settings → Backend).
- [ ] WebSocket connects (the dashboard header shows "Live", not "Reconnecting").
- [ ] Audio segments upload and play back in the dashboard.
- [ ] `ZEROUI_DATA_DIR` is on persistent disk and included in backups.

## 6. Backups

SQLite: safe to copy with the database tool's backup command (recommended) or a
filesystem snapshot; audio directory is flat files — include it in the same
schedule.

--- Generated as part of the Zero UI final-review deliverables; no
    deployment automation is included or executed. ---
