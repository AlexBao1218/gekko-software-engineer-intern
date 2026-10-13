"""Keep a basic liveness check alongside the import/query business tests."""


def test_health_is_available(client):
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
