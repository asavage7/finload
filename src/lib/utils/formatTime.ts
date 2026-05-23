export function formatTime(time: number, isMs: boolean = false): string {
    const seconds = isMs ? time / 1000 : time;
    if (!seconds || isNaN(seconds)) return "0:00";
    if (seconds >= 3600) {
        const hours = Math.floor(seconds / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        return `${hours}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    } else if (seconds >= 60) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${String(secs).padStart(2, '0')}`;
    } else {
        const secs = Math.floor(seconds);
        return `0:${String(secs).padStart(2, '0')}`;
    }
}