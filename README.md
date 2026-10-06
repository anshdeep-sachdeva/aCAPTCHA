# aCAPTCHA capstone prototype

A runnable implementation of the three-round HTTP verification protocol described in
[aCAPTCHA: Verifying That an Entity Is a Capable Agent via Asymmetric Hardness](https://arxiv.org/abs/2603.07116)
(Xu et al., 2026), especially Section 5 and Algorithm 1. This is an independent implementation,
not the authors' code or a reproduction of their empirical results.

## Run

Requires Python 3.11 or newer. From this directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn acaptcha.server:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> for the browser demonstration or
<http://127.0.0.1:8000/docs> for the API. The browser retains prior narratives locally
so you can inspect the cross-round dependencies. The authoritative timer is on the server;
the browser countdown is an estimate. The countdown reaching zero does not itself submit
an answer; the server rejects a subsequent late submission.

For a slower manual walkthrough, restart with:

```sh
ACAPTCHA_ROUND_SECONDS=90 ACAPTCHA_SESSION_SECONDS=300 python -m uvicorn acaptcha.server:app --host 127.0.0.1 --port 8000
```

This relaxed setting demonstrates the workflow and does not test human exclusion.

## Agent interaction

Give a tool-capable agent this task:

> Complete the aCAPTCHA at http://127.0.0.1:8000. POST JSON {} to /api/sessions.
> Read each narrative and question, retain previous narratives, and submit the short
> answer with the returned round number to the returned submit_url. Continue until
> accepted or rejected. Use only the public HTTP API, not local corpus files.

The agent must run on the same machine to reach this loopback address.
The verifier needs no model API key. The solving agent uses its own model and tools.

1. `POST /api/sessions` with `{}` (or `{"domain":"cybersecurity"}`) starts the timer and returns round 1.
2. `POST /api/sessions/{session_id}/answers` with `{"round":1,"answer":"..."}` returns the next round or a verdict.
3. Only three correct, timely, sequential submissions yield `accepted`. Expected answers are never returned.

An unsuccessful valid submission consumes the session immediately. Invalid JSON/schema
returns HTTP 422 without consuming it; the timer continues. Unknown/consumed sessions
return HTTP 404. Domain errors return 400; active-session capacity errors return 503.
The accepted response is a research verdict, not an authentication token.

## Run the automated solver with a local model API

The runner supports a Chat Completions-compatible endpoint such as
`http://localhost:4000/v1`. Start the verifier as above, then use a second terminal:

```sh
python3 -m acaptcha.runner --list-models
python3 -m acaptcha.runner --model YOUR_MODEL_ID
```

Replace `YOUR_MODEL_ID` with an ID printed by the first command. If exactly one model
is listed, `python3 -m acaptcha.runner` selects it automatically. If model discovery
is unavailable, specify the model ID provided by your API's configuration directly.
If authentication is required, set `ACAPTCHA_LLM_API_KEY` in your environment before
running; do not paste keys into source files. Use `--llm-url` to change the API URL.

Once started, the runner performs all three rounds automatically. It prints each
question, the model's submitted answer, model call duration, and final server verdict.
Messages from previous rounds remain in the model context. It never reads the answer
corpus. This is a scripted LLM-backed client with HTTP actions and conversation state,
not a trial of an independently planning, general-purpose tool-using agent.

For a presentation, keep the runner terminal visible. The website is a separate
manual client and does not display sessions started by the runner. Open two terminals:
one for the verifier, one for the runner. To collect repeated trials:

```sh
python3 -m acaptcha.runner --model YOUR_MODEL_ID --runs 10
```

Results append to `runs/results.jsonl`, including public narratives, questions,
submitted answers, verdicts, and timings. They do not contain API keys or expected
answers. Exit code 0 means all sessions passed, 1 means at least one was rejected,
and 2 indicates a configuration or API error. No request retries are made within a
timed round. The model timeout is 120 seconds; the server still enforces its own
shorter deadline, so slow model responses can result in rejection.

For a walkthrough, use the relaxed verifier timing command above. For actual trials,
restore the default 15-second/120-second limits and record the model and settings.

## Verifier features

- Three semantically linked rounds testing action, reasoning, and cross-round recall.
- A precomputed corpus and independent random question selection for each round.
- HMAC-based random session IDs and server-side state; sequential submissions are serialized.
- NFC Unicode normalization, trimming, and case folding before exact answer comparison.
- Default 15-second round budgets and a 120-second session limit, using a monotonic clock.
- Immediate rejection, single-use completed sessions, bounded active sessions, and expiry cleanup on session creation.

## Scope and differences from the paper

The original starter narratives in `data/corpus.json` contain one cybersecurity case,
with three questions per part (27 question combinations). They demonstrate linked
reasoning but are shorter and less diverse than the paper's evaluation corpus.
They are public and can be memorized or parsed; random question selection does not
prevent corpus memorization. Session consumption prevents resubmitting a used ID,
but does not prevent reuse of known answers in new sessions. Do not infer robust
script resistance or human exclusion from this demo.

Timing includes transport latency and server processing. The paper subtracts a
transport-derived RTT; this FastAPI prototype does not claim to measure TCP handshake
latency or trust client-reported RTT. A faithful timing reproduction needs transport
instrumentation and a documented compensation policy.

State is in memory. Run **one worker**: restarts discard sessions and multiple workers
would not share state. There is no production admission credential, identity binding,
per-client rate limiting, or distributed storage. Concurrent duplicate requests invalidate
the session when the duplicate is recognized as out of order; clients should submit once.

## Verify

```sh
python -m pytest -q
```

Tests cover full acceptance, answer secrecy in API responses, normalization, wrong
answers, ordering, duplicate submissions, round and session timeouts, exact deadline
boundaries, session isolation, expiry, capacity, and input validation. The success test
reads fixture answers only to verify the protocol; it is not an agent trial.

## Next implementation stage

Expand the private corpus across domains; independently validate every answer and
cross-round dependency. Add a tool-using agent trial harness that records correctness,
round timing, and session verdicts without giving the solver answer keys. Then compare
agent, stateless, and heuristic baselines and calibrate thresholds from observed data.
The paper's human timing results are modeled, so report simulations separately from
actual human trials. Implement RTT instrumentation before claiming faithful reproduction.
