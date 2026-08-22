<img src="docs/banner.png" alt="Finload logo, an outline of a shark fin with a waveform in the background." height="80">

[![CI](https://github.com/asavage7/finload/actions/workflows/ci.yml/badge.svg)](https://github.com/asavage7/finload/actions/workflows/ci.yml)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)

Finload is a sleek and easy to use Jellyfin music player, with the goal to provide a more Spotify-like experience with your personal music library.

**Finload is nearing a beta-ready state with most major features working as intended.**

## AI Usage

**The UI and logo of Finload are not, and will never be, AI generated.**

However, parts of Finload were created with the help of artificial intelligence. While care is taken to ensure code remains well-tested, documented, and free of infringing works, these items cannot be guaranteed.


## Screenshots

![Library grid view showing album artwork](docs/app1.png)


![Album detail page with the queue panel open](docs/app2.png)

![Home page showing recommendation rows and the lyrics panel](docs/app3.png)

## Features

- Onboarding Sequence
- Sync library from Jellyfin or local files
- Homepage with auto-generated recommendations based on listening history
- Library view (grid and list)
- Detail pages for albums, artists, playlists, and genres
- Playlists
- Gapless playback
- Queue, History, and Lyrics Panel
- Fullscreen "Now Playing" page
- Ratings for tracks and albums
- Autoplay for current queues
- Start radio from any Album, Artist, Track, or Playlist

**This is not an exhaustive list.**

### Known Issues

- Switching between Jellyfin and Local files requires a restart to clear database issues
- Discovery algorithm needs tweaked, especially for isolated genres
- Library tabs sometimes need to be scrolled for content to appear
- Volume UI needs tweaked
- Autoplay toggle does not persist on queue clear
- Volume normalization does not adjust volume of non-normalized tracks, causing them to be much louder
- WebKitGTK has different blur rendering than Firefox/Chromium, causing odd blur behaviors. 
- Some settings toggles are not fully functional.

## Install

Download finload from the [releases page](https://github.com/asavage7/finload/releases). Linux (x64) is the only supported platform at the moment, however Windows and Linux ARM support are planned.

**Debian / Ubuntu**:

```bash
sudo apt install ./finload_<version>_amd64.deb
```

**Fedora / openSUSE:**

```bash
sudo dnf install ./finload-<version>-1.x86_64.rpm
```

**Appimage:**

```bash
chmod +x finload_<version>_amd64.AppImage
./finload_<version>_amd64.AppImage
```

## Development

### Prerequisites

- Python 3.11+
- Node.js 20+ and npm 10+
- Rust 1.77+ if using Tauri
- libmpv (`libmpv2` on Debian/Ubuntu, `mpv` on Arch)

### Setup

Run the setup script for your platform. This installs system dependencies, creates the Python venv, and runs `npm install`:

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
