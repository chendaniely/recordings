# n8n automation for recordings

Ideas from 2026-10-16. Not yet scoped, not yet planned. Something nice to think about as the homelab builds up.

## The idea

The recordings app currently does two things in one container: **process things** (transcription, diarization, notes) and **schedule things** (backup, restore tests, Plaud sync, disk checks). n8n could own the scheduling and cross-system coordination, leaving the app to focus on what it's already doing well.

This isn't about replacing the app. It's about making the app a composable part of a larger homelab.

## What n8n would own

**Scheduling.** When to run each task: backup at 2am, Plaud sync every 4 hours, restore test monthly, disk check hourly. n8n's cron nodes are visual and editable — no code change to adjust a schedule.

**Notifications.** ntfy already handles backup/job alerts. n8n extends this to systems the app doesn't talk to: Slack, Telegram, Google Calendar, a home status page. The app logs the result; n8n decides where to notify.

**Monitoring.** Is the Spark reachable? Is the NAS mirror in sync? Is the app healthy? n8n polls endpoints and surfaces results in a single dashboard.

**New automations without app changes.** "When a recording gets tagged `course-101`, post to a Discord channel" — a new n8n workflow, no deploy.

## What stays in the app

- Job processing (transcription, diarization, notes)
- Archive management (writes, locks, state.db, index.db)
- Web UI and API
- Backup/restore *execution* — n8n triggers, the app does it
- Plaud sync *execution* — n8n triggers, the app does it

The worker container still exists and still processes jobs. It just doesn't schedule itself.

## Architecture sketch

```
┌───────────────────────── homelab machine (Docker Compose) ────────────────────────┐
│                                                                                  │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                      │
│  │    n8n       │────▶│ recordings   │     │  DGX Spark   │                      │
│  │  (cron)      │     │   -ui        │────▶│  (Whisper,   │                      │
│  │  (monitor)   │     │   worker     │     │   pyannote)  │                      │
│  │  (notify)    │     │              │     │              │                      │
│  └──────────────┘     └──────────────┘     └──────────────┘                      │
│       │                    │                                                       │
│       ▼                    ▼                                                       │
│  Slack / Telegram /   archive / state.db /                                    │
│  Calendar / Status    web UI                                                    │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Open questions

- n8n on the homelab machine (needs to reach the Spark at 127.0.0.1)
- API surface: thin trigger endpoints needed (`POST /api/jobs/trigger-backup`, etc.)
- What happens when n8n is down? Scheduled tasks don't fire. Worker still processes queued jobs.
- Tradeoff: app less standalone, but more composable

