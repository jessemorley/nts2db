# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

SoundDrop syncs a SoundCloud playlist/likes to Dropbox as MP3 files and displays live sync status in a web dashboard. The GitHub Actions workflow runs hourly automatically; the dashboard can also trigger it on demand.

## Running the Sync Locally

```bash
pip install -r requirements.txt

SC_CLIENT_ID=... SC_USER_ID=... DBX_APP_KEY=... DBX_APP_SECRET=... DBX_REFRESH_TOKEN=... \
  SUPABASE_URL=... SUPABASE_SECRET_KEY=... \
  python cloud_sync.py
```

The target playlist is set via `SC_PLAYLIST_URL` env var, falling back to a hardcoded default in `cloud_sync.py:14`.

## Architecture

**`cloud_sync.py`** — the worker (runs in GitHub Actions):
1. Fetches up to 50 tracks from a SoundCloud playlist via `yt-dlp` (flat extract)
2. For each track, checks if the file already exists in Dropbox at `/Music/Sync/<Title - Artist>.mp3`
3. If missing: downloads via yt-dlp → uploads to Dropbox, logging progress every 10% to Supabase
4. After all tracks: updates the `dropbox_inventory` Supabase table with the current Dropbox file list
5. All sync events/state are written to the `sync_history` Supabase table (upserted by `record_id`)

**`index.html`** — the live dashboard (no build step, open directly in browser):
- Pure React (CDN + Babel) + Tailwind + Supabase JS client
- Subscribes to Supabase Realtime on `sync_history` and `dropbox_inventory` tables for live updates
- "Sync Now" button calls the GitHub Actions `workflow_dispatch` API using a GitHub PAT stored in `localStorage`
- Supabase publishable key and URL are hardcoded directly in the file

**`.github/workflows/main.yml`** — runs `cloud_sync.py` on `ubuntu-latest` every hour or on `workflow_dispatch`. All secrets are passed as GitHub Actions secrets. ffmpeg is explicitly installed in the workflow (not present by default on ubuntu-latest).

**`.github/workflows/deploy.yml`** — deploys `index.html` to GitHub Pages, triggered only on changes to `index.html`. Source must be set to "GitHub Actions" in repo Settings → Pages.

## Supabase Tables

- `sync_history` — one row per track per sync run; columns: `id`, `title`, `artist`, `status`, `progress`, `created_at`. Status values: `queued`, `downloading`, `uploading`, `success`, `error`, `idle`.
- `dropbox_inventory` — full snapshot of `/Music/Sync/` Dropbox folder; columns: `id`, `name`. Rebuilt entirely on each sync (delete-all then re-insert up to 100 files).

## Gotchas

- **SoundCloud flat extract omits metadata** — `extract_flat=True` does not return `title` or `uploader` for SoundCloud. A full per-track `yt-dlp.extract_info(url, download=False)` is required to get track names for Dropbox filename matching.
- **Supabase RLS applies per table** — the dashboard uses the anon/publishable key. Each table (`sync_history`, `dropbox_inventory`) needs its own `SELECT` policy with `USING (true)` or reads silently return an empty array.
