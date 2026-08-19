"""Music quiz routes: session lifecycle, rounds, grading and suggestions."""
from fastapi import APIRouter, Body, HTTPException

import quiz

router = APIRouter()


def _session():
    try:
        return quiz.require_session()
    except quiz.QuizError as e:
        # 409 rather than 404: the route exists, there is just no game running
        # (e.g. the backend restarted mid-quiz), and the UI reacts by dropping
        # back to the setup screen.
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/quiz/start")
def start_quiz(
    answer_style: str = Body("multiple_choice", embed=True),
    start_point: str = Body("beginning", embed=True),
    time_limit: int = Body(quiz.DEFAULT_TIME_LIMIT, embed=True),
):
    try:
        session = quiz.start_session(answer_style, start_point, time_limit)
        return {"settings": session.settings(), "round": quiz.next_round(session)}
    except quiz.QuizError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/quiz/next")
def next_quiz_round():
    session = _session()
    try:
        return {"round": quiz.next_round(session)}
    except quiz.QuizError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/quiz/answer")
def answer_quiz_round(
    track_id: str = Body("", embed=True),
    text: str = Body("", embed=True),
    elapsed: float = Body(0.0, embed=True),
):
    session = _session()
    try:
        return quiz.submit_answer(session, track_id=track_id, text=text, elapsed=elapsed)
    except quiz.QuizError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/api/quiz/suggest")
def suggest_answers(q: str = "", limit: int = quiz.SUGGESTION_LIMIT):
    session = _session()
    return {"results": quiz.suggest(session, q, limit)}


@router.post("/api/quiz/stop")
def stop_quiz():
    """End the session and silence the snippet. Safe to call with none running,
    so the UI can fire it on navigating away without checking first."""
    quiz.end_session()
    return {"status": "ok"}
