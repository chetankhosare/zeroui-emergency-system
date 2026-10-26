# 🛡️ Zero UI — Emergency Assistance System

A Zero-UI emergency response system. A phone detects a fall, runs a 15-second
cancel window, and — unless the user enters their safety PIN — opens a live
emergency session that streams location, DIGIPIN, sensor telemetry, events and
~1-minute audio segments to an authority dashboard in real time.

Three real components, one project:

| Component | Path | What it is |
| --- | --- | --- |
| **Android app** | `mobile-app/` | Flutter app: permissions onboarding, on-device fall detection, countdown + PIN, emergency session, foreground service, GPS/DigiPIN, audio capture |
| **Backend** | `backend/` | FastAPI + SQLite: auth, emergency lifecycle, realtime fan-out, audio storage, DIGIPIN generation, SMS alerts |
| **Dashboard** | `dashboard/` | Live authority console: emergency list, Leaflet map + GPS track, timelines, per-emergency audio |
| **Browser phone** | `mobile-simulator/` | Same flow in a browser tab, for demos and E2E tests without a device |

Everything is served by the backend: `/dashboard/` (console), `/mobile/`
(browser phone), `/docs` (OpenAPI).

---

## Architecture

```
┌──────────────────────────┐        REST + WebSocket        ┌────────────────────────┐
│  Android app (Flutter)   │ ─────────────────────────────▶ │   FastAPI backend      │
│  com.zeroui.app          │ ◀───────────────────────────── │   uvicorn + SQLite     │
│                          │                                │                        │
│ • onboarding/permissions │   POST /api/emergencies/start  │ • auth (device+dash)   │
│ • accelerometer fall det.│   POST .../locations (30 s)    │ • emergency lifecycle  │
│ • 15 s countdown + PIN   │   POST .../telemetry           │ • one-active-per-user  │
│ • foreground service     │   POST .../events              │ • DIGIPIN generation   │
│ • FusedLocation → DigiPIN│   POST .../audio/segments      │ • audio storage        │
│ • PCM16 audio capture    │   WS  /ws/device/{id}          │ • realtime fan-out     │
│ • offline outbox+retry   │   WS  /ws/live-audio/{id}      │ • SMS (Twilio, opt-in) │
└──────────────────────────┘                                └───────────┬────────────┘
                                                                        │ WS /ws/dashboard
┌──────────────────────────┐                                ┌───────────▼────────────┐
│  Browser phone           │  same REST/WS API              │  Authority dashboard   │
│  mobile-simulator/       │ ─────────────────────────────▶ │  dashboard/            │
└──────────────────────────┘                                │ • live list + filters  │
                                                            │ • map + GPS track      │
                                                            │ • DIGIPIN + sensors    │
                                                            │ • event/location trail │
                                                            │ • per-emergency audio  │
                                                            └────────────────────────┘
```

### Emergency lifecycle

```
IDLE ──(impact + stillness, or manual SOS)──▶ COUNTDOWN (15 s, cancelable)
                                                 │                 │
                              "I'm safe" + PIN ──┘                 │ timeout
                                     │                             ▼
                                     ▼                        EMERGENCY (active)
                              nothing is sent              • GPS every 30 s
                              no record created           • DIGIPIN per fix
                                                          • ~60 s audio segments
                                                          • live audio stream
                                                          • event timeline
                                                             │            │
                                              resolve ◀──────┘            └──────▶ cancel (PIN)
                                              status=resolved                   status=cancelled
```

Emergency statuses: `verifying`, `emergency`, `cancelled`, `resolved`.
Dashboard connection states: `live` (recent update), `stale` (no update for
`ZEROUI_STALE_AFTER_SECONDS`), `ended`.

**A user can have only one active emergency.** A second `/start` for the same
user returns `409` with the existing `emergency_id` (or the existing record if
the retry carries the same `client_emergency_id`). Different users run fully
independent emergencies; every request, socket and audio asset is keyed by
`emergency_id`, so no data can cross between them.

---

## Quick start

Prerequisites: Python 3.12, and for the app Flutter 3.41+ with Android SDK 34+.

### 1. Backend (also serves dashboard + browser phone)

```bash
# from the repository root
venv/Scripts/python.exe -m pip install -r backend/requirements.txt     # Windows
# ./venv/bin/python -m pip install -r backend/requirements.txt         # macOS/Linux

cd backend
../venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open:

| URL | What |
| --- | --- |
| http://127.0.0.1:8000/dashboard/ | Authority console (sign in with `ZEROUI_DASHBOARD_TOKEN`) |
| http://127.0.0.1:8000/mobile/ | Browser phone (browser-motion / manual trigger) |
| http://127.0.0.1:8000/docs | OpenAPI explorer |

The default dashboard token is `zeroui-dev-dashboard-token` (from `.env`, or the
development default). The database is created and migrated automatically on
first start — there is no separate migration step.

### 2. Produce a live emergency without a phone

```bash
cd backend
../venv/Scripts/python.exe scripts/device_simulator.py --user demo_phone_001 --seconds 30 --keep-active
```

`device_simulator.py` is a headless phone: it enrolls a device, detects a fall,
runs the countdown, activates, pushes locations/telemetry/events, uploads a
segment and opens the realtime sockets — exactly the calls the Android app makes.

Options: `--trigger {fall,manual,shake,test}`, `--lat/--lng`, `--seconds`,
`--segment-seconds`, `--keep-active`, `--cancel --pin 1234`, `--resolve-only <id>`.

### 3. Android app

```bash
cd mobile-app
flutter pub get
flutter run --dart-define=ZEROUI_API_BASE=http://10.0.2.2:8000      # emulator
```

The emulator reaches the host at `10.0.2.2` (the default). See
[Android app](#android-app) for a physical device.

---

## Configuration

All backend settings are environment variables, read from `backend/.env`
(gitignored) with development defaults so the prototype runs with no setup.
Copy `backend/.env.example` and edit. The important ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ZEROUI_ENV` | `development` | `production` makes the three secrets below mandatory and fails fast if they are still defaults |
| `ZEROUI_DASHBOARD_TOKEN` | `zeroui-dev-dashboard-token` | Authority sign-in token |
| `ZEROUI_ENROLLMENT_TOKEN` | `zeroui-dev-enroll-token` | Phone enrolment token |
| `ZEROUI_JWT_SECRET` | dev default | Signs dashboard session tokens (12 h) |
| `ZEROUI_HOST` / `ZEROUI_PORT` | `0.0.0.0` / `8000` | Bind address |
| `ZEROUI_DATA_DIR` / `ZEROUI_DB_PATH` / `ZEROUI_AUDIO_DIR` | `backend/data*` | SQLite file + audio storage |
| `ZEROUI_COUNTDOWN_SECONDS` | `15` | On-device cancel window |
| `ZEROUI_LOCATION_INTERVAL_SECONDS` | `30` | Live location cadence |
| `ZEROUI_AUDIO_SEGMENT_SECONDS` | `60` | Segment length |
| `ZEROUI_STALE_AFTER_SECONDS` | `90` | No-update threshold for "stale" |
| `ZEROUI_CORS_ORIGINS` | `*` | Dashboard origin(s); must be explicit in production |
| `TWILIO_ACCOUNT_SID` / `_AUTH_TOKEN` / `_PHONE_NUMBER` | empty | Optional SMS. When unset the backend logs `sms skipped` instead of sending |

The app reads its base URL and enrolment token at build time via `--dart-define`
(`ZEROUI_API_BASE`, `ZEROUI_ENROLLMENT_TOKEN`, default `http://10.0.2.2:8000`),
and both can be changed at runtime in the in-app **Settings** screen. The app
also pulls live parameters from `GET /api/config`, so changing the countdown or
intervals on the server changes device behaviour without rebuilding.

---

## API

Authentication: devices send `Authorization: Bearer <device_token>` from
`POST /api/devices/register`; the dashboard sends a session JWT from
`POST /api/auth/dashboard/login`. WebSockets and `<audio>` elements take the
token as `?token=` because browsers cannot set headers on them.

### Auth, devices, config

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/auth/dashboard/login` | dashboard token → session JWT |
| `GET` | `/api/auth/dashboard/session` | validate current session |
| `POST` | `/api/devices/register` | enrol with the enrolment token → `device_token` |
| `GET` | `/api/devices/me` | this device |
| `GET` | `/api/devices/config` | server-side emergency parameters |
| `GET` | `/api/config` | public runtime config (interval, countdown, DigiPIN status) |
| `POST` / `GET` | `/api/pin/set`, `/api/pin/verify`, `/api/pin/status/{user_id}` | safety PIN (hashed) |
| `POST` / `GET` / `DELETE` | `/api/contacts`, `/api/contacts/{user_id}` | emergency contacts for SMS |

### Emergency session (device)

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/emergencies/start` | activate after countdown; idempotent via `client_emergency_id`; `409` if already active |
| `GET` | `/api/emergencies/active` | this user's active emergency |
| `GET` | `/api/emergencies/{emergency_id}` | full record |
| `POST` | `/api/emergencies/{emergency_id}/locations` | GPS fix; server generates the DIGIPIN |
| `POST` | `/api/emergencies/{emergency_id}/telemetry` | battery, network, service state, sensors, capabilities |
| `POST` | `/api/emergencies/{emergency_id}/events` | timeline events |
| `POST` | `/api/emergencies/{emergency_id}/status` | `cancelled` (PIN required) / `resolved` |
| `POST` | `/api/emergencies/{emergency_id}/audio/segments` | multipart ~60 s segment (WAV/WebM) |
| `GET` | `/api/emergencies/{emergency_id}/audio/segments[/{segment_id}]` | list / fetch |

### Dashboard (authority)

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/dashboard/snapshot` | full bootstrap: emergencies + stats + config |
| `GET` | `/api/dashboard/emergencies` | list (`?status=&limit=`) |
| `GET` | `/api/dashboard/emergencies/{emergency_id}` | detail |
| `GET` | `/api/dashboard/emergencies/{emergency_id}/locations` | GPS track |
| `GET` | `/api/dashboard/emergencies/{emergency_id}/events` | event timeline |
| `GET` | `/api/dashboard/emergencies/{emergency_id}/audio/segments[/{segment_id}]` | audio, scoped to this emergency |
| `GET` | `/api/dashboard/emergencies/{emergency_id}/live-audio/state` | is the phone streaming? |
| `POST` | `/api/dashboard/emergencies/{emergency_id}/request-live-audio?enabled=` | ask the phone to (stop) streaming |
| `POST` | `/api/dashboard/emergencies/{emergency_id}/resolve` | close the emergency |
| `GET` | `/api/dashboard/stats`, `/api/dashboard/devices`, `/api/dashboard/health` | aggregates |

### Realtime channels

| Socket | Auth | Purpose |
| --- | --- | --- |
| `WS /ws/dashboard?token=<session>` | dashboard | `hello` (full snapshot) then `emergency.created`, `emergency.updated`, `emergency.ended`, `event.added`, `location.added`, `audio.segment`, `audio.live.state`, `telemetry.updated` |
| `WS /ws/device/{emergency_id}?token=<device>` | device | device pushes; server acks + commands (`live_audio.request`) |
| `WS /ws/live-audio/{emergency_id}?token=<session>` | both ends | binary forward-only live audio for **one** emergency |

Every realtime message carries its `emergency_id`; clients ignore frames for
other emergencies, and the server only ever fans a message out to subscribers of
that emergency.

---

## Database

SQLite (`backend/data/emergencies.db`), WAL mode. Tables: `device_enrollments`,
`emergencies`, `emergency_events`, `emergency_locations`, `audio_segments`,
`emergency_contacts`, `user_pins`, `dashboard_audit`.

`emergencies.emergency_id` is a UUID and is the key for every child row
(`emergency_events.emergency_id`, `emergency_locations.emergency_id`,
`audio_segments.emergency_id`) — this is what makes cross-emergency leakage
structurally impossible. A partial unique index enforces one active emergency
per user at the database level, not just in application code.

`backend/migrations.py` runs on every start (`ensure_schema`): `CREATE TABLE IF
NOT EXISTS` for each table, then additive `ALTER TABLE ... ADD COLUMN` for
columns added after a table already existed, then indexes. It is idempotent and
preserves existing data, so an existing prototype database upgrades in place —
verified against a copy of a real 21-emergency database with events, locations
and audio intact.

---

## Dashboard

Single-page console in `dashboard/` (vanilla JS, no build step):

- **Live list** — every emergency with `emergency_id`, user, status, trigger,
  last fix, DIGIPIN, segment count, relative time, and one-click filters
  (`All / Active / Cancelled / Resolved / Stale`). New emergencies raise an alarm
  banner and a toast.
- **Map** — Leaflet + OpenStreetMap. Marker per emergency, colour-coded by state,
  plus the GPS track for the selected emergency and a popup with DIGIPIN and maps link.
- **Detail** — status/connection badge, emergency ID (copyable), trigger,
  DIGIPIN, coordinates, accuracy, duration, last update, device string.
- **Device & sensor status** — battery, network, app state, foreground service,
  screen, audio session, segment number, motion, accelerometer, capabilities,
  impact/free-fall/inactivity, countdown.
- **Audio** — strictly scoped to the selected `emergency_id`: a **Listen live**
  button (only enabled when the phone is streaming) and the stored ~1-minute
  segments with play/download. Audio can never mix between emergencies: each
  segment URL is `/api/dashboard/emergencies/{emergency_id}/audio/segments/{id}`
  and the server verifies the segment belongs to that emergency.
- **Timelines** — location samples with DigiPIN, and the full event timeline.
- **Resolve** — close an emergency with an optional note.
- Sign-in with the dashboard token, 12 h session, dark/light theme, connection
  badge (`Live` / `Reconnecting…` / `Offline`).

The browser phone (`mobile-simulator/`, served at `/mobile/`) mirrors the Android
flow: permissions, motion-based trigger, countdown with PIN, live session. It
records real audio from the microphone when granted, and can fall back to
synthesised audio for offline demos — the dashboard labels simulated sources.

---

## Android app

`mobile-app/` — Flutter, package `com.zeroui.app`, minSdk 24.

**Screens:** onboarding (permissions with in-context explanations, PIN setup),
home (monitoring state, manual SOS, live sensor readout), countdown (big timer,
`I'm safe` → PIN), active emergency (status, timer, live location + DIGIPIN,
audio state, event trail, `I'm safe`/resolve), settings (server URL, trigger
thresholds, contacts, PIN change).

**How the real pipeline works:**

1. **Fall detection** — `sensors.dart` listens to the accelerometer, looking for
   free-fall (low-g) → impact spike → post-impact stillness. Thresholds are
   tunable in Settings and validated by unit tests. If the device has no
   accelerometer the app says so and relies on manual SOS only.
2. **Countdown** — on detection the app shows a 15 s (server-configurable)
   countdown with strong vibration. **Cancelling requires the PIN** and creates
   *nothing* on the backend: no record, no socket, nothing on the dashboard.
3. **Activation** — on timeout the app obtains a real GPS fix first (retrying
   rather than inventing coordinates) and calls `/api/emergencies/start`.
4. **Foreground service** — an Android `foregroundServiceType="location|microphone|dataSync"`
   service keeps the session alive with the screen off. Its status is reported as
   telemetry, so the dashboard can see if the OS stopped it.
5. **Location** — every 30 s; the **server** generates the DIGIPIN from the
   lat/lng using the real India Post `digipin` library. Outside India the library
   returns nothing; the backend stores that reason instead of faking a code, and
   the dashboard shows "unavailable" with the reason.
6. **Audio** — capture starts **only** after activation and only with
   `RECORD_AUDIO`. PCM16 mono 16 kHz is packaged into WAV and uploaded as
   ~60 s segments; the same stream is forwarded over
   `WS /ws/live-audio/{emergency_id}` when the authority requests live listening.
7. **Resilience** — an offline outbox queues locations/telemetry/events while
   the network is down and flushes them in order on reconnect; the device socket
   reconnects with backoff and re-announces its state.

### Build and install

```bash
cd mobile-app
flutter pub get

# Emulator (host reachable at 10.0.2.2)
flutter build apk --release --dart-define=ZEROUI_API_BASE=http://10.0.2.2:8000

# Physical device on the same Wi-Fi: use the host's LAN IP
flutter build apk --release \
  --dart-define=ZEROUI_API_BASE=http://192.168.1.5:8000 \
  --dart-define=ZEROUI_ENROLLMENT_TOKEN=zeroui-dev-enroll-token
```

APK: `mobile-app/build/app/outputs/flutter-apk/app-release.apk`

Install and run:

```bash
flutter devices
flutter run -d <device-id> --dart-define=ZEROUI_API_BASE=http://192.168.1.5:8000
# or
adb install -r build/app/outputs/flutter-apk/app-release.apk
```

The backend must be started with `--host 0.0.0.0` and the Windows firewall must
allow port 8000 for a physical device to connect.

### Android configuration

`mobile-app/android/app/src/main/AndroidManifest.xml` declares:

| Permission | Why the app asks |
| --- | --- |
| `INTERNET`, `ACCESS_NETWORK_STATE` | REST + WebSocket to the backend |
| `ACCESS_COARSE_LOCATION`, `ACCESS_FINE_LOCATION` | live GPS tracking and the DIGIPIN |
| `ACCESS_BACKGROUND_LOCATION` | keep sending location with the screen off (Android 10+) |
| `RECORD_AUDIO` | emergency audio capture (live + segments) |
| `FOREGROUND_SERVICE` + `FOREGROUND_SERVICE_LOCATION` / `_MICROPHONE` / `_DATA_SYNC` | keep the emergency session alive |
| `POST_NOTIFICATIONS` | the persistent "emergency active" notification (Android 13+) |
| `WAKE_LOCK`, `VIBRATE` | keep the CPU awake, alert the user during the countdown |
| `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` | optional, asked during setup so the OS does not kill the session |

Accelerometer/GPS/microphone are declared `required="false"`: the app installs on
devices without them and degrades honestly.

Also configured: `minSdk 24`, `usesCleartextTraffic="true"` for local HTTP
development — **remove this and serve HTTPS in production**. Runtime permissions
are requested in one place by a small Kotlin bridge (`PermissionBridge.kt`) so
the onboarding screen can explain each one before the OS dialog appears.

---

## Testing

```bash
# Backend: 24 tests (digipin, emergency API, per-emergency isolation, live E2E over real sockets)
cd backend
../venv/Scripts/python.exe -m pip install -r requirements-dev.txt
../venv/Scripts/python.exe -m pytest tests/ -v

# Android app: fall detector, WAV packaging, payload shapes, config parsing
cd mobile-app
flutter analyze
flutter test
```

`tests/test_isolation.py` is the important one: it drives two users with
simultaneous emergencies and asserts that neither can read the other's record,
locations, events or audio, that `409` protects one-active-per-user, and that a
cancel without the PIN is rejected.

### End-to-end acceptance (backend + dashboard)

```bash
# 1. start the backend (see Quick start) and open the dashboard, sign in

# 2. user A: cancel safely during the countdown — nothing appears on the dashboard
#    (do this in the app or browser phone: trigger, then "I'm safe" + PIN)

# 3. user A: let it activate
cd backend && ../venv/Scripts/python.exe scripts/device_simulator.py \
    --user demo_phone_001 --trigger fall --seconds 40 --segment-seconds 20 --keep-active

# 4. user B: simultaneously, a second independent emergency
../venv/Scripts/python.exe scripts/device_simulator.py \
    --user demo_phone_002 --trigger manual --lat 18.52 --lng 73.85 --seconds 40 --keep-active

# 5. resolve one of them
../venv/Scripts/python.exe scripts/device_simulator.py --resolve-only <emergency_id>
```

Expected: both appear live on the dashboard without refreshing, each with its own
ID, DIGIPIN, location trail, timeline and audio; switching between them never
mixes audio or timelines; the cancelled/ended ones leave the Active count.

---

## Deployment notes

1. Set `ZEROUI_ENV=production` and real values for `ZEROUI_DASHBOARD_TOKEN`,
   `ZEROUI_ENROLLMENT_TOKEN`, `ZEROUI_JWT_SECRET` — startup fails if they are
   still the development defaults.
2. Set `ZEROUI_CORS_ORIGINS` to your dashboard origin(s), not `*`.
3. Serve over HTTPS (reverse proxy) so device tokens, location and audio are
   encrypted; then remove `usesCleartextTraffic` and rebuild the app.
4. Run behind a process manager (`systemd`, Docker, NSSM) with the data dir on
   persistent storage: `ZEROUI_DATA_DIR` holds the SQLite database and all audio.
5. For multiple workers, move state out of process (the realtime hub and the
   SQLite file assume a single worker) or put the hub behind Redis.
6. `POST_NOTIFICATIONS` + a foreground service keep the app alive, but Android
   battery optimisation can still stop long sessions — advise users to allow the
   exemption the onboarding screen requests.

---

## Real-device limitations (by design, documented not hidden)

- **Live audio is best-effort.** Android allows foreground-service microphone
  access, but aggressive OEM battery managers (Xiaomi/Huawei/Samsung) can kill
  the service anyway. The app reports its actual service state as telemetry, and
  the dashboard's **Listen live** is only enabled when the phone reports it is
  streaming. Segment upload resumes automatically after a gap.
- **`ACCESS_BACKGROUND_LOCATION` on Android 11+** must be granted from the
  Settings screen ("Allow all the time"); the app cannot request it in-line and
  explains this in onboarding. Without it, location only updates while the app is
  in the foreground.
- **DIGIPIN only exists inside India.** Outside the coverage area the backend
  returns no code plus the library's reason — it is never fabricated.
- **Fall detection is accelerometer-only.** It is a threshold detector, not an
  ML model, and will miss slow falls and can false-positive on hard drops.
  Thresholds are user-tunable; there is no gyroscope-based refinement on devices
  without one.
- **SMS alerting needs Twilio credentials** and a configured emergency contact;
  without them the backend deliberately logs `sms skipped`, which the dashboard
  shows in the event timeline.
- **Background location accuracy** can be degraded by the OS while the app is
  backgrounded; the reported accuracy reflects what the device actually gives.
- **Cleartext HTTP** is enabled for local development only.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Phone cannot reach the backend | Start uvicorn with `--host 0.0.0.0`, allow port 8000 in the firewall, and use the host's LAN IP in `ZEROUI_API_BASE` or the app's Settings screen |
| Dashboard says "Invalid dashboard token" | Use the value of `ZEROUI_DASHBOARD_TOKEN` (`zeroui-dev-dashboard-token` by default) |
| Dashboard shows "Session expired" | The JWT expired or the secret changed; sign in again |
| Emergency never appears | Check the device enrolled (`POST /api/devices/register` returns a token) and that `/api/emergencies/start` returned 200, not `409` |
| No audio on the dashboard | Audio starts only after activation and only with `RECORD_AUDIO`; "Listen live" needs the phone's foreground service to report `running` |
| Everything is "Stale" right after a restart | Expected: staleness is computed from the last device update |
| `flutter build` fails on Gradle | Use Flutter 3.41+, Android SDK 34+, and make sure only one platform toolchain is on `PATH` |
