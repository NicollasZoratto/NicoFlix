from unittest.mock import patch, MagicMock


def test_primeiro_usuario_cadastrado_vira_admin(auth_app):
    client = auth_app.app.test_client()

    mock_cursor = MagicMock()
    # 1ª query: verifica se username/email já existe -> não existe
    # 2ª query: conta quantos usuários já existem -> 0 (primeiro usuário)
    mock_cursor.fetchone.side_effect = [None, {'total': 0}]

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch('mysql.connector.connect', return_value=mock_conn):
        res = client.post('/register', json={
            "username": "nicollas",
            "email": "nicollas@example.com",
            "password": "senha123"
        })

    assert res.status_code == 201
    assert res.get_json()['role'] == 'admin'


def test_segundo_usuario_cadastrado_vira_usuario_comum(auth_app):
    client = auth_app.app.test_client()

    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = [None, {'total': 1}]

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch('mysql.connector.connect', return_value=mock_conn):
        res = client.post('/register', json={
            "username": "davi",
            "email": "davi@example.com",
            "password": "senha456"
        })

    assert res.status_code == 201
    assert res.get_json()['role'] == 'usuario'


def test_cadastro_com_dados_incompletos_retorna_400(auth_app):
    client = auth_app.app.test_client()
    res = client.post('/register', json={"username": "sofoo"})
    assert res.status_code == 400


def test_login_com_credenciais_invalidas_retorna_401(auth_app):
    client = auth_app.app.test_client()

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None  # nenhum usuário encontrado

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch('mysql.connector.connect', return_value=mock_conn):
        res = client.post('/login', json={"username": "ghost", "password": "errada"})

    assert res.status_code == 401


def test_validate_token_com_token_valido_retorna_role(auth_app):
    client = auth_app.app.test_client()
    import jwt
    import datetime

    payload = {
        "user_id": 1,
        "username": "nicollas",
        "role": "admin",
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    }
    token = jwt.encode(payload, auth_app.JWT_SECRET, algorithm="HS256")

    res = client.post('/validate-token', json={"token": token})
    data = res.get_json()

    assert res.status_code == 200
    assert data['valid'] is True
    assert data['role'] == 'admin'


def test_validate_token_com_token_invalido_retorna_401(auth_app):
    client = auth_app.app.test_client()
    res = client.post('/validate-token', json={"token": "token.invalido.aqui"})
    assert res.status_code == 401
    assert res.get_json()['valid'] is False


def test_forgot_password_nao_revela_se_email_existe(auth_app):
    """Por segurança, a resposta deve ser a mesma genérica tanto para
    e-mail existente quanto inexistente (evita enumeração de usuários)."""
    client = auth_app.app.test_client()

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None  # e-mail não encontrado

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch('mysql.connector.connect', return_value=mock_conn):
        res = client.post('/forgot-password', json={"email": "naoexiste@example.com"})

    assert res.status_code == 200
    assert "e-mail existir" in res.get_json()['message'].lower()
