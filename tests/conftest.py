import importlib.util
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module(unique_name, file_path):
    """Carrega um app.py como um módulo com nome único, evitando colisão
    no sys.modules (as três apps se chamam literalmente 'app.py').
    Registra em sys.modules ANTES de executar, porque o Flask usa
    sys.modules para descobrir o root_path (e assim achar templates/)."""
    spec = importlib.util.spec_from_file_location(unique_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def auth_app():
    """Importa o auth_service/app.py com o mysql.connector mockado, pra
    não tentar abrir conexão real com o MariaDB remoto durante o import
    (o init_db() roda automaticamente ao carregar o módulo)."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch('mysql.connector.connect', return_value=mock_conn):
        module = _load_module(
            'nicoflix_auth_service_app',
            os.path.join(BASE_DIR, 'auth_service', 'app.py')
        )
    return module


@pytest.fixture
def catalog_app():
    """Importa o app_principal/app.py com o mysql.connector mockado (o
    init_app_tables() também roda no import)."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch('mysql.connector.connect', return_value=mock_conn):
        module = _load_module(
            'nicoflix_app_principal_app',
            os.path.join(BASE_DIR, 'app_principal', 'app.py')
        )
    return module


@pytest.fixture
def log_app():
    """O log_service não faz nada com Redis no import (conexão é lazy),
    então pode ser importado direto."""
    module = _load_module(
        'nicoflix_log_service_app',
        os.path.join(BASE_DIR, 'log_service', 'app.py')
    )
    return module
