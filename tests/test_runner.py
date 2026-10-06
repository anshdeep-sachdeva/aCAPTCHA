import json

import httpx
from fastapi.testclient import TestClient

from acaptcha.runner import model_ids, run_session
from acaptcha.server import create_app


def fixture_verifier(tmp_path):
    path = tmp_path / 'corpus.json'
    path.write_text(json.dumps([{'id': 'test', 'domain': 'test', 'parts': [
        {'narrative': f'Fact {i}', 'questions': [{'question': f'Question {i}', 'answer': f'A{i}'}]}
        for i in range(1, 4)]}]))
    return TestClient(create_app(corpus_path=path))


def test_runner_retains_history_and_completes(tmp_path):
    requests = []

    def model(request):
        body = json.loads(request.content)
        requests.append(body)
        number = len(requests)
        return httpx.Response(200, json={'choices': [{'message': {'content': f'A{number}'}}]})

    with httpx.Client(transport=httpx.MockTransport(model)) as llm:
        report = run_session(fixture_verifier(tmp_path), llm, 'http://model/v1', 'test', emit=lambda _: None)
    assert report['status'] == 'accepted'
    assert len(report['rounds']) == 3
    assert [len(r['messages']) for r in requests] == [2, 4, 6]
    assert requests[2]['messages'][1]['content'].startswith('Round 1')
    assert requests[2]['messages'][2]['content'] == 'A1'
    assert all('answer' not in r['messages'][-1]['content'].lower() for r in requests)


def test_runner_stops_on_wrong_answer(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={'choices': [{'message': {'content': 'wrong'}}]}))) as llm:
        report = run_session(fixture_verifier(tmp_path), llm, 'http://model/v1', 'test', emit=lambda _: None)
    assert report['status'] == 'rejected'
    assert report['verdict']['reason'] == 'wrong_answer'
    assert len(report['rounds']) == 1


def test_provider_error_is_distinct_from_rejection(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401, text='private provider detail'))) as llm:
        report = run_session(fixture_verifier(tmp_path), llm, 'http://model/v1', 'test', emit=lambda _: None)
    assert report['status'] == 'error'
    assert report['http_status'] == 401
    assert 'private provider detail' not in json.dumps(report)


def test_model_discovery():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={'data': [{'id': 'my-model'}]}))) as llm:
        assert model_ids(llm, 'http://model/v1/') == ['my-model']
