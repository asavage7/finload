import { playerState, type PlayerState } from '$lib/store';

type PatchListener = (patch: Partial<PlayerState>) => void;
const patchListeners = new Set<PatchListener>();

export function onPlayerStatePatch(listener: PatchListener): () => void {
    patchListeners.add(listener);
    return () => patchListeners.delete(listener);
}

export function updatePlayerState(patch: Partial<PlayerState>): void {
    playerState.update(s => ({ ...s, ...patch }));
    patchListeners.forEach(l => l(patch));
}
