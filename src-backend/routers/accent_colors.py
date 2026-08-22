"""Accent color extraction from cover art.

Derives three colors from an item's artwork: an accent that white text can sit
on, a light variant for colored text on dark backgrounds, and a dark variant
used as the base background.
"""
import colorsys
import hashlib
import logging
import os

from fastapi import APIRouter
from PIL import Image

from core.database import PlaylistTrack, Track
from routers.images import playlist_image_path, resolve_image_path

logger = logging.getLogger(__name__)

router = APIRouter()

_accent_color_cache: dict[str, list] = {}

_ACCENT_CHROMA_EXP = 1.5
_ACCENT_CONTRAST_EXP = 2.0
_ACCENT_MIN_STANDOUT = 3.0
_ACCENT_DARK_FLOOR_L = 0.15
_SECONDARY_MIN_HUE_DIST = 10  # degrees from the accent hue to count as a distinct color
_SECONDARY_MIN_CHROMA = 0.12  # must be a real color, not a near-grey
_SECONDARY_MIN_SHARE = 0.02   # min fraction of pixels to be "prevalent"
_LIGHT_TARGET_L = 0.80        # lightness the secondary color is raised to for text use
_TEXT_CONTRAST = 4.5          # WCAG AA: white-on-accent and light-on-dark

# Extraction reads a small thumbnail; this matches the standard cover size so
# the image is usually already cached.
_SOURCE_IMAGE_SIZE = 240


def invalidate(item_id: str):
    """Drop the cached colors for an item (call when its image changes)."""
    _accent_color_cache.pop(item_id, None)
    _accent_color_cache.pop(playlist_image_path(item_id), None)


def _to_hls(rgb):
    """RGB 0-255 -> (hue, lightness, saturation), each 0..1."""
    return colorsys.rgb_to_hls(*[c / 255 for c in rgb])


def _to_rgb(h, l, s):
    """(hue, lightness, saturation) 0..1 -> clamped RGB ints 0-255."""
    return [max(0, min(255, round(c * 255))) for c in colorsys.hls_to_rgb(h, l, s)]


def rgb_to_hex(rgb):
    return '#{:02x}{:02x}{:02x}'.format(*rgb)


def _luminance(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _chroma(l, s):
    """Colorfulness: 0 at pure black/white, peaks at mid lightness."""
    return s * (1 - abs(2 * l - 1))


def _hue_dist(h1, h2):
    """Shortest distance between two hues, in degrees (0..180)."""
    d = abs(h1 - h2) * 360
    return min(d, 360 - d)


def _extract_palette(path, size=240, colors=16):
    """Return [(rgb, share)] ordered by pixel prevalence, where share is the
    fraction of pixels that color represents."""
    im = Image.open(path)
    im.draft("RGB", (size, size))   # fast JPEG downscale during decode
    im = im.convert("RGB")
    im.thumbnail((size, size))
    quant = im.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
    pal = quant.getpalette()
    total = im.width * im.height
    counts = quant.getcolors(maxcolors=colors) or []
    return [((pal[idx * 3], pal[idx * 3 + 1], pal[idx * 3 + 2]), n / total)
            for n, idx in sorted(counts, reverse=True)]


def get_accent_colors(item_id: str, type: str = "album", image_path: str | None = None):
    if item_id in _accent_color_cache:
        return _accent_color_cache[item_id]

    cache_path = image_path or resolve_image_path(item_id, _SOURCE_IMAGE_SIZE, type)
    if not cache_path or not os.path.exists(cache_path):
        return {"error": "Image not found"}

    # Tracks normally resolve to their album's cover, so a dozen+ tracks on the
    # same album all land on this same file. Key the cache by that resolved
    # path too, so only the first of them pays for palette extraction and the
    # rest are a plain dict hit.
    if cache_path in _accent_color_cache:
        result = _accent_color_cache[cache_path]
        _accent_color_cache[item_id] = result
        return result

    try:
        palette = _extract_palette(cache_path)

        def accent_fitness(rgb, share):
            _, l, s = _to_hls(rgb)
            white_fit = min(_contrast((255, 255, 255), rgb) / _TEXT_CONTRAST, 1.0)
            dark_fade = min(1.0, l / _ACCENT_DARK_FLOOR_L)   # near-black reads as black, not hue
            return (_chroma(l, s) ** _ACCENT_CHROMA_EXP) * share * (white_fit ** _ACCENT_CONTRAST_EXP) * dark_fade

        best_rgb = max(palette, key=lambda entry: accent_fitness(*entry))[0]
        ah, al, as_ = _to_hls(best_rgb)

        dark_primary = _to_rgb(ah, 0.12, min(as_, 0.5))

        while _contrast((255, 255, 255), _to_rgb(ah, al, as_)) < _TEXT_CONTRAST and al > 0.05:
            al -= 0.02
        while (_contrast(_to_rgb(ah, al, as_), dark_primary) < _ACCENT_MIN_STANDOUT
               and _contrast((255, 255, 255), _to_rgb(ah, al + 0.02, as_)) >= _TEXT_CONTRAST
               and al < 0.95):
            al += 0.02
        accent = _to_rgb(ah, al, as_)

        secondary, best_secondary = None, 0.0
        for rgb, share in palette:
            h, l, s = _to_hls(rgb)
            if share < _SECONDARY_MIN_SHARE or _chroma(l, s) < _SECONDARY_MIN_CHROMA:
                continue
            if _hue_dist(h, ah) < _SECONDARY_MIN_HUE_DIST:
                continue
            score = share * (0.2 + _chroma(l, s))       # prevalence weighted by vividness
            if score > best_secondary:
                best_secondary, secondary = score, rgb

        if secondary is not None:
            sh, sl, ss = _to_hls(secondary)
            light_primary = _to_rgb(sh, max(sl, _LIGHT_TARGET_L), ss)
        else:
            light_primary = _to_rgb(ah, 0.85, min(as_, 0.45))

        lh, ll, ls = _to_hls(light_primary)
        while _contrast(light_primary, dark_primary) < _TEXT_CONTRAST and ll < 0.96:
            ll += 0.02
            light_primary = _to_rgb(lh, ll, ls)

        result = [rgb_to_hex(accent), rgb_to_hex(light_primary), rgb_to_hex(dark_primary)]
        _accent_color_cache[cache_path] = result
        _accent_color_cache[item_id] = result
        return result

    except Exception as e:
        logger.warning("Color extraction skipped: %s", e)
        return {"error": str(e)}


# Saturation band for hash-derived (non-image) accents — real cover-art
# extraction varies in saturation too, so this is a range, not one fixed
# value: with ~100+ genres sharing only 360 possible hues, birthday-paradox
# math guarantees some near-duplicate hues (e.g. "Punk" and "Punk Rock"
# landed 2 degrees apart) — varying saturation independently means two
# genres have to collide on *both* to actually look alike, which is far less
# likely than colliding on hue alone.
_SYNTHETIC_SATURATION_RANGE = (0.45, 0.70)


def _hash_unit_values(seed: str) -> tuple[float, float]:
    """Two independent 0..1 values from one hash — used for hue and a
    saturation offset, deliberately uncorrelated with each other."""
    digest = hashlib.sha1(seed.encode("utf-8")).digest()
    a = int.from_bytes(digest[0:4], "big") / 0xFFFFFFFF
    b = int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF
    return a, b


def get_synthetic_accent_colors(seed: str) -> list[str]:
    """Deterministic [accent, light, dark] triple from a seed string (e.g. a
    genre id) — no image involved. Mirrors the accent/dark-primary contrast
    fitting in ``get_accent_colors`` (same target contrast ratios, same
    lightness floor/ceiling) and its no-secondary-color fallback for the
    light variant, so a genre's colors read as part of the same visual family
    as real cover-derived accents rather than a separately-tuned palette.
    """
    hue_frac, sat_frac = _hash_unit_values(seed)
    ah = hue_frac
    sat_lo, sat_hi = _SYNTHETIC_SATURATION_RANGE
    as_ = sat_lo + sat_frac * (sat_hi - sat_lo)
    al = 0.5

    dark_primary = _to_rgb(ah, 0.12, min(as_, 0.5))

    while _contrast((255, 255, 255), _to_rgb(ah, al, as_)) < _TEXT_CONTRAST and al > 0.05:
        al -= 0.02
    while (_contrast(_to_rgb(ah, al, as_), dark_primary) < _ACCENT_MIN_STANDOUT
           and _contrast((255, 255, 255), _to_rgb(ah, al + 0.02, as_)) >= _TEXT_CONTRAST
           and al < 0.95):
        al += 0.02
    accent = _to_rgb(ah, al, as_)

    light_primary = _to_rgb(ah, 0.85, min(as_, 0.45))
    lh, ll, ls = _to_hls(light_primary)
    while _contrast(light_primary, dark_primary) < _TEXT_CONTRAST and ll < 0.96:
        ll += 0.02
        light_primary = _to_rgb(lh, ll, ls)

    return [rgb_to_hex(accent), rgb_to_hex(light_primary), rgb_to_hex(dark_primary)]


@router.get("/api/genre/{genre_id}/accent-colors")
def get_genre_accent_colors(genre_id: str):
    return get_synthetic_accent_colors(genre_id)


@router.get("/api/album/{album_id}/accent-colors")
def get_album_accent_colors(album_id: str):
    return get_accent_colors(album_id, type="album")


@router.get("/api/track/{track_id}/accent-colors")
def get_track_accent_colors(track_id: str):
    return get_accent_colors(track_id, type="track")


@router.get("/api/artist/{artist_id}/accent-colors")
def get_artist_accent_colors(artist_id: str):
    return get_accent_colors(artist_id, type="artist")


@router.get("/api/playlist/{playlist_id}/accent-colors")
def get_playlist_accent_colors(playlist_id: str):
    custom_image = playlist_image_path(playlist_id)
    if os.path.exists(custom_image):
        return get_accent_colors(playlist_id, image_path=custom_image)
    first = (PlaylistTrack.select(PlaylistTrack, Track)
             .join(Track)
             .where(PlaylistTrack.playlist == playlist_id)
             .order_by(PlaylistTrack.position)
             .first())
    if first:
        return get_accent_colors(str(first.track.album_id))
    return {"error": "No image available"}
