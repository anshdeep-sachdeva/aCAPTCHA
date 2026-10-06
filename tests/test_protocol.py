from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient
import pytest

from acaptcha.server import ROOT, create_app, normalize


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def setup():
    clock = Clock()
    return TestClient(create_app(clock=clock)), clock


def start(client):
    response = client.post('/api/sessions', json={})
    assert response.status_code == 200
    return response.json()


def solution(challenge):
    corpus = json.loads((ROOT / 'data/corpus.json').read_text())
    for case in corpus:
        part = case['parts'][challenge['round'] - 1]
        if part['narrative'] == challenge['narrative']:
            return next(q['answer'] for q in part['questions'] if q['question'] == challenge['question'])
    raise AssertionError('Missing fixture answer')


def submit(client, challenge, answer=None, round_number=None):
    return client.post(challenge['submit_url'], json={
        'round': challenge['round'] if round_number is None else round_number,
        'answer': solution(challenge) if answer is None else answer,
    })


def test_full_session_no_answers_disclosed_and_cannot_replay(setup):
    client, clock = setup
    challenge = start(client)
    url = challenge['submit_url']
    for i in range(1, 4):
        assert challenge['round'] == i
        assert 'answer' not in challenge and 'questions' not in challenge
        clock.now += 2
        response = submit(client, challenge, '  '+solution(challenge).swapcase()+'  ')
        assert response.status_code == 200
        challenge = response.json()
    assert challenge['status'] == 'accepted'
    assert challenge['elapsed_seconds'] == 6
    assert client.post(url, json={'round': 3, 'answer': 'anything'}).status_code == 404


@pytest.mark.parametrize('kind,reason', [('wrong', 'wrong_answer'), ('order', 'out_of_order'),
                                         ('late', 'round_timeout')])
def test_failure_consumes_session(setup, kind, reason):
    client, clock = setup
    challenge = start(client)
    if kind == 'late':
        clock.now = 15.001
    response = submit(client, challenge, answer='incorrect' if kind == 'wrong' else None,
                      round_number=2 if kind == 'order' else None)
    assert response.json()['reason'] == reason
    assert submit(client, challenge).status_code == 404


def test_total_timeout_even_when_round_is_in_budget():
    clock = Clock()
    client = TestClient(create_app(clock=clock, round_seconds=15, session_seconds=20))
    challenge = start(client)
    clock.now = 14
    challenge = submit(client, challenge).json()
    clock.now = 21
    assert submit(client, challenge).json()['reason'] == 'session_timeout'


def test_exact_budget_is_accepted(setup):
    client, clock = setup
    challenge = start(client)
    clock.now = 15
    assert submit(client, challenge).json()['round'] == 2


def test_sessions_are_isolated_and_ids_unique(setup):
    client, _ = setup
    a, b = start(client), start(client)
    assert a['session_id'] != b['session_id']
    submit(client, a, answer='bad')
    assert submit(client, b).json()['status'] == 'challenge'


def test_duplicate_concurrent_submission_cannot_advance_twice(setup):
    client, _ = setup
    challenge = start(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(client, challenge).json(), range(2)))
    assert sorted(r['status'] for r in results) == ['challenge', 'rejected']
    assert next(r for r in results if r['status'] == 'rejected')['reason'] == 'out_of_order'


def test_capacity_reclaimed_after_expiry():
    clock = Clock()
    client = TestClient(create_app(clock=clock, max_sessions=1))
    first = start(client)
    assert client.post('/api/sessions', json={}).status_code == 503
    clock.now = 16
    start(client)
    assert submit(client, first).status_code == 404


def test_invalid_input_and_browser(setup):
    client, _ = setup
    assert client.post('/api/sessions', json={'domain': 'unknown'}).status_code == 400
    challenge = start(client)
    assert submit(client, challenge, round_number=4).status_code == 422
    assert client.get('/').status_code == 200
    assert client.get('/api/info').json()['timing_mode'] == 'server_elapsed_no_rtt_subtraction'


def test_unicode_matching():
    assert normalize('  CAFE\u0301 ') == normalize('café')
    assert normalize('Straße') == normalize('STRASSE')


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf')])
def test_invalid_settings(timeout):
    with pytest.raises(ValueError):
        create_app(round_seconds=timeout)
