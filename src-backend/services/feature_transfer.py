"""Stopgap export/import for sharing computed audio-analysis features.

! Caution: This file is mostly AI Generated.
It will likely be removed soon anyway.

Lets one Finload install's already-computed TrackFeatures (see
audio_analysis.py) be handed to another install as a plain JSON file, so a
fresh install doesn't have to re-run DSP analysis on tracks someone else
already analyzed. Matching is by Jellyfin item id first, falling back to
MusicBrainz recording id (Track.mbid) for tracks synced under a different id
on another server.

Deliberately isolated from the rest of the backend: it only reads/writes the
existing Track/TrackFeatures tables through models already exported by
core.database, and is wired in from exactly two places -- main.py's router
list and routers/feature_transfer.py. Delete this file plus those two lines
to remove the feature entirely.

This is a stopgap. The real fix is server-side sharing (a Jellyfin plugin),
so every install pointed at one server sees the same cache automatically
instead of trading files -- see the ProviderIds/plugin discussion this
replaced.
"""
import datetime
import json

from core.database import Track, TrackFeatures, db
from services.audio_analysis import FEATURE_VERSION, compute_hubness_stats

EXPORT_FORMAT_VERSION = 1


def export_features(path: str) -> int:
    """Write every current-version TrackFeatures row to a JSON file at `path`.
    Returns the number of tracks written."""
    rows = (TrackFeatures
            .select(TrackFeatures.track, TrackFeatures.bpm, TrackFeatures.features, Track.mbid)
            .join(Track)
            .where(TrackFeatures.feature_version == FEATURE_VERSION))

    tracks = []
    for row in rows:
        data = json.loads(row.features)
        tracks.append({
            "id": row.track_id,
            "mbid": row.track.mbid,
            "bpm": row.bpm,
            "mfcc_mean": data["mfcc_mean"],
            "mfcc_std": data["mfcc_std"],
            "contrast_mean": data["contrast_mean"],
        })

    payload = {
        "finload_feature_export": EXPORT_FORMAT_VERSION,
        "feature_version": FEATURE_VERSION,
        "exported_at": datetime.datetime.now().isoformat(),
        "tracks": tracks,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return len(tracks)


def import_features(path: str, db_manager) -> dict:
    """Read a file written by `export_features` and upsert whatever rows match
    a local track, carrying the local FEATURE_VERSION. Hubness stats
    (dist_center/dist_scale) aren't part of the export -- they're relative to
    this library's own pairwise distances -- so they're recomputed locally
    afterward instead.

    Returns {"total", "imported", "version_mismatch"} for the caller to
    display.
    """
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if payload.get("finload_feature_export") != EXPORT_FORMAT_VERSION:
        raise ValueError("Not a Finload feature export file")

    rows = payload.get("tracks", [])
    if payload.get("feature_version") != FEATURE_VERSION:
        return {"total": len(rows), "imported": 0, "version_mismatch": True}

    local_ids = {t.id for t in Track.select(Track.id)}
    by_mbid = {t.mbid: t.id for t in Track.select(Track.id, Track.mbid)
               .where(Track.mbid.is_null(False))}

    now = datetime.datetime.now()
    imported = 0
    with db.atomic():
        for row in rows:
            target_id = row["id"] if row["id"] in local_ids else by_mbid.get(row.get("mbid"))
            if target_id is None:
                continue
            TrackFeatures.insert(
                track=target_id,
                bpm=row["bpm"],
                feature_version=FEATURE_VERSION,
                features=json.dumps({
                    "mfcc_mean": row["mfcc_mean"],
                    "mfcc_std": row["mfcc_std"],
                    "contrast_mean": row["contrast_mean"],
                }),
                analyzed_at=now,
            ).on_conflict_replace().execute()
            imported += 1

    if imported:
        compute_hubness_stats(db_manager)

    return {"total": len(rows), "imported": imported, "version_mismatch": False}
