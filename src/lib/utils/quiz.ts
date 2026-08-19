import { apiUrl } from '$lib/backend';

export type QuizAnswerStyle = 'multiple_choice' | 'open_ended';
export type QuizStartPoint = 'beginning' | 'random';

export type QuizChoice = {
    id: string;
    title: string;
    artist_name: string;
    album_title: string;
    album_id: string;
};

export type QuizRound = {
    round_number: number;
    difficulty_level: number;
    time_limit: number;
    answer_style: QuizAnswerStyle;
    // Empty for open-ended rounds, where the player types instead of picking.
    choices: QuizChoice[];
    score: number;
    correct_count: number;
};

export type QuizResult = {
    correct: boolean;
    points: number;
    score: number;
    correct_count: number;
    round_number: number;
    answer: QuizChoice;
    selected_id: string;
};

export type QuizOptions = {
    answer_style: QuizAnswerStyle;
    start_point: QuizStartPoint;
    time_limit: number;
};

export const QUIZ_TIME_LIMITS = [5, 10, 15, 30];
export const QUIZ_DEFAULT_TIME_LIMIT = 10;

// The quiz session lives in the backend's memory only, so it can vanish under
// the page (a backend restart, a stop from another window). Distinguished from
// a real failure so the page can just drop back to the setup screen.
export class QuizSessionLost extends Error {}

async function post(path: string, body?: unknown): Promise<any> {
    const res = await fetch(apiUrl(path), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
    });
    if (res.status === 409) throw new QuizSessionLost('No quiz in progress');
    if (!res.ok) throw new Error(`Quiz request failed: ${path}`);
    return res.json();
}

export async function startQuiz(options: QuizOptions): Promise<QuizRound> {
    return (await post('/api/quiz/start', options)).round;
}

export async function nextRound(): Promise<QuizRound> {
    return (await post('/api/quiz/next')).round;
}

export async function submitAnswer(guess: {
    trackId?: string;
    text?: string;
    elapsed: number;
}): Promise<QuizResult> {
    return post('/api/quiz/answer', {
        track_id: guess.trackId ?? '',
        text: guess.text ?? '',
        elapsed: guess.elapsed,
    });
}

export async function stopQuiz(): Promise<void> {
    // Called on the way out of the page, where there is nothing left to
    // recover if it fails, and the backend treats "no session" as a no-op.
    try {
        await post('/api/quiz/stop');
    } catch {
        // ignored
    }
}

export async function fetchSuggestions(q: string): Promise<QuizChoice[]> {
    try {
        const res = await fetch(apiUrl(`/api/quiz/suggest?q=${encodeURIComponent(q)}`));
        if (!res.ok) return [];
        const data = await res.json();
        return Array.isArray(data.results) ? data.results : [];
    } catch {
        return [];
    }
}
