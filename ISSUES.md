# Known Issues
If an issue is shown with ~~strikethrough~~, the issue is fixed but a release hasn't been published yet.
Please submit issues not on this list to [the issues page](https://github.com/asavage7/finload/issues).

### Library/Browsing
- ~~Sync does a long search before adding any tracks, makes it seem like the app is frozeon on large libraries.~~ (Still an initial waiting period, but now more accurately shows added tracks as they happen.)
- Library tabs sometimes need to be scrolled for content to appear
- Volume UI needs tweaked
- Autoplay toggle does not persist on queue clear

### Playback
- Volume normalization does not adjust volume of non-normalized tracks, causing them to be much louder

### Discovery
- Missing a proper testing suite to determine functionality/regression.

### DB/Backend
- WebKitGTK has different blur rendering than most other renderers, causing odd blur behaviors.
- Some settings toggles are not fully functional.
- Audio analysis through jellyfin is fairly slow due to bandwidth limitations. (Looks like this is unfixable without changes to Jellyfin :/ )