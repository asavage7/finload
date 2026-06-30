import { goto } from '$app/navigation';
import { apiUrl } from '$lib/backend';
import { playlistPickerStore, playlistEditStore, showConfirm, playerState } from '$lib/store';
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
    IconDisc,
    IconHash,
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
        { field: 'album_count', label: 'Album Count', icon: IconDisc },
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

export function removeFromQueue(queueItemId: string | number): void {
    // Optimistically drop the item; the backend echoes the authoritative queue.
    playerState.update((s) => ({
        ...s,
        queue: s.queue.filter((item) => item.id !== queueItemId),
    }));
    dispatch('remove_from_queue', queueItemId);
}

export async function getTrackIds(itemId: string | number, itemType: string): Promise<string[]> {
    try {
        const res = await fetch(apiUrl(`/api/${itemType}/${itemId}/tracks`));
        if (!res.ok) return [];
        const data = await res.json();
        return Array.isArray(data) ? data.map((t: any) => t.id) : [];
    } catch {
        return [];
    }
}

export async function playAllTracks(trackIds: (string | number)[], shuffle = false): Promise<void> {
    if (trackIds.length === 0) return;
    await fetch(apiUrl('/api/playback/play'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: trackIds, shuffle }),
    });
}

export async function playAlbum(albumId: string | number, shuffle = false): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_album/${albumId}?shuffle=${shuffle}`), { method: 'POST' });
}

export async function playArtist(artistId: string | number, shuffle = false): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_artist/${artistId}?shuffle=${shuffle}`), { method: 'POST' });
}

export async function playAlbumAtTrack(albumId: string | number, trackId: string | number): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_album/${albumId}?track_id=${trackId}`), { method: 'POST' });
}

export async function playTrackById(trackId: string | number): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_track/${trackId}`), { method: 'POST' });
}

export async function playItem(itemId: string | number, itemType: string, shuffle = false): Promise<void> {
    const trackIds = await getTrackIds(itemId, itemType);
    if (trackIds.length === 0) return;
    await fetch(apiUrl('/api/playback/play'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: trackIds, shuffle }),
    });
}

export async function playTracks(trackIds: (string | number)[], shuffle = false): Promise<void> {
    if (trackIds.length === 0) return;
    await fetch(apiUrl('/api/playback/play'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: trackIds, shuffle }),
    });
}

export async function addToQueue(trackIds: string | number | (string | number)[]): Promise<void> {
    await fetch(apiUrl('/api/playback/add_to_queue'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: trackIds }),
    });
}

export async function playNext(
    trackIds: string | number | (string | number)[],
    top = true
): Promise<void> {
    await fetch(apiUrl('/api/playback/play_next'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: trackIds, top }),
    });
}

export async function playPlaylist(playlistId: string | number, shuffle = false): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_playlist/${playlistId}?shuffle=${shuffle}`), { method: 'POST' });
}

export async function playPlaylistAtTrack(playlistId: string | number, trackId: string | number): Promise<void> {
    await fetch(apiUrl(`/api/playback/play_playlist/${playlistId}?track_id=${trackId}`), { method: 'POST' });
}

function openPlaylistPicker(ids: (string | number)[]): void {
    playlistPickerStore.set({ open: true, trackIds: ids.map(String) });
}

export async function deletePlaylist(playlistId: string | number, playlistName: string = ""): Promise<void> {
    const confirmed = await showConfirm({
        title: playlistName ? `Delete Playlist ${playlistName}?` : 'Delete Playlist?',
        message: 'Are you sure you want to delete this playlist? This action cannot be undone.',
        confirmLabel: 'Delete',
        destructive: true,
    });
    if (!confirmed) return;

    await fetch(apiUrl(`/api/playlist/${playlistId}`), { method: 'DELETE' });
    goto('/');
}

export function buildTrackMenuItems(trackId: string | number): MenuItem[] {
    return [
        { label: 'Play', icon: IconPlayerPlayFilled, action: () => playTrackById(trackId) },
        { label: 'Play Next', icon: IconPlayerSkipForwardFilled, action: () => playNext([trackId], true) },
        { label: 'Add to Up Next', icon: IconArrowForward, action: () => playNext([trackId], false) },
        { label: 'Add to Queue', icon: IconPlaylistAdd, action: () => addToQueue([trackId]) },
        { label: 'Add to Playlist', icon: IconPlaylistFilled, action: () => openPlaylistPicker([trackId]) },
    ];
}

export function buildPlaylistTrackMenuItems(
    trackId: string | number,
    playlistId: string | number,
    itemId: string | number,
    onRemove?: () => void
): MenuItem[] {
    return [
        { label: 'Play', icon: IconPlayerPlayFilled, action: () => playTrackById(trackId) },
        { label: 'Play Next', icon: IconPlayerSkipForwardFilled, action: () => playNext([trackId], true) },
        { label: 'Add to Up Next', icon: IconArrowForward, action: () => playNext([trackId], false) },
        { label: 'Add to Queue', icon: IconPlaylistAdd, action: () => addToQueue([trackId]) },
        { label: 'Add to Playlist', icon: IconPlaylistFilled, action: () => openPlaylistPicker([trackId]) },
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

export function buildQueueItemMenuItems(queueItemId: string | number): MenuItem[] {
    return [
        { label: 'Play Next', icon: IconPlayerSkipForwardFilled, action: () => dispatch('move_queue_item', { id: queueItemId, position: 'next' }) },
        { label: 'Add to Up Next', icon: IconArrowForward, action: () => dispatch('move_queue_item', { id: queueItemId, position: 'end' }) },
        { label: 'Remove from Queue', icon: IconTrashFilled, destructive: true, action: () => removeFromQueue(queueItemId) },
    ];
}

export function buildAlbumMenuItems(albumId: string | number): MenuItem[] {
    return [
        { label: 'Play', icon: IconPlayerPlayFilled, action: () => playItem(albumId, 'album', false) },
        { label: 'Shuffle', icon: IconArrowsShuffle, action: () => playItem(albumId, 'album', true) },
        { label: 'Play Next', icon: IconPlayerSkipForwardFilled, action: () => getTrackIds(albumId, 'album').then(ids => playNext(ids, true)) },
        { label: 'Add to Up Next', icon: IconArrowForward, action: () => getTrackIds(albumId, 'album').then(ids => playNext(ids, false)) },
        { label: 'Add to Queue', icon: IconPlaylistAdd, action: () => getTrackIds(albumId, 'album').then(ids => addToQueue(ids)) },
        { label: 'Add to Playlist', icon: IconPlaylistFilled, action: () => getTrackIds(albumId, 'album').then(ids => openPlaylistPicker(ids)) },
    ];
}

export function buildPlaylistMenuItems(playlistId: string | number, playlistName: string = ""): MenuItem[] {
    return [
        { label: 'Play', icon: IconPlayerPlayFilled, action: () => playPlaylist(playlistId, false) },
        { label: 'Shuffle', icon: IconArrowsShuffle, action: () => playPlaylist(playlistId, true) },
        { label: 'Play Next', icon: IconPlayerSkipForwardFilled, action: () => getTrackIds(playlistId, 'playlist').then(ids => playNext(ids, true)) },
        { label: 'Add to Up Next', icon: IconArrowForward, action: () => getTrackIds(playlistId, 'playlist').then(ids => playNext(ids, false)) },
        { label: 'Add to Queue', icon: IconPlaylistAdd, action: () => getTrackIds(playlistId, 'playlist').then(ids => addToQueue(ids)) },
        { divider: true },
        { label: 'Edit Playlist', icon: IconPencilFilled, action: async () => {
            const res = await fetch(apiUrl(`/api/playlist/${playlistId}`));
            if (res.ok) {
                const data = await res.json();
                playlistEditStore.set({ open: true, playlist: data.playlist });
            }
        }},
        { label: `Delete Playlist`, icon: IconTrashFilled, destructive: true, action: () => deletePlaylist(playlistId, playlistName) },
    ];
}

export function buildCollectionMenuItems(trackIds: (string | number)[]): MenuItem[] {
    return [
        { label: 'Play', icon: IconPlayerPlayFilled, action: () => playTracks(trackIds, false) },
        { label: 'Shuffle', icon: IconArrowsShuffle, action: () => playTracks(trackIds, true) },
        { label: 'Play Next', icon: IconPlayerSkipForwardFilled, action: () => playNext(trackIds, true) },
        { label: 'Add to Up Next', icon: IconArrowForward, action: () => playNext(trackIds, false) },
        { label: 'Add to Queue', icon: IconPlaylistAdd, action: () => addToQueue(trackIds) },
        { label: 'Add to Playlist', icon: IconPlaylistFilled, action: () => openPlaylistPicker(trackIds) },
    ];
}

export function buildItemMenuItems(id: string | number, type: 'album' | 'playlist' | 'artist'): MenuItem[] {
    if (type === 'album') return buildAlbumMenuItems(id);
    if (type === 'playlist') return buildPlaylistMenuItems(id);
    return [];
}