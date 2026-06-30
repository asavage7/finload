# Finload

A sleek and easy to use Jellyfin music player, with the goal to provide a more Spotify-like experience with your personal music library.

**Finload is in early development and is NOT feature-complete or ready for production use. While the core music player and library work, there are still gaps in functionality. See the features and known issues sections for details.**

**Only Jellyfin is supported as a music provider at the moment, however local folders and other media servers will be added in the future.**

## Screenshot
![App Screenshot](https://github.com/asavage7/finload/blob/72c19fe26f09bcc2003b10618a6e30fb431f0131/static/app1.png)

## Features

- Sync library from Jellyfin or local files
- Library view (grid and list)
- Detail pages for albums and artists
- Playback of albums, artists, playlists, and individual tracks
- Gapless playback
- Queue management (add, reorder, remove, clear)
- Queue and lyrics panel
- Fullscreen "Now Playing" page
- Playlists (create, edit, delete)
- Ratings for tracks and albums

### To Be Implemented

- Home Tab
- History tab in queue panel
- Search
- Proper Onboarding
- Smaller memory/disk footprint

**This is not an exhaustive list.**

### Known Issues

- Hitbox for seeking is misaligned
- Track length incorrect for certain tracks on local files

## Prerequisites

Install these before running Finload:

- Python 3.11+
- Node.js 20+ and npm 10+
- Rust 1.77+ if using Tauri
- libmpv (`libmpv2` on Debian/Ubuntu, `mpv` on Arch)

## Setup

Run the setup script for your platform. This installs system dependencies, creates the Python venv, and runs `npm install`:

```bash
# Linux / macOS
bash scripts/setup-dev.sh

# Windows (PowerShell)
.\scripts\setup-dev.ps1
```

## Running the app

Start the frontend and backend together:

```bash
npm run dev
```

For the full desktop shell (Tauri):

```bash
npm run dev:tauri
```

Jellyfin credentials (server URL, API key, user ID) are configured inside the app under Settings. Use the button on the "Now Playing" bar to access the settings page. A proper onboarding setup is under development.

If you need to override backend server or data directory settings, copy `.env.example` to `.env` and adjust:

```bash
# This will be removed in a future commit
cp .env.example .env
```

## Building

```bash
npm run build:tauri

# Completed builds can be found in <Repo Directory>/src-tauri/target/release/bundle/
```
