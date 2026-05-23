# Finload

A sleek and easy to use Jellyfin music player, with the goal to provide a more Spotify-like experience with your personal music library.

**Finload is in very early development and is NOT feature-complete or ready for production use. While parts of the interface and music player do work properly, there are still major gaps in functionality. See the features and known issues section for more details.**

**Only Jellyfin is supported as a music provider at the moment, however local folders and other media servers will be added in the future.**

## Features

- Library views for albums and artists
- Detail page for albums
- Play albums/tracks
- Gapless playback
- Queue panel with proper track switching and removal

### To Be Implemented:

- Playlists
- Tracks/Playlist library views
- Better sorting/view modes for library pages
- Queue reordering
- Shuffle/repeat playback modes
- Adding tracks/albums to queue
- Proper Jellyfin sync
- Virtual scroll for library page
- History/Lyrics pages in queue panel
- Search
- Ratings system (implemented in UI but not functional)
- Implement other media sources

**This is not an exhaustive list!**

## Known issues

- Queue panel only opens on larger screens; this is intentional but needs changed
- Clearing the whole queue is not implemented
- Album art can disappear on some albums when switching between albums/artist views
- MPV player state can sometimes desync from frontend
- Hitbox for seeking is slightly higher than intended

## Prerequisites

Install these outside the app itself before running Finload:

- Python 3.11+ for the backend tooling and local sync scripts
- Node.js 20+ and npm 10+ for the frontend and shared dev scripts
- Rust 1.77+ (stable toolchain) for the Tauri desktop shell

If you are on Linux and building the desktop app, you may also need the standard system packages required by Tauri for your distribution.

## Running the app

1) Create a local `.env` from `.env.example` and fill in your Jellyfin credentials (do NOT commit the real `.env`):

```bash
cp .env.example .env
# edit .env and set JELLYFIN_URL, JELLYFIN_API_KEY, JELLYFIN_USER_ID, and any other optional variables.
```

2) Install Python backend dependencies and frontend dependencies:

```bash
python -m pip install -r src-backend/requirements.txt
npm install
```

3) Run both dev servers together (cross-platform):

```bash
npm run dev:all
# Don't panic if the app doesn't load immediately! It takes ~30s on first launch to load.
```

Notes:
- The backend will attempt to auto-load the repository `.env` file (project root `.env`) using `python-dotenv`. If you prefer another mechanism, set environment variables in your shell before starting `uvicorn`.
- Set `INITIAL_FULL_SYNC=true` to import the full Jellyfin library on launch. Leave it `false` for normal startup.

```bash
# backend (from repo root)
uvicorn main:app --reload --app-dir src-backend --host 127.0.0.1 --port 8000

# frontend (from finload-new)
npm run dev
```

