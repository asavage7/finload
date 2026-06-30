import { playerState, type PlayerState } from '$lib/store';

export function updatePlayerState(patch: Partial<PlayerState>): void {
    playerState.update(s => ({ ...s, ...patch }));
}
