import { apiUrl, absoluteApiUrl } from '$lib/backend';

export function getImageUrl(id: string | number, size: number, type?: string): string {
    return apiUrl(`/api/image/${id}?size=${size}${type ? `&type=${type}` : ''}`);
}

// Same as getImageUrl, but always fully-qualified. Use this for URLs handed to
// native OS code (e.g. OS media-session artwork) instead of <img> tags.
export function getAbsoluteImageUrl(id: string | number, size: number, type?: string): string {
    return absoluteApiUrl(`/api/image/${id}?size=${size}${type ? `&type=${type}` : ''}`);
}

export function getItemHref(
    type: 'album' | 'artist' | 'playlist' | 'track',
    id: string | number,
    albumId?: string | number
): string {
    // Tracks live on their album page; the `track` query tells the album page to
    // scroll to and briefly highlight that track.
    if (type === 'track') return `/album/${albumId ?? id}?track=${id}`;
    return `/${type}/${id}`;
}

export async function fetchAccentColors(
    type: 'album' | 'artist' | 'playlist' | 'track' | 'genre',
    id: string | number
): Promise<string[]> {
    try {
        const res = await fetch(apiUrl(`/api/${type}/${id}/accent-colors`));
        if (!res.ok) return [];
        const colors = await res.json();
        return Array.isArray(colors) ? colors : [];
    } catch {
        return [];
    }
}
