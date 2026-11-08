# BRUXMAP booking site (participant-facing page)

Static page that shows the free BRUXMAP session slots and sends the participant to a
pre-filled Microsoft Form. Design and status: [../booking_system.md](../booking_system.md).
Built 2026-11-08 after Bookings (shared) and Bookings with me were rejected.

```
owner's Outlook calendar ──"Publish a calendar" (Can view when I'm busy)──► secret ICS URL
        │ server.py on the owner's VPS: fetch the ICS per visit, compute, 30 s cache
        ▼
GET /  (site/index.html)   GET /slots.json (date-times only)      ── live, no delay
        │ participant clicks a free time → dialog: name, email, repeat email
        │   checks in the browser: syntax · same twice · typo suggestion (gamil → gmail)
        │   POST /check-domain  (domain only → has MX?)            ── nothing stored
        ▼ Continue → the pre-filled Microsoft Form (slot, name, email) appears inside the dialog
          (official embed mode; fallback link opens it in a new tab) → Submit
Flow B "BRUXMAP - booking" (Power Automate, AU tenant):
        InputOK (shape of slot / email / name) ─ fails → Terminate, nobody emailed
        calendar re-check (free, ≥ 24 h, in campaign) ─ fails → "please choose another"
        ok → anonymous Private Busy event "BRUXMAP session (ref N)" → confirmation with
             .ics + 5 PDFs from SharePoint → Notify_owner "new booking ref N" to the owner
Flow "BRUXMAP - heartbeat" every 15 min: SharePoint reachable? → 1-min Free event = heartbeat;
        the page pauses when it is missing (fail-closed). Pause bookings = turn this flow off.
Flow "BRUXMAP - daily digest" 07:00: who comes today and tomorrow (names via the Forms ref).
Fallback host without a server: GitHub Pages via .github/workflows/pages.yml (5-min refresh).
```

Rules (same as the verified Power Automate slot-finder): campaign 2026-11-09 … 2026-12-05,
every day; starts 09:00 / 12:30 / 15:00 Europe/Copenhagen; 150 min; **only events whose
`X-MICROSOFT-CDO-BUSYSTATUS` is `BUSY` block**; nothing within 24 h of now (owner's rule 2026-11-08). Change them at the
top of `compute_slots.py`.

## Files

| File | Role |
|---|---|
| `compute_slots.py` | reads the ICS (env `ICS_URL` or `--ics file`), expands recurrences, writes `slots.json` (`slots` list + `by_day` map + metadata). `--now` overrides the clock for tests. |
| `site/index.html` | the page; served by `server.py` (or copied to `public/` by CI). Week grid, eligibility gate, paused banner, booking dialog (name / email / repeat email: syntax, typo dictionary `KNOWN`, `POST check-domain`). `FORM_BASE` + `Q` (three Forms question ids) build the pre-filled link, shown embedded (`&embed=true`) in the dialog. |
| `server.py` | **Current host: the owner's VPS.** FastAPI app: `/` = the page, `/slots.json` = live recomputation (cache `CACHE_SECONDS`, default 30; serves the last good copy if the feed is down), `POST /check-domain` (`{"domain"}` → MX/A lookup, 10-min cache, domain only, never logged), `/healthz`. `ICS_URL` from `.env`. |
| `requirements-server.txt` | `requirements.txt` + fastapi + uvicorn + python-dotenv + dnspython (MX lookups). |
| `ecosystem.config.js` | PM2 entry (own venv, own logs, uvicorn on 127.0.0.1:4010, `.env` via `--env-file`). |
| `.env.example` | template for `.env` (`ICS_URL`, optional `CACHE_SECONDS`, `REQUIRE_HEARTBEAT`). |
| `VERSION` | deploy marker shown in `/healthz`; bump it with each change. |
| `.github/workflows/pages.yml` | **Current host: GitHub Pages.** Build + deploy on push to `main`, every 5 min, by hand, and on `repository_dispatch` type `refresh` (sent by Flow B after each booking → the booked slot vanishes within 1–2 min). `ICS_URL` = repository secret. |
| `.gitlab-ci.yml` | Same job for AU GitLab Pages (`ICS_URL` = masked CI/CD variable); kept for when AU GitLab becomes available. |
| `tests/` | `test_compute_slots.py` (rules), `test_heartbeat.py` (fail-closed heartbeat), `test_check_domain.py` (domain endpoint: invalid input, cache, no local part). |

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
cd /home/bruxmap_booking && git pull && venv/bin/pip install -q -r requirements-server.txt && pm2 restart bruxmap-booking
curl -s http://127.0.0.1:4010/healthz     # version must equal the VERSION file in git
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

## Fail-closed design and what to check

Every link in the chain either keeps working or stops bookings visibly. Nothing fails silently.

| Link | If it breaks | How it shows | Effect on participants |
|---|---|---|---|
| VPS / nginx / uvicorn | page unreachable | site down | nothing can be booked (closed) |
| Published ICS feed (Outlook) | fetch fails | `/healthz` → `stale: true`, `last_error`; page keeps the last good copy, banner after 2 h ("Calendar read at" old) | booking paused after 2 h |
| Power Automate heartbeat (`BRUXMAP - heartbeat`, every 15 min) | no 1-min Free event in the last 25 min | `/healthz` → `booking_open: false`; page shows **Booking is temporarily paused** and locks the buttons | closed |
| Flow `BRUXMAP - booking` turned off | not detected: the Power Automate Management connector is not offered in the AU environment (heartbeat v3 dropped the check) | Power Automate itself emails the owner when it turns a flow off; **to pause bookings, turn the heartbeat flow off** (and do so whenever the booking flow is off) | closed once the heartbeat stops |
| Flow B run fails mid-way | `Alert_owner_on_failure` card | owner gets a High-importance email with the response id | that one booking is handled by hand |
| Two submissions for one slot | trigger concurrency 1 + calendar re-check | second gets "please choose another" | no double booking |
| Code deployed but process not restarted | `/healthz` → `version` older than `VERSION` in git | — | run `pm2 restart bruxmap-booking` |
| Participant mistypes or garbles the input | page dialog: syntax, repeat, typo suggestion, domain MX; Flow B `InputOK` → Terminate (Cancelled) | nothing — no event, no email, no owner notice; run shows Cancelled | closed for that submission |
| Well-formed address at a real domain, wrong local part | passes every layer | event created, confirmation bounces (NDR) + `Notify_owner` mail with the same ref | owner deletes the entry; only an emailed verification code would remove this (premium connector — not built) |
| SharePoint / calendar connection token expires (90 idle days) | the heartbeat flow touches a SharePoint file every run → fails → no heartbeat | `Alert_check_failed` email; page paused | closed |
| Forms trigger connection expires | submissions produce no run | Power Automate turns the flow off and emails; heartbeat then pauses the page | closed; weekly self-test booking recommended |
| Owner wants to know who comes | — | `Notify_owner` email per booking; `BRUXMAP - daily digest` at 07:00; Forms → Responses | — |

`REQUIRE_HEARTBEAT=1` (default) is the fail-closed switch; set `REQUIRE_HEARTBEAT=0` in `.env`
only while the heartbeat flow is being set up. The Form's own "Get email notification of each
response" must be OFF: `Notify_owner` is the only booking notice, sent after a successful
confirmation. Weekly: open `/healthz` once (expect `booking_open: true`, small
`heartbeat_age_min`, `stale: false`), make and delete one test booking with your own address, check
the certificate renewal in 1Panel. Form responses are the only register of who booked which slot:
keep each until its session has taken place; delete everything after the campaign.

## Verify locally

```powershell
cd <clone of this repository>
python -m venv .venv; .\.venv\Scripts\pip install -q -r requirements.txt pytest
.\.venv\Scripts\python -m pytest -q
$env:ICS_URL = "<busy-only ICS link>"; .\.venv\Scripts\python compute_slots.py public\slots.json
.\.venv\Scripts\pip install -q -r requirements-server.txt
$env:ICS_URL = "<busy-only ICS link>"; .\.venv\Scripts\python -m uvicorn server:app --port 4010   # then open http://127.0.0.1:4010
curl.exe -s -X POST -H "Content-Type: application/json" -d '{"domain":"gmail.com"}' http://127.0.0.1:4010/check-domain   # {"ok":true,...}
```

Data: the page, the JSON and the repository hold date-times only. What the participant types in
the dialog goes straight into the Microsoft Form link (AU tenant); the VPS receives only the email
domain for the MX check. Bookings are anonymous Private events "BRUXMAP session (ref N)" in the
owner's calendar; names live in the Forms responses, the owner's mailbox and the Flow run history
(secure inputs/outputs).
