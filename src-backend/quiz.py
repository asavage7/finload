"""Music quiz game: round generation, difficulty scaling and snippet playback.

Snippets play on a dedicated mpv instance rather than through PlaybackManager.
The main player writes the queue, the play history and the now-playing state on
every load, and all three are visible in the UI, so routing quiz audio through
it would hand the player the answer before the guess is in.

One session lives in this module at a time. It is deliberately in-memory only:
a quiz is a single sitting, and a backend restart mid-game just drops the
player back to the setup screen.
"""
import random
import re
import threading

import mpv
from peewee import JOIN, fn

import state
from database import Album, Artist, PlayHistory, Track

ANSWER_STYLES = ("multiple_choice", "open_ended")
START_POINTS = ("beginning", "random")

MIN_TIME_LIMIT = 3
MAX_TIME_LIMIT = 60
DEFAULT_TIME_LIMIT = 10

CHOICE_COUNT = 4
ROUNDS_PER_LEVEL = 10   # difficulty steps up once every this many rounds
MAX_LEVEL = 6           # past this the mix stops tightening, so it stays winnable

# Chance that any one wrong option is drawn from the answer's own album or
# artist instead of from anywhere in the library. Rises with the level, so late
# rounds become "which of these four tracks by the same artist is playing"
# rather than "do you recognise the artist".
RELATED_BASE = 0.15
RELATED_STEP = 0.18
RELATED_MAX = 0.85

# How far answer picking swings from "tracks you play constantly" toward
# "tracks you have barely touched" as the level rises.
OBSCURITY_STEP = 0.22
OBSCURITY_MAX = 0.95

# Random start points stay within this fraction of the track, so a snippet is
# never intro silence or the fade-out.
SNIPPET_WINDOW = (0.05, 0.7)
# Seconds of track left after a random start point, so the snippet can run the
# full time limit without the track ending underneath it.
SNIPPET_TAIL = 1.0
# How many tracks a round will try before giving up, in case the ones it draws
# have no playable audio.
PLAY_ATTEMPTS = 5

POINTS_BASE = 100          # awarded for any correct answer
POINTS_SPEED_BONUS = 100   # awarded in full for an instant answer, scaled down
                           # linearly to zero as the time limit runs out
POINTS_LEVEL_BONUS = 0.25  # multiplier added per difficulty level

SUGGESTION_LIMIT = 6

# Same allowlist normalisation as library search: lowercase, drop everything
# that is not a word character or whitespace, collapse runs of whitespace. Used
# both to match typed answers against the real title and to rank suggestions.
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    return " ".join(_NON_WORD.sub("", (text or "").lower()).split())


class QuizError(Exception):
    """A request that cannot be served in the session's current state."""


# ---------------------------------------------------------------------------
# Snippet playback
# ---------------------------------------------------------------------------

class QuizPlayer:
    """The quiz's own mpv core, created on first use and reused after that."""

    def __init__(self):
        self._player = None
        self._lock = threading.Lock()

    def _ensure_player(self):
        if self._player is None:
            self._player = mpv.MPV(
                ytdl=False,
                osc=False,
                vid='no',
                config='no',
                profile='low-latency',
            )
            self._player['audio-buffer'] = 0.2
        return self._player

    def play(self, url: str, start: float = 0.0):
        """Load ``url`` at ``start`` and return once audio is actually running.

        ``loadfile`` returns the instant the command is queued, long before the
        file is open and decoding. The round clock starts when the UI gets its
        response, so returning there would spend the opening seconds of the
        player's time on silence. mpv's playback-restart event is its own
        report that playback has (re)started, so waiting on it hands back a
        stream that is genuinely at ``start`` and playing.

        No timeout is imposed: mpv errors are raised through the event's error
        handler, and a source that never responds is bounded by mpv's own
        network-timeout rather than a second guess at the same number here.
        """
        with self._lock:
            player = self._ensure_player()
            player.pause = False
            with player.prepare_and_wait_for_event('playback-restart'):
                player.loadfile(url, start=str(max(0.0, start)))

    def stop(self):
        with self._lock:
            if self._player is None:
                return
            try:
                self._player.command('stop')
            except Exception:
                pass


player = QuizPlayer()


def _play_snippet(row: dict, offset: float) -> bool:
    """Start the answer track at ``offset`` on the quiz player.

    The main player is paused first: the two mpv cores share an audio output
    and would otherwise talk over each other. Returns False if the track has no
    usable stream (a local file that moved, a provider that is unreachable), so
    the caller can move on to a different track instead of arming a silent
    round.
    """
    playback = getattr(state, "playback", None)
    if playback is not None and not playback.is_paused:
        playback.toggle_pause()
    try:
        # A provider that can position the stream itself hands back a URL that
        # already begins at the offset, leaving the player nothing to seek.
        url, seek = state.provider.get_seeked_stream(row["id"], offset)
    except Exception:
        return False
    try:
        player.play(url, seek)
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# Track pool
# ---------------------------------------------------------------------------

def _load_pool() -> list[dict]:
    """Every track in the library, with everything a round needs about it.

    Loaded once when a session starts rather than per round: the play-count
    aggregate is the expensive part, the counts barely move over one sitting,
    and holding the whole pool in memory means picking an answer, its wrong
    options and its suggestions costs no queries at all.
    """
    rows = list(Track
                .select(Track.id.alias("id"),
                        Track.title.alias("title"),
                        Track.duration_ms.alias("duration_ms"),
                        Album.id.alias("album_id"),
                        Album.title.alias("album_title"),
                        Artist.id.alias("artist_id"),
                        Artist.name.alias("artist_name"),
                        fn.COUNT(PlayHistory.id).alias("plays"))
                .join(Album, on=(Track.album == Album.id))
                .switch(Track)
                .join(Artist, on=(Track.artist == Artist.id))
                .switch(Track)
                .join(PlayHistory, JOIN.LEFT_OUTER,
                      on=((PlayHistory.track == Track.id) & (PlayHistory.visible == True)))
                .group_by(Track.id)
                .dicts())
    for row in rows:
        row["id"] = str(row["id"])
        row["album_id"] = str(row["album_id"] or "")
        row["artist_id"] = str(row["artist_id"] or "")
        row["title"] = str(row["title"] or "")
        row["album_title"] = str(row["album_title"] or "")
        row["artist_name"] = str(row["artist_name"] or "Unknown Artist")
        # Precomputed so suggestion ranking, which rescans the whole pool on
        # every keystroke, never re-normalises the same strings.
        row["norm_title"] = _normalize(row["title"])
        row["norm_artist"] = _normalize(row["artist_name"])
    return rows


def _choice(row: dict) -> dict:
    """The public shape of an answer option (what a MediaRow needs to render)."""
    return {
        "id": row["id"],
        "title": row["title"],
        "artist_name": row["artist_name"],
        "album_title": row["album_title"],
        "album_id": row["album_id"],
    }


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class QuizSession:
    def __init__(self, answer_style: str, start_point: str, time_limit: int):
        self.answer_style = answer_style
        self.start_point = start_point
        self.time_limit = time_limit
        self.round_number = 0
        self.score = 0
        self.correct_count = 0
        self.pool = _load_pool()
        self.by_id = {row["id"]: row for row in self.pool}
        # Tracks already used as an answer, so one game does not ask the same
        # song twice until the library runs out.
        self.used_track_ids: set[str] = set()
        self.current: dict | None = None
        self.answered = True

    def settings(self) -> dict:
        return {
            "answer_style": self.answer_style,
            "start_point": self.start_point,
            "time_limit": self.time_limit,
            "track_count": len(self.pool),
        }


_session: QuizSession | None = None
_session_lock = threading.Lock()


def _difficulty_level(round_number: int) -> int:
    """0 for the first ROUNDS_PER_LEVEL rounds, then one higher per block."""
    return min(MAX_LEVEL, max(0, (round_number - 1) // ROUNDS_PER_LEVEL))


def _weighted_choice(entries: list, weights: list):
    total = sum(weights)
    if total <= 0:
        return random.choice(entries)
    threshold = random.random() * total
    running = 0.0
    for entry, weight in zip(entries, weights):
        running += weight
        if running >= threshold:
            return entry
    return entries[-1]


def _pick_answer(session: QuizSession, level: int) -> dict | None:
    """Sample the round's answer track, biased by difficulty.

    Level 0 leans toward the tracks with the most plays, the ones the player is
    most likely to be able to name. Each level shifts the bias further toward
    tracks with little or no play history.
    """
    candidates = [row for row in session.pool if row["id"] not in session.used_track_ids]
    if not candidates:
        # Library exhausted: start reusing rather than ending the game.
        session.used_track_ids.clear()
        candidates = session.pool
    if not candidates:
        return None

    obscurity = min(OBSCURITY_MAX, OBSCURITY_STEP * level)
    familiar = [1.0 + row["plays"] for row in candidates]
    obscure = [1.0 / (1.0 + row["plays"]) for row in candidates]
    # Both terms are rescaled to a mean of 1 before mixing. Raw play counts and
    # their reciprocals sit on very different scales, so blending them as-is
    # would let the play-count side dominate at every level.
    familiar_mean = sum(familiar) / len(familiar)
    obscure_mean = sum(obscure) / len(obscure)
    weights = [
        (1.0 - obscurity) * (f / familiar_mean) + obscurity * (o / obscure_mean)
        for f, o in zip(familiar, obscure)
    ]
    return _weighted_choice(candidates, weights)


def _pick_wrong_options(session: QuizSession, answer: dict, level: int,
                        count: int) -> list[dict]:
    """Choose the wrong options for a multiple-choice round.

    Each slot is filled either from the answer's own album/artist or from
    anywhere in the library, with the odds of the former rising per level.
    Same-album candidates are used before same-artist ones because they are the
    tighter confusion of the two. Options whose title matches the answer's are
    skipped, since a second correct-looking title makes the round unfair.
    """
    related_chance = min(RELATED_MAX, RELATED_BASE + RELATED_STEP * level)
    answer_title = answer["norm_title"]

    same_album, same_artist = [], []
    for row in session.pool:
        if row["id"] == answer["id"] or row["norm_title"] == answer_title:
            continue
        if answer["album_id"] and row["album_id"] == answer["album_id"]:
            same_album.append(row)
        elif answer["artist_id"] and row["artist_id"] == answer["artist_id"]:
            same_artist.append(row)
    random.shuffle(same_album)
    random.shuffle(same_artist)
    related = same_album + same_artist

    chosen: list[dict] = []
    taken = {answer["id"]}
    taken_titles = {answer_title}
    for _ in range(count):
        pick = None
        if related and random.random() < related_chance:
            while related:
                candidate = related.pop(0)
                if candidate["id"] not in taken and candidate["norm_title"] not in taken_titles:
                    pick = candidate
                    break
        if pick is None:
            # Bounded rather than a filtered copy of the pool: rejections are
            # rare (only the handful already taken), and building a filtered
            # list per slot would walk the whole library four times a round.
            for _ in range(50):
                candidate = random.choice(session.pool)
                if candidate["id"] not in taken and candidate["norm_title"] not in taken_titles:
                    pick = candidate
                    break
        if pick is None:
            continue
        taken.add(pick["id"])
        taken_titles.add(pick["norm_title"])
        chosen.append(pick)
    return chosen


def _start_offset(session: QuizSession, row: dict) -> float:
    duration = (row["duration_ms"] or 0) / 1000.0
    if session.start_point != "random" or duration <= 0:
        return 0.0
    latest = duration - session.time_limit - SNIPPET_TAIL
    if latest <= 0:
        return 0.0
    low, high = SNIPPET_WINDOW
    return min(latest, random.uniform(duration * low, duration * high))


def _round_payload(session: QuizSession, choices: list[dict]) -> dict:
    current = session.current or {}
    return {
        "round_number": session.round_number,
        "difficulty_level": current.get("level", 0),
        "time_limit": session.time_limit,
        "answer_style": session.answer_style,
        "choices": choices,
        "score": session.score,
        "correct_count": session.correct_count,
    }


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------

def start_session(answer_style: str, start_point: str, time_limit: int) -> QuizSession:
    global _session
    if answer_style not in ANSWER_STYLES:
        raise QuizError(f"Unknown answer style: {answer_style}")
    if start_point not in START_POINTS:
        raise QuizError(f"Unknown starting point: {start_point}")
    time_limit = max(MIN_TIME_LIMIT, min(MAX_TIME_LIMIT, int(time_limit)))

    with _session_lock:
        player.stop()
        session = QuizSession(answer_style, start_point, time_limit)
        if not session.pool:
            raise QuizError("Library has no tracks to quiz on")
        _session = session
        return session


def require_session() -> QuizSession:
    session = _session
    if session is None:
        raise QuizError("No quiz in progress")
    return session


def end_session():
    global _session
    with _session_lock:
        player.stop()
        _session = None


def next_round(session: QuizSession) -> dict:
    """Pick the next answer, start its clip and return the round to play.

    A track whose audio will not load is marked used and another is drawn, so
    one missing file cannot end the game. The round number only advances once a
    clip is actually playing.
    """
    level = _difficulty_level(session.round_number + 1)
    answer, offset, choices = None, 0.0, []
    for _ in range(PLAY_ATTEMPTS):
        candidate = _pick_answer(session, level)
        if candidate is None:
            break
        session.used_track_ids.add(candidate["id"])
        candidate_offset = _start_offset(session, candidate)

        # Built before the clip starts, not after. Choosing the wrong options
        # walks the whole pool, and any work done between the audio starting
        # and this response landing is time the player never gets to hear.
        candidate_choices: list[dict] = []
        if session.answer_style == "multiple_choice":
            options = [candidate] + _pick_wrong_options(
                session, candidate, level, CHOICE_COUNT - 1)
            random.shuffle(options)
            candidate_choices = [_choice(row) for row in options]

        if _play_snippet(candidate, candidate_offset):
            answer, offset, choices = candidate, candidate_offset, candidate_choices
            break
    if answer is None:
        raise QuizError("Couldn't play any track from your library")

    session.round_number += 1
    session.current = {"answer": answer, "level": level, "offset": offset}
    session.answered = False
    return _round_payload(session, choices)


def _award(session: QuizSession, level: int, elapsed: float) -> int:
    remaining = max(0.0, session.time_limit - max(0.0, elapsed))
    speed = remaining / session.time_limit if session.time_limit else 0.0
    return int(round((POINTS_BASE + POINTS_SPEED_BONUS * speed)
                     * (1.0 + POINTS_LEVEL_BONUS * level)))


def submit_answer(session: QuizSession, track_id: str = "", text: str = "",
                  elapsed: float = 0.0) -> dict:
    """Grade a guess, stop the snippet and reveal the answer.

    A guess counts when it names the same track, or when the typed/selected
    title matches the answer's title. The title fallback matters for open-ended
    rounds, where naming the right song off a different release should not be
    marked wrong just because it resolves to another track id.
    """
    if session.current is None:
        raise QuizError("No round in progress")
    if session.answered:
        raise QuizError("This round has already been answered")

    answer = session.current["answer"]
    level = session.current["level"]

    guess_title = text or ""
    if track_id:
        guessed_row = session.by_id.get(str(track_id))
        if guessed_row:
            guess_title = guessed_row["title"]

    correct = str(track_id) == answer["id"] if track_id else False
    if not correct and guess_title:
        correct = _normalize(guess_title) == answer["norm_title"]

    points = _award(session, level, elapsed) if correct else 0
    session.answered = True
    session.score += points
    if correct:
        session.correct_count += 1
    player.stop()

    return {
        "correct": correct,
        "points": points,
        "score": session.score,
        "correct_count": session.correct_count,
        "round_number": session.round_number,
        "answer": _choice(answer),
        "selected_id": str(track_id) if track_id else "",
    }


def _suggestion_score(row: dict, query: str, tokens: list[str]) -> int:
    """Rank one pool row against a typed query.

    Tiered the same way library search is (exact beats prefix beats word-start
    beats substring), but scored against titles the pool already normalised and
    without the cross-entity tiebreaks, since only tracks are ever suggested.
    """
    title = row["norm_title"]
    if title == query:
        return 1000
    if title.startswith(query):
        return 800
    if any(word.startswith(query) for word in title.split()):
        return 600
    if query in title:
        return 400
    if len(tokens) > 1 and all(tok in f"{title} {row['norm_artist']}" for tok in tokens):
        return 200
    return 0


def suggest(session: QuizSession, query: str, limit: int = SUGGESTION_LIMIT) -> list[dict]:
    query = _normalize(query)
    if not query:
        return []
    tokens = query.split()
    scored = []
    for row in session.pool:
        score = _suggestion_score(row, query, tokens)
        if score > 0:
            scored.append((score, -len(row["title"]), row))
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [_choice(row) for _, _, row in scored[:max(1, limit)]]
