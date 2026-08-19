import { writable, derived } from "svelte/store";

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
        artist_id: string | null;
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
    volume: number;
    current_track: TrackInfo | null;
    queue: QueueItemState[];
    lyrics: string | null;
    accent_colors: [string, string, string];
    repeat_mode: 0 | 1 | 2;
    shuffle: boolean;
    radio_enabled: boolean;
};

// Hex (not rgb()) so the `{color}25` alpha-append pattern used in styles stays
// valid CSS even on the default/no-track state.
export const DEFAULT_ACCENT_COLORS: [string, string, string] = [
    '#505050',
    '#ffffff',
    '#1e1e1e',
];

export const playerState = writable<PlayerState>({
    time_pos: 0,
    duration: 1,
    is_paused: true,
    volume: 100,
    current_track: null,
    queue: [],
    lyrics: null,
    accent_colors: DEFAULT_ACCENT_COLORS,
    repeat_mode: 0,
    shuffle: false,
    radio_enabled: false,
});

// null = not yet checked against the backend; true/false = known state.
// Drives the +layout.svelte redirect guard to /onboarding.
export const onboardingComplete = writable<boolean | null>(null);

export const queuePanelActive = writable(false);

// Reserved width (px) of the right queue panel. Mirrors its `w-80` class and is
// what the page reserves as padding-right while it's open.
export const QUEUE_PANEL_WIDTH = 320;

// Left navigation panel. It can be condensed to an icon-only rail but never
// closed, so we track a condensed flag rather than an open/closed one.
export const leftPanelCondensed = writable(true);
export const LEFT_PANEL_WIDTH_EXPANDED = 256;
export const LEFT_PANEL_WIDTH_CONDENSED = 68;
export const leftPanelWidth = derived(leftPanelCondensed, ($condensed) =>
    $condensed ? LEFT_PANEL_WIDTH_CONDENSED : LEFT_PANEL_WIDTH_EXPANDED,
);

// Live viewport width. Drives responsive panel behaviour and lets the grid lock
// its column count to the window instead of the (panel-dependent) content width.
export const windowWidth = writable<number>(
    typeof window !== "undefined" ? window.innerWidth : 1280,
);

// Below PUSH_BREAKPOINT the panels float over the content (modal-like) instead
// of pushing it aside.
export const PUSH_BREAKPOINT = 1000;

export const panelsOverlay = derived(windowWidth, ($w) => $w < PUSH_BREAKPOINT);

// Layout space each panel reserves (content padding + footer offset). A panel
// only reserves space while it pushes content; when overlaying it reserves
// nothing. The condensed left rail always reserves its width so it stays beside
// the content even below the overlay breakpoint.
export const leftPanelReserve = derived(
    [windowWidth, leftPanelCondensed],
    ([$w, $condensed]) =>
        $w >= PUSH_BREAKPOINT
            ? $condensed
                ? LEFT_PANEL_WIDTH_CONDENSED
                : LEFT_PANEL_WIDTH_EXPANDED
            : LEFT_PANEL_WIDTH_CONDENSED,
);
export const rightPanelReserve = derived(
    [windowWidth, queuePanelActive],
    ([$w, $active]) =>
        $w >= PUSH_BREAKPOINT && $active ? QUEUE_PANEL_WIDTH : 0,
);

export const libraryActiveTab = writable<'Tracks' | 'Albums' | 'Artists' | 'Playlists'>('Albums');

// Persist scroll position and view type across navigation so returning to the
// library page restores where the user was.
export const libraryScrollTop = writable<Record<string, number>>({});
export const libraryActiveView = writable<Record<string, string>>({
    Albums: 'grid',
    Tracks: 'list',
    Artists: 'grid',
    Playlists: 'grid',
});
export const libraryItemCache = writable<Record<string, (any | undefined)[]>>({});
export const libraryTotalCounts = writable<Record<string, number>>({});

export type SortState = { field: string; order: 'asc' | 'desc' };

export const librarySortState = writable<Record<string, SortState>>({
    Albums: { field: 'title', order: 'asc' },
    Tracks: { field: 'title', order: 'asc' },
    Artists: { field: 'name', order: 'asc' },
    Playlists: { field: 'name', order: 'asc' },
});

export const playlistPickerStore = writable<{ open: boolean; trackIds: string[] }>({
    open: false,
    trackIds: [],
});

// Keyed by playlist ID — bump the value to force PlaylistCover to re-fetch its image everywhere.
export const playlistCoverTimestamps = writable<Record<string, number>>({});

// Keyed by item id — updated by Rating on any change so list views stay in sync.
export const ratingOverrides = writable<Record<string, number>>({});

export const playlistEditStore = writable<{ open: boolean; playlist: any | null }>({
    open: false,
    playlist: null,
});

type ConfirmState = {
    open: boolean;
    title: string;
    message: string;
    confirmLabel: string;
    destructive: boolean;
};

let _confirmResolve: ((v: boolean) => void) | null = null;

export const confirmStore = writable<ConfirmState>({
    open: false,
    title: '',
    message: '',
    confirmLabel: 'Confirm',
    destructive: false,
});

export function showConfirm(opts: Partial<Omit<ConfirmState, 'open'>>): Promise<boolean> {
    return new Promise(resolve => {
        _confirmResolve = resolve;
        confirmStore.set({
            open: true,
            title: opts.title ?? '',
            message: opts.message ?? '',
            confirmLabel: opts.confirmLabel ?? 'Confirm',
            destructive: opts.destructive ?? false,
        });
    });
}

export function resolveConfirm(value: boolean) {
    confirmStore.update(s => ({ ...s, open: false }));
    _confirmResolve?.(value);
    _confirmResolve = null;
}