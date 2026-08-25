import { goto } from '$app/navigation';
import { apiUrl } from '$lib/backend';
import { playlistPickerStore, playlistEditStore, showConfirm, playerState, radioStarting } from '$lib/store';
import {
    IconPlayerPlayFilled,
    IconPencilFilled,
    IconPlaylistAdd,
    IconPlaylistFilled,
    IconPlayerSkipForwardFilled,
    IconArrowsShuffle,
    IconArrowForward,
    IconTrashFilled,
    IconPlaylistX,
    IconSortAscending,
    IconSortDescending,
    IconLetterCase,
    IconMicrophoneFilled,
    IconCalendarFilled,
    IconStarFilled,
    IconClockFilled,
    IconMusic,
    IconDiscFilled,
    IconHash,
    IconInfinity,
} from '@tabler/icons-svelte';
import type { SortState } from '$lib/store';

type MenuItem = {
    label?: string;
    icon?: any;
    action?: () => void;
    destructive?: boolean;
    divider?: boolean;
    active?: boolean;
};

type Id = string | number;

const SORT_FIELDS: Record<string, { field: string; label: string; icon: any }[]> = {
    Albums: [
        { field: 'title', label: 'Name', icon: IconLetterCase },
        { field: 'artist', label: 'Artist', icon: IconMicrophoneFilled },
        { field: 'release_year', label: 'Release Year', icon: IconCalendarFilled },
        { field: 'rating', label: 'Rating', icon: IconStarFilled },
        { field: 'track_count', label: 'Track Count', icon: IconMusic },
        { field: 'duration_ms', label: 'Duration', icon: IconClockFilled },
    ],
    Tracks: [
        { field: 'title', label: 'Name', icon: IconLetterCase },
        { field: 'artist', label: 'Artist', icon: IconMicrophoneFilled },
        { field: 'rating', label: 'Rating', icon: IconStarFilled },
        { field: 'duration_ms', label: 'Duration', icon: IconClockFilled },
    ],
    Artists: [
        { field: 'name', label: 'Name', icon: IconLetterCase },
        { field: 'album_count', label: 'Album Count', icon: IconDiscFilled },
        { field: 'duration_ms', label: 'Duration', icon: IconClockFilled },
    ],
    Playlists: [
        { field: 'name', label: 'Name', icon: IconLetterCase },
        { field: 'track_count', label: 'Track Count', icon: IconHash },
        { field: 'duration_ms', label: 'Duration', icon: IconClockFilled },
    ],
};

export function buildSortMenuItems(
    activeTab: string,
    currentSort: SortState,
    onSortChange: (field: string, order: 'asc' | 'desc') => void
): MenuItem[] {
    const fields = SORT_FIELDS[activeTab] ?? [];
    const fieldItems: MenuItem[] = fields.map(({ field, label, icon }) => ({
        label,
        icon,
        active: currentSort.field === field,
        action: () => onSortChange(field, currentSort.order),
    }));
    const orderItems: MenuItem[] = [
        {
            label: 'Ascending',
            icon: IconSortAscending,
            active: currentSort.order === 'asc',
            action: () => onSortChange(currentSort.field, 'asc'),
        },
        {
            label: 'Descending',
            icon: IconSortDescending,
            active: currentSort.order === 'desc',
            action: () => onSortChange(currentSort.field, 'desc'),
        },
    ];
    return [...fieldItems, { divider: true }, ...orderItems];
}

export function dispatch(action: string, value?: unknown): void {
    window.dispatchEvent(
        new CustomEvent<{ action: string; value?: unknown }>('player-command', {
            detail: { action, value },
        })
    );
}

export function removeFromQueue(queueItemId: Id): void {
    // Optimistically drop the item; the backend echoes the authoritative queue.
    playerState.update((s) => ({
        ...s,
        queue: s.queue.filter((item) => item.id !== queueItemId),
    }));
    dispatch('remove_from_queue', queueItemId);
}

export async function getTrackIds(itemId: Id, itemType: string): Promise<string[]> {
    try {
        const res = await fetch(apiUrl(`/api/${itemType}/${itemId}/tracks`));
        if (!res.ok) return [];
        const data = await res.json();
        return Array.isArray(data) ? data.map((t: any) => t.id) : [];
    } catch {
        return [];
    }
}

export async function playTracks(trackIds: Id[], shuffle = false): Promise<void> {
    if (trackIds.length === 0) return;
    await fetch(apiUrl('/api/playback/play'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: trackIds, shuffle }),
    });
}

// Queues trackIds in order (same as playTracks) but starts playback at
// startTrackId instead of the front of the list — for clicking a track
// inside an ordered list (e.g. a home-page "Because you've been into X"
// row) and having the rest of the row queue up after it, the way clicking
// a track partway through an album does.
export async function playTracksFrom(trackIds: Id[], startTrackId: Id): Promise<void> {
    if (trackIds.length === 0) return;
    await fetch(apiUrl('/api/playback/play'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: trackIds, start_track_id: startTrackId }),
    });
}

export async function playAlbum(albumId: Id, shuffle = false): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_album/${albumId}?shuffle=${shuffle}`), { method: 'POST' });
}

export async function playArtist(artistId: Id, shuffle = false): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_artist/${artistId}?shuffle=${shuffle}`), { method: 'POST' });
}

export async function playAlbumAtTrack(albumId: Id, trackId: Id): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_album/${albumId}?track_id=${trackId}`), { method: 'POST' });
}

export async function playTrackById(trackId: Id): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_track/${trackId}`), { method: 'POST' });
}

export async function playItem(itemId: Id, itemType: string, shuffle = false): Promise<void> {
    await playTracks(await getTrackIds(itemId, itemType), shuffle);
}

// Replaces the queue with a fresh algorithmic mix starting from a track, album,
// artist, or playlist (the backend picks representative seed tracks for anything
// but a single track — see radio.pick_seed_tracks). Distinct from
// setRadioEnabled, which toggles the same auto-generation for what's queued.
export async function startRadio(id: Id, type: 'track' | 'album' | 'artist' | 'playlist'): Promise<void> {
    // A track radio plays immediately and fills the mix in the background; the
    // other kinds build their first batch on this request, which can include
    // analyzing seed audio that hasn't been analyzed yet.
    if (type === 'track') {
        await fetch(apiUrl(`/api/playback/start_radio/track/${id}`), { method: 'POST' });
        return;
    }
    radioStarting.set(true);
    try {
        await fetch(apiUrl(`/api/playback/start_radio/${type}/${id}`), { method: 'POST' });
    } finally {
        radioStarting.set(false);
    }
}

export function setRadioEnabled(enabled: boolean): void {
    // Optimistic: flip the toggle immediately, and when disabling, drop
    // not-yet-played mix tracks (queue_type=2) from the local queue right
    // away too — the backend does the same removal, but that confirmation
    // can take a moment (enabling re-runs the whole recommendation engine
    // synchronously). The authoritative queue/flag still arrives over the
    // socket and overwrites this either way.
    playerState.update((s) => ({
        ...s,
        radio_enabled: enabled,
        queue: enabled ? s.queue : s.queue.filter((item) => item.queue_type !== 2),
    }));
    dispatch('set_radio_enabled', enabled);
}

export async function addToQueue(trackIds: Id | Id[]): Promise<void> {
    await fetch(apiUrl('/api/playback/add_to_queue'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: trackIds }),
    });
}

export async function playNext(trackIds: Id | Id[], top = true): Promise<void> {
    await fetch(apiUrl('/api/playback/play_next'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: trackIds, top }),
    });
}

export async function playPlaylist(playlistId: Id, shuffle = false): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_playlist/${playlistId}?shuffle=${shuffle}`), { method: 'POST' });
}

export async function playPlaylistAtTrack(playlistId: Id, trackId: Id): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_playlist/${playlistId}?track_id=${trackId}`), { method: 'POST' });
}

function openPlaylistPicker(ids: Id[]): void {
    playlistPickerStore.set({ open: true, trackIds: ids.map(String) });
}

export async function deletePlaylist(playlistId: Id, playlistName: string = ""): Promise<void> {
    const confirmed = await showConfirm({
        title: playlistName ? `Delete Playlist ${playlistName}?` : 'Delete Playlist?',
        message: 'Are you sure you want to delete this playlist? This action cannot be undone.',
        confirmLabel: 'Delete',
        destructive: true,
    });
    if (!confirmed) return;

    await fetch(apiUrl(`/api/playlist/${playlistId}`), { method: 'DELETE' });
    goto('/library');
}

// Every media context menu shares the same queueing actions; only how the
// track IDs are obtained differs (a literal list vs a fetch per item type).
type IdsSource = Id[] | (() => Promise<Id[]>);

function queueingMenuItems(source: IdsSource, includePlaylist = true): MenuItem[] {
    const run = (fn: (ids: Id[]) => void) => async () => {
        const ids = typeof source === 'function' ? await source() : source;
        if (ids.length) fn(ids);
    };
    const items: MenuItem[] = [
        { label: 'Play Next', icon: IconPlayerSkipForwardFilled, action: run((ids) => playNext(ids, true)) },
        { label: 'Add to Up Next', icon: IconArrowForward, action: run((ids) => playNext(ids, false)) },
        { label: 'Add to Queue', icon: IconPlaylistAdd, action: run((ids) => addToQueue(ids)) },
    ];
    if (includePlaylist) {
        items.push({ label: 'Add to Playlist', icon: IconPlaylistFilled, action: run(openPlaylistPicker) });
    }
    return items;
}

export function buildTrackMenuItems(trackId: Id, albumId: string = ""): MenuItem[] {
    return [
        ...(albumId ? [{ label: 'View Album', icon: IconDiscFilled, action: () => goto(`/album/${albumId}?track=${trackId}`) }] : []),
        { label: 'Play', icon: IconPlayerPlayFilled, action: () => playTrackById(trackId) },
        { label: 'Start Radio', icon: IconInfinity, action: () => startRadio(trackId, 'track') },
        ...queueingMenuItems([trackId]),
    ];
}

export function buildPlaylistTrackMenuItems(
    trackId: Id,
    playlistId: Id,
    itemId: Id,
    onRemove?: () => void
): MenuItem[] {
    return [
        ...buildTrackMenuItems(trackId),
        { divider: true },
        {
            label: 'Remove from Playlist',
            icon: IconPlaylistX,
            destructive: true,
            action: async () => {
                await fetch(apiUrl(`/api/playlist/${playlistId}/tracks`), {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ item_ids: [itemId] }),
                });
                onRemove?.();
            },
        },
    ];
}

export function buildQueueItemMenuItems(queueItemId: Id): MenuItem[] {
    return [
        { label: 'Play Next', icon: IconPlayerSkipForwardFilled, action: () => dispatch('move_queue_item', { id: queueItemId, position: 'next' }) },
        { label: 'Add to Up Next', icon: IconArrowForward, action: () => dispatch('move_queue_item', { id: queueItemId, position: 'end' }) },
        { label: 'Remove from Queue', icon: IconTrashFilled, destructive: true, action: () => removeFromQueue(queueItemId) },
    ];
}

export function buildAlbumMenuItems(albumId: Id): MenuItem[] {
    return [
        { label: 'Play', icon: IconPlayerPlayFilled, action: () => playItem(albumId, 'album', false) },
        { label: 'Shuffle', icon: IconArrowsShuffle, action: () => playItem(albumId, 'album', true) },
        { label: 'Start Radio', icon: IconInfinity, action: () => startRadio(albumId, 'album') },
        ...queueingMenuItems(() => getTrackIds(albumId, 'album')),
    ];
}

export function buildArtistMenuItems(artistId: Id): MenuItem[] {
    return [
        { label: 'Play', icon: IconPlayerPlayFilled, action: () => playItem(artistId, 'artist', false) },
        { label: 'Shuffle', icon: IconArrowsShuffle, action: () => playItem(artistId, 'artist', true) },
        { label: 'Start Radio', icon: IconInfinity, action: () => startRadio(artistId, 'artist') },
        ...queueingMenuItems(() => getTrackIds(artistId, 'artist')),
    ];
}

export function buildPlaylistMenuItems(playlistId: Id, playlistName: string = ""): MenuItem[] {
    return [
        { label: 'Play', icon: IconPlayerPlayFilled, action: () => playPlaylist(playlistId, false) },
        { label: 'Shuffle', icon: IconArrowsShuffle, action: () => playPlaylist(playlistId, true) },
        { label: 'Start Radio', icon: IconInfinity, action: () => startRadio(playlistId, 'playlist') },
        ...queueingMenuItems(() => getTrackIds(playlistId, 'playlist'), false),
        { divider: true },
        {
            label: 'Edit Playlist', icon: IconPencilFilled, action: async () => {
                const res = await fetch(apiUrl(`/api/playlist/${playlistId}`));
                if (res.ok) {
                    const data = await res.json();
                    playlistEditStore.set({ open: true, playlist: data.playlist });
                }
            }
        },
        { label: 'Delete Playlist', icon: IconTrashFilled, destructive: true, action: () => deletePlaylist(playlistId, playlistName) },
    ];
}

/**
 * Like buildCollectionMenuItems, but Play/Shuffle can draw from different
 * track lists than the queueing actions (Play Next/Add to Queue/Add to
 * Playlist) — e.g. a genre page where "Play" walks its albums in order,
 * "Shuffle" draws from every tagged track, and queueing uses the same
 * ordered list as Play.
 *
 * `radio`, when given, inserts a "Start Radio" item that seeds from the
 * collection's own id (album/artist/playlist) rather than from playIds --
 * detail pages already have their track list loaded for Play/Shuffle, but
 * radio needs the entity id itself so the backend can sample its own seed
 * tracks (see radio.pick_seed_tracks) instead of trusting a single track.
 */
export function buildCollectionMenuItemsWithSources(
    playIds: Id[],
    shuffleIds: Id[],
    queueIds: Id[] = playIds,
    radio?: { id: Id; type: 'album' | 'artist' | 'playlist' },
): MenuItem[] {
    return [
        { label: 'Play', icon: IconPlayerPlayFilled, action: () => playTracks(playIds, false) },
        { label: 'Shuffle', icon: IconArrowsShuffle, action: () => playTracks(shuffleIds, true) },
        ...(radio ? [{ label: 'Start Radio', icon: IconInfinity, action: () => startRadio(radio.id, radio.type) }] : []),
        ...queueingMenuItems(queueIds),
    ];
}

export function buildCollectionMenuItems(
    trackIds: Id[],
    radio?: { id: Id; type: 'album' | 'artist' | 'playlist' },
): MenuItem[] {
    return buildCollectionMenuItemsWithSources(trackIds, trackIds, trackIds, radio);
}

export function buildItemMenuItems(id: Id, type: 'album' | 'playlist' | 'artist' | 'track', albumId?: string): MenuItem[] {
    if (type === 'album') return buildAlbumMenuItems(id);
    if (type === 'playlist') return buildPlaylistMenuItems(id);
    if (type === 'track') return buildTrackMenuItems(id, albumId);
    if (type === 'artist') return buildArtistMenuItems(id);
    return [];
}
