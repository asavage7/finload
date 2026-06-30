import { apiUrl } from '$lib/backend';

export async function fetchRating(
    type: 'album' | 'track',
    id: string | number,
): Promise<number> {
    const res = await fetch(apiUrl(`/api/${type}/${id}/rating`));
    if (!res.ok) return 0;
    const data = await res.json();
    return data.rating ?? 0;
}

export async function updateRating(
    type: 'album' | 'track',
    id: string | number,
    rating: number
): Promise<void> {
    await fetch(apiUrl(`/api/${type}/${id}/rating`), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating }),
    });
}
