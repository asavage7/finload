import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { get } from 'svelte/store';
import { playerState, type PlayerState } from '$lib/store';
import { onPlayerStatePatch } from '$lib/utils/store';
import { dispatch } from '$lib/utils/playback';
import { getAbsoluteImageUrl } from '$lib/utils/media';

type MediaControlEventPayload =
    | { type: 'play' }
    | { type: 'pause' }
    | { type: 'toggle' }
    | { type: 'next' }
    | { type: 'previous' }
    | { type: 'stop' }
    | { type: 'seek'; direction: 'forward' | 'backward' }
    | { type: 'seek_by'; direction: 'forward' | 'backward'; secs: number }
    | { type: 'set_position'; secs: number }
    | { type: 'set_volume'; value: number }
    | { type: 'open_uri'; uri: string }
    | { type: 'quit' };

function clamp(value: number, min: number, max: number): number {
    return Math.min(Math.max(value, min), max);
}

function pushMetadata(s: PlayerState): void {
    invoke('update_now_playing_metadata', {
        title: s.current_track?.title ?? null,
        artist: s.current_track?.artist_name ?? null,
        album: s.current_track?.album_name ?? null,
        durationSecs: s.duration || null,
        coverUrl: s.current_track?.album_id ? getAbsoluteImageUrl(s.current_track.album_id, 512) : null,
    });
}

function pushStatus(s: PlayerState): void {
    invoke('update_playback_status', { isPaused: s.is_paused, positionSecs: s.time_pos });
}

const DEFAULT_SEEK_SECS = 10;

function handleMediaControlEvent(event: MediaControlEventPayload): void {
    const s = get(playerState);
    switch (event.type) {
        case 'play':
            if (s.is_paused) dispatch('toggle_pause');
            break;
        case 'pause':
        case 'stop':
            if (!s.is_paused) dispatch('toggle_pause');
            break;
        case 'toggle':
            dispatch('toggle_pause');
            break;
        case 'next':
            dispatch('skip_next');
            break;
        case 'previous':
            dispatch('skip_prev');
            break;
        case 'seek': {
            const delta = event.direction === 'forward' ? DEFAULT_SEEK_SECS : -DEFAULT_SEEK_SECS;
            dispatch('seek', clamp(s.time_pos + delta, 0, s.duration));
            break;
        }
        case 'seek_by': {
            const delta = event.direction === 'forward' ? event.secs : -event.secs;
            dispatch('seek', clamp(s.time_pos + delta, 0, s.duration));
            break;
        }
        case 'set_position':
            dispatch('seek', clamp(event.secs, 0, s.duration));
            break;
        case 'set_volume':
            dispatch('set_volume', Math.round(event.value * 100));
            break;
        case 'open_uri':
        case 'quit':
            break;
    }
}

// Bridges Finload's playback state to the OS media session (MPRIS/SMTC/Now
// Playing) via the Rust/souvlaki layer. Returns a cleanup function.
export function initMediaSession(): () => void {
    if (!isTauri()) return () => {};

    const unsubscribe = onPlayerStatePatch((patch) => {
        const s = get(playerState); // updatePlayerState() has already merged `patch` by the time listeners run
        if ('current_track' in patch || 'duration' in patch) pushMetadata(s);
        if ('is_paused' in patch || 'time_pos' in patch) pushStatus(s);
    });

    const unlistenPromise = listen<MediaControlEventPayload>('media-control', (event) => {
        handleMediaControlEvent(event.payload);
    });

    return () => {
        unsubscribe();
        unlistenPromise.then((fn) => fn());
    };
}
