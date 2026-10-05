import io
from unittest.mock import patch, MagicMock

from PIL import Image


def _fake_user(user_id=1, username="nicollas", role="usuario"):
    return {"valid": True, "user_id": user_id, "username": username, "role": role}


def _tiny_png_bytes():
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), color=(255, 0, 0)).save(buf, format='PNG')
    return buf.getvalue()


# --- Núcleo da atividade 6: cada um só edita o próprio perfil ---

def test_editar_perfil_de_outro_usuario_retorna_403(catalog_app):
    """Usuário logado como id=1 tentando editar o perfil do id=2 (ID
    manipulado na URL/requisição) deve ser recusado, mesmo direto no
    endpoint, sem passar pela tela."""
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(user_id=1)), \
         patch.object(catalog_app, 'log_event') as mock_log:
        res = client.post('/perfil/2/editar', data={"nome_exibicao": "Hacker", "bio": "invadido"})

    assert res.status_code == 403
    mock_log.assert_called_once()
    assert '403_editar_perfil_negado' in mock_log.call_args[0][1]


def test_editar_o_proprio_perfil_e_permitido(catalog_app):
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(user_id=1)), \
         patch.object(catalog_app, 'log_event') as mock_log, \
         patch('mysql.connector.connect') as mock_connect:
        mock_connect.return_value.cursor.return_value = MagicMock()
        res = client.post('/perfil/1/editar', data={"nome_exibicao": "Nicollas Z.", "bio": "Fã de Tom Hanks"})

    assert res.status_code == 302
    assert '/perfil' in res.headers['Location']
    mock_log.assert_called_once_with(1, 'editar_perfil')


def test_upload_foto_de_outro_usuario_retorna_403(catalog_app):
    """Mesma checagem de identidade vale pro upload de foto."""
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(user_id=1)), \
         patch.object(catalog_app, 'log_event') as mock_log:
        res = client.post(
            '/perfil/2/foto',
            data={"foto": (io.BytesIO(_tiny_png_bytes()), "foto.png")},
            content_type='multipart/form-data'
        )

    assert res.status_code == 403
    mock_log.assert_called_once()
    assert '403_upload_foto_negado' in mock_log.call_args[0][1]


# --- Validação de arquivo (tipo e tamanho) ---

def test_upload_rejeita_arquivo_que_nao_e_imagem(catalog_app):
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(user_id=1)):
        res = client.post(
            '/perfil/1/foto',
            data={"foto": (io.BytesIO(b"isso aqui nao e uma imagem de verdade"), "arquivo.png")},
            content_type='multipart/form-data'
        )

    assert res.status_code == 302  # redireciona de volta pro perfil com flash de erro
    catalog_app.minio_client.put_object.assert_not_called()


def test_upload_rejeita_arquivo_maior_que_limite(catalog_app):
    client = catalog_app.app.test_client()
    conteudo_grande = _tiny_png_bytes() + (b"\x00" * (catalog_app.MAX_FOTO_SIZE_BYTES + 1))

    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(user_id=1)):
        res = client.post(
            '/perfil/1/foto',
            data={"foto": (io.BytesIO(conteudo_grande), "grande.png")},
            content_type='multipart/form-data'
        )

    assert res.status_code == 302
    catalog_app.minio_client.put_object.assert_not_called()


def test_upload_de_imagem_valida_salva_no_minio_e_no_banco(catalog_app):
    client = catalog_app.app.test_client()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"foto_key": None}

    with patch.object(catalog_app, 'get_current_user', return_value=_fake_user(user_id=1)), \
         patch.object(catalog_app, 'log_event') as mock_log, \
         patch('mysql.connector.connect') as mock_connect:
        mock_connect.return_value.cursor.return_value = mock_cursor

        res = client.post(
            '/perfil/1/foto',
            data={"foto": (io.BytesIO(_tiny_png_bytes()), "avatar.png")},
            content_type='multipart/form-data'
        )

    assert res.status_code == 302
    catalog_app.minio_client.put_object.assert_called_once()
    bucket_usado = catalog_app.minio_client.put_object.call_args[0][0]
    assert bucket_usado == catalog_app.MINIO_BUCKET
    mock_log.assert_called_once_with(1, 'upload_foto_perfil')


def test_perfil_redireciona_para_login_se_nao_autenticado(catalog_app):
    client = catalog_app.app.test_client()
    with patch.object(catalog_app, 'get_current_user', return_value=None):
        res = client.get('/perfil')
    assert res.status_code == 302
    assert '/login' in res.headers['Location']
