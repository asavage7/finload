import { apiUrl } from '$lib/backend';

export type SearchResult = {
    type: 'artist' | 'album' | 'track';
    id: string;
    title: string;
    subtitle: string;
    image_id: string;
    album_id: string | null;
};

// Hits the ranked /api/search endpoint. Pass an AbortSignal so superseded
// keystrokes can cancel their in-flight request.
export async function searchLibrary(
    q: string,
    signal?: AbortSignal,
    limit = 5,
): Promise<SearchResult[]> {
    const res = await fetch(
        apiUrl(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`),
        { signal },
    );
    if (!res.ok) throw new Error(`Search failed: ${res.status}`);
    const data = await res.json();
    return data.results ?? [];
}
