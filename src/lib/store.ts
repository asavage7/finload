import { writable } from "svelte/store";

export type TrackInfo = {
    id: string | number | null;
    album_id: string | null;
    artist_id: string | null;
    album_name: string;
    title: string;
    artist_name: string;
    rating: number;
};

export type QueueItemState = {
    id: string | number;
    track: {
        id: string | number | null;
        album_id: string | null;
        album_name: string;
        title: string;
        artist_name: string;
        runtime: number;
    };
    queue_type: number;
    position: number;
    is_current: boolean;
};

export type PlayerState = {
    time_pos: number;
    duration: number;
    is_paused: boolean;
    current_track: TrackInfo | null;
    queue: QueueItemState[];
    lyrics: string | null;
    accent_colors: [string, string, string];
};

export const playerState = writable<PlayerState>({
    time_pos: 0,
    duration: 1,
    is_paused: true,
    current_track: null,
    queue: [],
    lyrics: null,
    accent_colors: ["rgb(255, 255, 255)", "rgb(255, 255, 255)", "rgb(34, 34, 36)"],
});

export const queuePanelActive = writable(false);