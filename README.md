<img src="docs/banner.png" alt="Compass music banner, consisting of a circular compass with an audio waveform in the background, app name in large text, and below the name a tagline: Re-discover your personal taste." height="83"><br/>

[![CI](https://github.com/asavage7/finload/actions/workflows/ci.yml/badge.svg)](https://github.com/asavage7/finload/actions/workflows/ci.yml)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![GitHub Sponsors](https://img.shields.io/static/v1?label=Sponsor&message=%E2%9D%A4&logo=GitHub&color=%23fe8e86)](https://github.com/sponsors/asavage7)

Compass (previously Finload) is a unique self-hosted music client, with a custom discovery algorithm built to let you re-live your library.

**The app is currently in beta. Give it a shot! If you run into problems, feel free to [open an issue](https://github.com/asavage7/finload/issues).**

Current known issues can be found in [ISSUES.md](ISSUES.md).

## Screenshots

![Library grid view showing album artwork](docs/app1.png)


![Album detail page with the queue panel open](docs/app2.png)

![Home page showing recommendation rows and the lyrics panel](docs/app3.png)

## Why Compass?

Compass is a next-gen music experience built around discovery. Sync your library, and Compass automatically builds a custom experience around your tastes.
- Get custom recommendations based on your listening history.
- Start radio from an Artist/Album and get songs that *feel* similar, not just share the same genre.
- Turn on Autoplay and keep the music going forever. Autoplay queues up 3 songs at a time and reacts to skips, queued songs, and your play history to keep things feeling tailored and fresh.
- Browse your library with a functional but incredibly elegant UI.

### How it Works
Compass analyzes your music directly using DSP to extract acoustic similarites between songs. It combines this with genre tags and your listening history to curate the perfect collection of songs. Compass even takes into account how you interact with songs day-to-day to match your tastes.

**Compass optionally connects to MusicBrainz, Last.fm, and TheAudioDB to better learn your library. Audio analysis is entirely local.**

## Features

- Listen to local audio (MP3, FLAC, M4A, AAC, ALAC, OGG, OPUS, WAV, anything MPV supports)
- Connect to Jellyfin to listen to music not on your device
- Responsive Homepage tailored to your library
- Library view (grid and list)
- Detail pages for albums, artists, playlists, and genres
- Playlists
- Gapless playback
- Transcoding
- Automatic Genre/Metadata Enrichment
- Queue, History, and Lyrics Panel
- Fullscreen "Now Playing" page
- Ratings for tracks and albums
- Autoplay for current queues
- Start radio from any Album, Artist, Track, or Playlist

**This is not an exhaustive list.**

## AI Usage

Agentic AI tools have historically been used during development. I have stopped using these tools entirely, however, AI-generated code still exists in Compass. I have been continually refactoring parts of the app over time to remove AI code, especially where it creates issues or makes maintenance harder. Logos, UI design, etc. were all human-generated from the start, but some frontend functions may still contain AI-generated code.

## Install

Download Compass from the [releases page](https://github.com/asavage7/finload/releases). Linux (x64) and Windows (x64) are supported; Linux ARM support is planned.

**Debian / Ubuntu**:

```bash
sudo apt install ./compass_music-<version>_amd64.deb
```

**Fedora / openSUSE:**

```bash
sudo dnf install ./compass_music-<version>-1.x86_64.rpm
```

**Appimage:**

```bash
chmod +x compass_music-<version>_amd64.AppImage
./compass_music-<version>_amd64.AppImage
```

**Windows:**

Run `compass_music-<version>_x64-setup.exe`. MPV is bundled in, so there's nothing extra to install.

Initial sync, enrichment, and audio processing can take a while on large libraries. The app is still fully functional during this time, but recommendations will improve as more songs are analyzed.

## Development

### Prerequisites

- Python 3.11+
- Node.js 20+ and npm 10+
- Rust 1.77+ if using Tauri
- libmpv (`libmpv2` on Debian/Ubuntu, `mpv` on Arch)

### Setup

Run the setup script for your platform. This installs system dependencies, creates the Python venv, and runs `npm install` (on Windows, it also installs MPV):

```bash
# Linux / macOS
bash scripts/setup-dev.sh

# Windows (PowerShell)
.\scripts\setup-dev.ps1
```

### Running the app

Start the frontend and backend together:

```bash
npm run dev:all
```

For the full desktop shell (Tauri):

```bash
npm run dev:tauri
```

On first launch, the app will take you through an onboarding process. After this, credentials can be changed in Settings, or by restarting the onboarding process.

## Building

```bash
# All three Linux bundles
npm run build:linux

# Or one target at a time
npm run build:tauri:deb
npm run build:tauri:rpm
npm run build:tauri:appimage

# Completed builds can be found at src-tauri/target/release/bundle/
```

## License

[GPL-3.0](LICENSE).
