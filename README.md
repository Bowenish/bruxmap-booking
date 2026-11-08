# BRUXMAP booking site (participant-facing page)

Static page that shows the free BRUXMAP session slots and sends the participant to a
pre-filled Microsoft Form. Design and status: [../booking_system.md](../booking_system.md).
Built 2026-11-08 after Bookings (shared) and Bookings with me were rejected.

```
owner's Outlook calendar ──"Publish a calendar" (Can view when I'm busy)──► secret ICS URL
        │ server.py on the owner's VPS: on every visit fetch the ICS, compute, 30 s cache
        ▼
GET /  (site/index.html)   GET /slots.json (date-times only)      ── live, no delay
        │ participant clicks a time → Microsoft Form pre-filled with "YYYY-MM-DD HH:MM"
        ▼
owner (by hand for now; Flow B later): Busy event 150 min in the calendar, participant as
attendee → confirmation email with the consent forms. The slot vanishes on the next visit.
Fallback host without a server: GitHub Pages via .github/workflows/pages.yml (5-min refresh).
```

Rules (same as the verified Power Automate slot-finder): campaign 2026-11-09 … 2026-12-05,
every day; starts 09:00 / 12:30 / 15:00 Europe/Copenhagen; 150 min; **only events whose
`X-MICROSOFT-CDO-BUSYSTATUS` is `BUSY` block**; nothing within 48 h of now. Change them at the
top of `compute_slots.py`.

## Files

| File | Role |
|---|---|
| `compute_slots.py` | reads the ICS (env `ICS_URL` or `--ics file`), expands recurrences, writes `slots.json` (`slots` list + `by_day` map + metadata). `--now` overrides the clock for tests. |
| `site/index.html` | the page; served by `server.py` (or copied to `public/` by CI). Set `FORM_URL_TEMPLATE` once (pre-filled Form link with `{slot}`). |
| `server.py` | **Current host: the owner's VPS.** FastAPI app: `/` = the page, `/slots.json` = live recomputation (cache `CACHE_SECONDS`, default 30; serves the last good copy if the feed is down), `/healthz`. `ICS_URL` from `.env`. |
| `requirements-server.txt` | `requirements.txt` + fastapi + uvicorn. |
| `ecosystem.config.js` | PM2 entry (own venv, own logs, uvicorn on 127.0.0.1:4010, `.env` via `--env-file`). |
| `.env.example` | template for `.env` (`ICS_URL`, optional `CACHE_SECONDS`). |
| `.github/workflows/pages.yml` | **Current host: GitHub Pages.** Build + deploy on push to `main`, every 5 min, by hand, and on `repository_dispatch` type `refresh` (sent by Flow B after each booking → the booked slot vanishes within 1–2 min). `ICS_URL` = repository secret. |
| `.gitlab-ci.yml` | Same job for AU GitLab Pages (`ICS_URL` = masked CI/CD variable); kept for when AU GitLab becomes available. |
| `tests/test_compute_slots.py` | rule tests on a synthetic feed. |

## Deploy (VPS — current, live slots)

A standalone project at `/home/bruxmap_booking` with its own venv, logs and `.env`. It shares
nothing with other projects on the box except the per-user PM2 daemon and the 1Panel/OpenResty
front. Use a neutral domain or subdomain for the study page, not a game-branded one.

```bash
# on the VPS — first time
git clone <repo holding this folder's contents> /home/bruxmap_booking     # or scp the folder
cd /home/bruxmap_booking
python3 -m venv venv && venv/bin/pip install -r requirements-server.txt
mkdir -p logs
cp .env.example .env && nano .env            # paste the busy-only ICS link
chmod 600 .env
pm2 start ecosystem.config.js && pm2 save
curl -s http://127.0.0.1:4010/healthz && curl -s http://127.0.0.1:4010/slots.json | head -c 300

# later updates
cd /home/bruxmap_booking && git pull && pm2 restart bruxmap-booking
```

1Panel → Websites → new reverse-proxy site for the chosen domain → upstream
`http://127.0.0.1:4010`, HTTPS certificate on; or in an existing server block:

```nginx
location / { proxy_pass http://127.0.0.1:4010/; proxy_set_header Host $host; }
```

Open the domain in a private window: the day grid shows only free times and "Calendar read at"
is the current minute. Add a Busy event in Outlook, reload after 30 s: the slot is gone.

## Deploy (GitHub Pages — fallback without a server)

1. Public repo `bruxmap-booking` under the owner's GitHub account; this repo folder stays the
   source, the GitHub repo is a deploy copy (copy on change).
2. Copy this folder's contents to the repo root (the `.github/workflows/pages.yml` path matters).
3. Settings → Secrets and variables → Actions → New repository secret `ICS_URL` = the busy-only
   ICS link from Outlook.
4. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
5. Push to `main`; Actions tab → workflow *pages* → green; the site is at
   `https://<account>.github.io/bruxmap-booking/`. Open it in a private window: the day grid
   shows only free times. The schedule then refreshes it every 5 minutes; Flow B's dispatch
   refreshes it right after each booking (GitHub connector action *Create a repository dispatch
   event*, Repository Owner = account, Repository Name = `bruxmap-booking`, Event Name `refresh`).

AU GitLab variant: project Public, Pages access Everyone, shared runner, `ICS_URL` masked
variable, pipeline schedule `*/15 * * * *`; `.gitlab-ci.yml` is already in place.

## Verify locally

```powershell
cd ethics/projects/bruxmap_healthtech_pilot/recruitment/booking_site
python -m venv .venv; .\.venv\Scripts\pip install -q -r requirements.txt pytest
.\.venv\Scripts\python -m pytest -q
$env:ICS_URL = "<busy-only ICS link>"; .\.venv\Scripts\python compute_slots.py public\slots.json
.\.venv\Scripts\pip install -q -r requirements-server.txt
$env:ICS_URL = "<busy-only ICS link>"; .\.venv\Scripts\python -m uvicorn server:app --port 4010   # then open http://127.0.0.1:4010
```

Data: the page, the JSON and the repository hold date-times only. Participant name and email
go through Microsoft Forms into the AU tenant; bookings are events in the owner's calendar.
