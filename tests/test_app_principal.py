from unittest.mock import patch, MagicMock


def _fake_user(user_id=1, username="usuario_comum", role="usuario"):
    return {"valid": True, "user_id": user_id, "username": username, "role": role}


def test_index_redireciona_para_login_se_nao_autenticado(catalog_app):
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=None):
        res = client.get('/')
    assert res.status_code == 302
    assert '/login' in res.headers['Location']


def test_admin_comentarios_retorna_403_para_usuario_comum(catalog_app):
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(role="usuario")), \
         patch.object(catalog_app, 'log_event') as mock_log:
        res = client.get('/admin/comentarios')

    assert res.status_code == 403
    # a tentativa negada precisa ser logada, pra atividade de auditoria
    mock_log.assert_called_once()
    assert '403' in mock_log.call_args[0][1]


def test_admin_comentarios_funciona_para_admin(catalog_app):
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(role="admin")), \
         patch.object(catalog_app, 'fetch_tom_hanks_movies', return_value=[]), \
         patch.object(catalog_app, '_buscar_usuarios', return_value={}), \
         patch('mysql.connector.connect') as mock_connect:
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_connect.return_value.cursor.return_value = mock_cursor

        res = client.get('/admin/comentarios')

    assert res.status_code == 200


def test_admin_logs_retorna_403_para_usuario_comum(catalog_app):
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(role="usuario")), \
         patch.object(catalog_app, 'log_event') as mock_log:
        res = client.get('/admin/logs')

    assert res.status_code == 403
    mock_log.assert_called_once_with(1, '403_acesso_logs_negado')


def test_excluir_comentario_de_outro_usuario_retorna_403_para_usuario_comum(catalog_app):
    """Núcleo da atividade 4: um usuário comum tentando apagar o
    comentário de OUTRO usuário deve ser recusado com 403 — mesmo indo
    direto no endpoint, sem passar pelo botão de moderação."""
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(user_id=1, role="usuario")), \
         patch.object(catalog_app, 'log_event'):
        res = client.post('/comentar/excluir', data={"movie_id": "42", "user_id": "999"})

    assert res.status_code == 403


def test_excluir_o_proprio_comentario_e_permitido_para_usuario_comum(catalog_app):
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(user_id=1, role="usuario")), \
         patch.object(catalog_app, 'log_event') as mock_log, \
         patch('mysql.connector.connect') as mock_connect:
        mock_connect.return_value.cursor.return_value = MagicMock()

        res = client.post('/comentar/excluir', data={"movie_id": "42", "user_id": "1"})

    assert res.status_code == 302
    mock_log.assert_called_once()
    assert 'apagar_comentario_proprio' in mock_log.call_args[0][1]


def test_admin_pode_excluir_comentario_de_outro_usuario(catalog_app):
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(user_id=1, role="admin")), \
         patch.object(catalog_app, 'log_event') as mock_log, \
         patch('mysql.connector.connect') as mock_connect:
        mock_connect.return_value.cursor.return_value = MagicMock()

        res = client.post('/comentar/excluir', data={
            "movie_id": "42", "user_id": "999", "redirect_to": "admin"
        })

    assert res.status_code == 302
    assert '/admin/comentarios' in res.headers['Location']
    assert 'apagar_comentario_moderacao' in mock_log.call_args[0][1]


def test_favoritar_alterna_e_gera_log(catalog_app):
    client = catalog_app.app.test_client()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None  # ainda não é favorito -> vai inserir

    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user()), \
         patch.object(catalog_app, 'log_event') as mock_log, \
         patch('mysql.connector.connect') as mock_connect:
        mock_connect.return_value.cursor.return_value = mock_cursor

        res = client.post('/favoritar', data={"movie_id": "42"})

    assert res.status_code == 302
    mock_log.assert_called_once_with(1, 'favoritar:filme_42')
