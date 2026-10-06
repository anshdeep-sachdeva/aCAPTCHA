"""Single-process research prototype of the 2026 aCAPTCHA protocol."""
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
import unicodedata

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent


def normalize(answer: str) -> str:
    return unicodedata.normalize("NFC", answer.strip()).casefold()


def load_corpus(path: Path) -> list[dict]:
    cases = json.loads(path.read_text())
    if not cases:
        raise ValueError("The corpus must contain at least one case")
    for case in cases:
        if not case.get("id") or not case.get("domain") or len(case.get("parts", [])) != 3:
            raise ValueError("Each case needs an id, domain, and exactly three parts")
        for part in case["parts"]:
            if not part.get("narrative") or not part.get("questions"):
                raise ValueError("Each part needs a narrative and questions")
            for qa in part["questions"]:
                if not isinstance(qa.get("question"), str) or not qa["question"].strip():
                    raise ValueError("Questions must be nonempty strings")
                if not isinstance(qa.get("answer"), str) or not normalize(qa["answer"]):
                    raise ValueError("Answers must be nonempty strings")
    return cases


@dataclass
class Session:
    case: dict
    questions: list[dict]
    started: float
    round_started: float
    round_index: int = 0


class StartRequest(BaseModel):
    domain: str | None = Field(default=None, max_length=100)


class AnswerRequest(BaseModel):
    round: int = Field(ge=1, le=3)
    answer: str = Field(max_length=200)


def create_app(corpus_path: Path | None = None, round_seconds: float = 15,
               session_seconds: float = 120, clock=time.monotonic,
               max_sessions: int = 1000) -> FastAPI:
    if (not math.isfinite(round_seconds) or not math.isfinite(session_seconds)
            or min(round_seconds, session_seconds) <= 0 or max_sessions < 1):
        raise ValueError("Timeouts and capacity must be finite and positive")
    corpus = load_corpus(corpus_path or ROOT / "data" / "corpus.json")
    app = FastAPI(title="aCAPTCHA prototype", version="0.1.0")
    sessions: dict[str, Session] = {}
    secret = secrets.token_bytes(32)
    lock = threading.Lock()

    def challenge(sid: str, session: Session, now: float) -> dict:
        idx = session.round_index
        return {
            "status": "challenge", "session_id": sid, "round": idx + 1,
            "total_rounds": 3, "domain": session.case["domain"],
            "narrative": session.case["parts"][idx]["narrative"],
            "question": session.questions[idx]["question"],
            "round_budget_seconds": round_seconds,
            "session_remaining_seconds": max(0, session_seconds - (now - session.started)),
            "submit_url": f"/api/sessions/{sid}/answers",
        }

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web" / "index.html")

    @app.get("/api/info")
    def info():
        return {"domains": sorted({c["domain"] for c in corpus}),
                "round_seconds": round_seconds, "session_seconds": session_seconds,
                "rounds": 3, "timing_mode": "server_elapsed_no_rtt_subtraction",
                "prototype": True}

    @app.post("/api/sessions")
    def start(body: StartRequest):
        candidates = [c for c in corpus if body.domain is None or c["domain"] == body.domain]
        if not candidates:
            raise HTTPException(400, "Unknown domain")
        with lock:
            now = clock()
            for sid, s in list(sessions.items()):
                if now - s.started > session_seconds or now - s.round_started > round_seconds:
                    del sessions[sid]
            if len(sessions) >= max_sessions:
                raise HTTPException(503, "Session capacity reached; retry later")
            case = secrets.choice(candidates)
            sid = hmac.new(secret, secrets.token_bytes(32), hashlib.sha256).hexdigest()
            s = Session(case, [secrets.choice(p["questions"]) for p in case["parts"]], now, now)
            sessions[sid] = s
            return challenge(sid, s, now)

    @app.post("/api/sessions/{sid}/answers")
    def answer(sid: str, body: AnswerRequest):
        with lock:
            s = sessions.get(sid)
            if s is None:
                raise HTTPException(404, "Unknown, consumed, or expired session")
            now = clock()
            elapsed = now - s.round_started
            reason = None
            if now - s.started > session_seconds:
                reason = "session_timeout"
            elif elapsed > round_seconds:
                reason = "round_timeout"
            elif body.round != s.round_index + 1:
                reason = "out_of_order"
            elif normalize(body.answer) != normalize(s.questions[s.round_index]["answer"]):
                reason = "wrong_answer"
            if reason:
                del sessions[sid]
                return {"status": "rejected", "reason": reason,
                        "round": s.round_index + 1, "elapsed_seconds": elapsed}
            if s.round_index == 2:
                del sessions[sid]
                return {"status": "accepted", "rounds_completed": 3,
                        "elapsed_seconds": now - s.started}
            s.round_index += 1
            s.round_started = clock()
            response = challenge(sid, s, s.round_started)
            response["previous_round_elapsed_seconds"] = elapsed
            return response

    return app


app = create_app(round_seconds=float(os.getenv("ACAPTCHA_ROUND_SECONDS", "15")),
                 session_seconds=float(os.getenv("ACAPTCHA_SESSION_SECONDS", "120")))
