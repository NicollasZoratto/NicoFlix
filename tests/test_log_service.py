from unittest.mock import patch, MagicMock


def test_log_sem_campos_obrigatorios_retorna_400(log_app):
    client = log_app.app.test_client()
    res = client.post('/log', json={"acao": "login"})  # falta usuario_id
    assert res.status_code == 400


def test_log_evento_valido_grava_no_redis_stream(log_app):
    client = log_app.app.test_client()

    mock_redis = MagicMock()
    mock_redis.xadd.return_value = "1234567890-0"

    with patch.object(log_app, 'get_redis', return_value=mock_redis):
        res = client.post('/log', json={"usuario_id": 1, "acao": "login", "ip": "127.0.0.1"})

    assert res.status_code == 201
    assert res.get_json()['id'] == "1234567890-0"

    # confirma que foi usado XADD (Redis Streams), com os campos mínimos exigidos
    args, kwargs = mock_redis.xadd.call_args
    stream_key, fields = args
    assert stream_key == log_app.STREAM_KEY
    assert fields['usuario_id'] == '1'
    assert fields['acao'] == 'login'
    assert 'timestamp' in fields
    assert fields['ip'] == '127.0.0.1'


def test_listar_eventos_retorna_do_mais_recente_pro_mais_antigo(log_app):
    client = log_app.app.test_client()

    mock_redis = MagicMock()
    mock_redis.xrevrange.return_value = [
        ("2-0", {"usuario_id": "1", "acao": "logout", "timestamp": "t2", "ip": ""}),
        ("1-0", {"usuario_id": "1", "acao": "login", "timestamp": "t1", "ip": ""}),
    ]

    with patch.object(log_app, 'get_redis', return_value=mock_redis):
        res = client.get('/events?limit=10')

    data = res.get_json()
    assert res.status_code == 200
    assert data['count'] == 2
    assert data['events'][0]['acao'] == 'logout'  # mais recente primeiro
    mock_redis.xrevrange.assert_called_once_with(log_app.STREAM_KEY, count=10)


def test_limit_e_limitado_a_1000(log_app):
    client = log_app.app.test_client()
    mock_redis = MagicMock()
    mock_redis.xrevrange.return_value = []

    with patch.object(log_app, 'get_redis', return_value=mock_redis):
        client.get('/events?limit=999999')

    mock_redis.xrevrange.assert_called_once_with(log_app.STREAM_KEY, count=1000)


def test_health_check(log_app):
    client = log_app.app.test_client()
    mock_redis = MagicMock()

    with patch.object(log_app, 'get_redis', return_value=mock_redis):
        res = client.get('/health')

    assert res.status_code == 200
    assert res.get_json()['status'] == 'ok'
