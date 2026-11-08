# BRUXMAP booking site (participant-facing page)

Static page that shows the free BRUXMAP session slots and sends the participant to a
pre-filled Microsoft Form. Design and status: [../booking_system.md](../booking_system.md).
Built 2026-11-08 after Bookings (shared) and Bookings with me were rejected.

```
owner's Outlook calendar ──"Publish a calendar" (Can view when I'm busy)──► secret ICS URL
        │ GitHub Actions (every 5 min, every push, dispatch after each booking) — compute_slots.py
        ▼
public/slots.json (date-times only) + public/index.html  ──► GitHub Pages
        │ participant clicks a time → Microsoft Form pre-filled with "YYYY-MM-DD HH:MM"
        ▼
Power Automate Flow B: re-check the window → Create event (V4), Busy, 150 min, participant
as attendee → confirmation email with the consent forms; else "please choose another"
```

Rules (same as the verified Power Automate slot-finder): campaign 2026-11-09 … 2026-12-05,
every day; starts 09:00 / 12:30 / 15:00 Europe/Copenhagen; 150 min; **only events whose
`X-MICROSOFT-CDO-BUSYSTATUS` is `BUSY` block**; nothing within 48 h of now. Change them at the
top of `compute_slots.py`.

## Files

| File | Role |
|---|---|
| `compute_slots.py` | reads the ICS (env `ICS_URL` or `--ics file`), expands recurrences, writes `slots.json` (`slots` list + `by_day` map + metadata). `--now` overrides the clock for tests. |
| `site/index.html` | the page; copied to `public/` by CI. Set `FORM_URL_TEMPLATE` once (pre-filled Form link with `{slot}`). |
| `.github/workflows/pages.yml` | **Current host: GitHub Pages.** Build + deploy on push to `main`, every 5 min, by hand, and on `repository_dispatch` type `refresh` (sent by Flow B after each booking → the booked slot vanishes within 1–2 min). `ICS_URL` = repository secret. |
| `.gitlab-ci.yml` | Same job for AU GitLab Pages (`ICS_URL` = masked CI/CD variable); kept for when AU GitLab becomes available. |
| `tests/test_compute_slots.py` | rule tests on a synthetic feed. |

## Deploy (GitHub Pages — current, AU GitLab unavailable 2026-11-08)

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
```

Data: the page, the JSON and the repository hold date-times only. Participant name and email
go through Microsoft Forms into the AU tenant; bookings are events in the owner's calendar.
