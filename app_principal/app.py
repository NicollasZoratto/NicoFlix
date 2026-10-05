import io
import os
import uuid
import datetime

import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
import mysql.connector
from minio import Minio
from minio.error import S3Error
from PIL import Image

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "chave_secreta_padrao_desenv")

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "6566259ba55415e75fcdaaec316a8be7")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth_service:5001")
LOG_SERVICE_URL = os.getenv("LOG_SERVICE_URL", "http://log_service:5002")

DB_HOST = os.getenv("DB_HOST", "35.226.64.52")
DB_USER = os.getenv("DB_USER", "IAC_2026_02_nicollas_carvalho")
DB_PASSWORD = os.getenv("DB_PASSWORD", "nico11as")
DB_NAME = os.getenv("DB_NAME", "IAC_2026_02_nicollas_carvalho")

# --- MinIO (Atividade 6: fotos de perfil) ---
# Endpoint INTERNO: usado pra upload/leitura de verdade, via rede Docker (rápido, não depende do host).
MINIO_INTERNAL_ENDPOINT = os.getenv("MINIO_INTERNAL_ENDPOINT", "minio:9000")
# Endpoint PÚBLICO: usado só pra MONTAR a URL pré-assinada, porque é o navegador do
# usuário (não o container) que vai baixar a imagem direto do MinIO.
MINIO_PUBLIC_ENDPOINT = os.getenv("MINIO_PUBLIC_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "nicoflix_admin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "nicoflix_minio_secret")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "nicoflix-perfis")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
PRESIGNED_URL_EXPIRY_SECONDS = int(os.getenv("PRESIGNED_URL_EXPIRY_SECONDS", "3600"))  # 1h

MAX_FOTO_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB
FORMATOS_AGEITOS = {"jpeg", "png", "webp"}

minio_client = Minio(
    MINIO_INTERNAL_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE,
)
# Cliente separado só pra assinar URLs com o host público embutido na assinatura.
minio_public_client = Minio(
    MINIO_PUBLIC_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE,
)


def get_db():
    return mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
    )


def init_app_tables():
    """Garante a existência das tabelas de favoritos, comentários e perfis."""
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS comentarios (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                movie_id INT NOT NULL,
                comentario TEXT NOT NULL,
                UNIQUE KEY user_movie (user_id, movie_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS favoritos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                movie_id INT NOT NULL,
                UNIQUE KEY user_movie_fav (user_id, movie_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS perfis (
                user_id INT PRIMARY KEY,
                nome_exibicao VARCHAR(100) NOT NULL,
                bio VARCHAR(280) NOT NULL DEFAULT '',
                foto_key VARCHAR(255),
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Erro ao inicializar tabelas da app principal: {e}")


def init_minio_bucket():
    """Garante que o bucket dedicado deste projeto existe no MinIO."""
    try:
        if not minio_client.bucket_exists(MINIO_BUCKET):
            minio_client.make_bucket(MINIO_BUCKET)
            print(f"[MINIO] Bucket '{MINIO_BUCKET}' criado.")
    except Exception as e:
        print(f"[MINIO ERROR] Falha ao preparar bucket '{MINIO_BUCKET}': {e}")


init_app_tables()
init_minio_bucket()


def log_event(usuario_id, acao):
    try:
        requests.post(
            f"{LOG_SERVICE_URL}/log",
            json={"usuario_id": usuario_id, "acao": acao, "ip": request.remote_addr},
            timeout=3
        )
    except Exception as e:
        print(f"[LOG WARNING] Não foi possível registrar evento '{acao}': {e}")


def get_current_user():
    token = session.get('jwt_token')
    if not token:
        return None
    try:
        res = requests.post(f"{AUTH_SERVICE_URL}/validate-token", json={"token": token}, timeout=5)
        if res.status_code == 200 and res.json().get('valid'):
            return res.json()
    except Exception as e:
        print(f"Erro ao validar token no Auth Service: {e}")
    return None


def fetch_tom_hanks_movies():
    url = f"https://api.themoviedb.org/3/person/31/movie_credits?api_key={TMDB_API_KEY}&language=pt-BR"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            cast = data.get('cast', [])
            return sorted(cast, key=lambda x: x.get('release_date', ''), reverse=True)
    except Exception as e:
        print(f"Erro TMDB: {e}")
    return []


def get_or_create_perfil(user_id, username_padrao):
    """Busca o perfil do usuário; cria um com valores padrão se ainda não existir."""
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM perfis WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "INSERT INTO perfis (user_id, nome_exibicao, bio, foto_key) VALUES (%s, %s, '', NULL)",
                (user_id, username_padrao)
            )
            db.commit()
            row = {"user_id": user_id, "nome_exibicao": username_padrao, "bio": "", "foto_key": None}
        cursor.close()
        db.close()
        return row
    except Exception as e:
        print(f"Erro ao buscar/criar perfil: {e}")
        return {"user_id": user_id, "nome_exibicao": username_padrao, "bio": "", "foto_key": None}


def gerar_url_foto(foto_key):
    """Gera uma URL pré-assinada (temporária) pra servir a foto de perfil
    direto do MinIO pro navegador, sem o bucket precisar ser público."""
    if not foto_key:
        return None
    try:
        return minio_public_client.presigned_get_object(
            MINIO_BUCKET, foto_key,
            expires=datetime.timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS)
        )
    except Exception as e:
        print(f"Erro ao gerar URL assinada da foto: {e}")
        return None


def _buscar_usuarios():
    try:
        res = requests.get(f"{AUTH_SERVICE_URL}/users", timeout=5)
        if res.status_code == 200:
            return {u['id']: u['username'] for u in res.json().get('users', [])}
    except Exception as e:
        print(f"Erro ao buscar usuários no Auth Service: {e}")
    return {}


_ACAO_LABELS = {
    'login': '🔓 Login',
    'logout': '🔒 Logout',
    'favoritar': '⭐ Favoritou',
    'desfavoritar': '☆ Desfavoritou',
    'editar_perfil': '📝 Editou o perfil',
    'upload_foto_perfil': '🖼️ Trocou a foto de perfil',
}


def _rotular_acao(acao_bruta):
    base = acao_bruta.split(':')[0]
    if base in _ACAO_LABELS:
        return _ACAO_LABELS[base]
    if base.startswith('403'):
        return f"🚫 Ação negada (403) — {acao_bruta}"
    if base.startswith('comentar'):
        return f"💬 Comentou — {acao_bruta}"
    if base.startswith('apagar_comentario'):
        return f"🗑️ Apagou comentário — {acao_bruta}"
    return acao_bruta


@app.route('/')
def index():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    user_id = user['user_id']
    movies = fetch_tom_hanks_movies()

    comentarios = {}
    favoritos = []
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT movie_id, comentario FROM comentarios WHERE user_id = %s", (user_id,))
        comentarios = {row['movie_id']: row['comentario'] for row in cursor.fetchall()}

        cursor.execute("SELECT movie_id FROM favoritos WHERE user_id = %s", (user_id,))
        favoritos = [row['movie_id'] for row in cursor.fetchall()]

        cursor.close()
        db.close()
    except Exception as e:
        print(f"Erro ao buscar dados do usuário: {e}")

    return render_template(
        'index.html',
        filmes=movies,
        comentarios=comentarios,
        favoritos=favoritos,
        usuario=user['username'],
        role=user.get('role', 'usuario')
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        try:
            res = requests.post(f"{AUTH_SERVICE_URL}/login", json={"username": username, "password": password}, timeout=5)
            if res.status_code == 200:
                data = res.json()
                session['pending_username'] = username
                dev_code = data.get('dev_code', '')
                flash(f"Código 2FA gerado: {dev_code}", "info")
                return redirect(url_for('verify_2fa_page'))
            else:
                err_msg = res.json().get('error', 'Erro ao realizar login')
                flash(err_msg, "danger")
        except Exception as e:
            flash(f"Erro de conexão com serviço de autenticação: {e}", "danger")

    return render_template('login.html')


@app.route('/cadastro', methods=['GET', 'POST'])
@app.route('/register', methods=['GET', 'POST'])
def cadastro():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        try:
            res = requests.post(
                f"{AUTH_SERVICE_URL}/register",
                json={"username": username, "email": email, "password": password},
                timeout=5
            )
            if res.status_code == 201:
                flash("Conta criada com sucesso! Faça login abaixo.", "success")
                return redirect(url_for('login'))
            else:
                err_msg = res.json().get('error', 'Erro ao cadastrar usuário')
                flash(err_msg, "danger")
        except Exception as e:
            flash(f"Erro de conexão com serviço de autenticação: {e}", "danger")

    return render_template('login.html')


@app.route('/verify-2fa', methods=['GET', 'POST'])
def verify_2fa_page():
    username = session.get('pending_username')
    if not username:
        return redirect(url_for('login'))

    if request.method == 'POST':
        code = request.form.get('code')
        try:
            res = requests.post(
                f"{AUTH_SERVICE_URL}/verify-2fa",
                json={"username": username, "code": code, "ip": request.remote_addr},
                timeout=5
            )
            if res.status_code == 200:
                data = res.json()
                session.pop('pending_username', None)
                session['jwt_token'] = data['token']
                return redirect(url_for('index'))
            else:
                err_msg = res.json().get('error', 'Código 2FA inválido')
                flash(err_msg, "danger")
        except Exception as e:
            flash(f"Erro ao validar 2FA: {e}", "danger")

    return render_template('verify_2fa.html', username=username)


@app.route('/esqueci-senha', methods=['GET', 'POST'])
def esqueci_senha():
    if request.method == 'POST':
        email = request.form.get('email')
        try:
            res = requests.post(f"{AUTH_SERVICE_URL}/forgot-password", json={"email": email}, timeout=5)
            if res.status_code == 200:
                flash(res.json().get('message', 'Se o e-mail existir, um link foi enviado.'), "success")
            else:
                flash(res.json().get('error', 'Erro ao solicitar redefinição de senha'), "danger")
        except Exception as e:
            flash(f"Erro de conexão com serviço de autenticação: {e}", "danger")
        return redirect(url_for('login'))

    return render_template('forgot_password.html')


@app.route('/reset-senha/<token>', methods=['GET', 'POST'])
def reset_senha(token):
    if request.method == 'POST':
        nova_senha = request.form.get('password')
        confirmar_senha = request.form.get('confirm_password')

        if nova_senha != confirmar_senha:
            flash("As senhas não coincidem.", "danger")
            return render_template('reset_password.html', token=token)

        try:
            res = requests.post(
                f"{AUTH_SERVICE_URL}/reset-password",
                json={"token": token, "new_password": nova_senha},
                timeout=5
            )
            if res.status_code == 200:
                flash("Senha redefinida com sucesso! Faça login com a nova senha.", "success")
                return redirect(url_for('login'))
            else:
                flash(res.json().get('error', 'Não foi possível redefinir a senha'), "danger")
                return render_template('reset_password.html', token=token)
        except Exception as e:
            flash(f"Erro de conexão com serviço de autenticação: {e}", "danger")
            return render_template('reset_password.html', token=token)

    try:
        res = requests.post(f"{AUTH_SERVICE_URL}/validate-reset-token", json={"token": token}, timeout=5)
        if res.status_code != 200 or not res.json().get('valid'):
            flash(res.json().get('error', 'Link inválido ou expirado. Solicite um novo.'), "danger")
            return redirect(url_for('esqueci_senha'))
    except Exception as e:
        flash(f"Erro ao validar link: {e}", "danger")
        return redirect(url_for('esqueci_senha'))

    return render_template('reset_password.html', token=token)


@app.route('/logout')
def logout():
    user = get_current_user()
    if user:
        log_event(user['user_id'], 'logout')
    session.clear()
    return redirect(url_for('login'))


@app.route('/comentar', methods=['POST'])
def comentar():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    movie_id = request.form.get('movie_id')
    comentario = request.form.get('comentario')

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO comentarios (user_id, movie_id, comentario)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE comentario = %s
        """, (user['user_id'], movie_id, comentario, comentario))
        db.commit()
        cursor.close()
        db.close()
        log_event(user['user_id'], f'comentar:filme_{movie_id}')
    except Exception as e:
        print(f"Erro ao salvar comentário: {e}")

    return redirect(url_for('index'))


@app.route('/comentar/excluir', methods=['POST'])
def excluir_comentario():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    movie_id = request.form.get('movie_id')
    target_user_id = request.form.get('user_id', user['user_id'])
    redirect_to = request.form.get('redirect_to', 'index')

    try:
        target_user_id = int(target_user_id)
    except (TypeError, ValueError):
        abort(400)

    is_own_comment = target_user_id == int(user['user_id'])
    is_admin = user.get('role') == 'admin'

    if not is_own_comment and not is_admin:
        log_event(user['user_id'], f'403_apagar_comentario_negado:filme_{movie_id}')
        abort(403)

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "DELETE FROM comentarios WHERE user_id = %s AND movie_id = %s",
            (target_user_id, movie_id)
        )
        db.commit()
        cursor.close()
        db.close()
        acao = 'apagar_comentario_proprio' if is_own_comment else 'apagar_comentario_moderacao'
        log_event(user['user_id'], f'{acao}:filme_{movie_id}:usuario_{target_user_id}')
    except Exception as e:
        print(f"Erro ao excluir comentário: {e}")

    if redirect_to == 'admin' and is_admin:
        return redirect(url_for('admin_comentarios'))
    return redirect(url_for('index'))


@app.route('/admin/comentarios')
def admin_comentarios():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if user.get('role') != 'admin':
        log_event(user['user_id'], '403_acesso_moderacao_negado')
        abort(403)

    movies = fetch_tom_hanks_movies()
    titulos_filmes = {m['id']: m.get('title', f"Filme #{m['id']}") for m in movies}
    usuarios_por_id = _buscar_usuarios()

    comentarios = []
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, user_id, movie_id, comentario FROM comentarios ORDER BY movie_id, user_id")
        for row in cursor.fetchall():
            comentarios.append({
                "user_id": row['user_id'],
                "username": usuarios_por_id.get(row['user_id'], f"Usuário #{row['user_id']}"),
                "movie_id": row['movie_id'],
                "titulo_filme": titulos_filmes.get(row['movie_id'], f"Filme #{row['movie_id']}"),
                "comentario": row['comentario'],
            })
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Erro ao buscar comentários para moderação: {e}")

    return render_template('admin_comentarios.html', comentarios=comentarios, usuario=user['username'])


@app.route('/admin/logs')
def admin_logs():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if user.get('role') != 'admin':
        log_event(user['user_id'], '403_acesso_logs_negado')
        abort(403)

    usuarios_por_id = _buscar_usuarios()
    eventos = []
    try:
        res = requests.get(f"{LOG_SERVICE_URL}/events", params={"limit": 100}, timeout=5)
        if res.status_code == 200:
            for e in res.json().get('events', []):
                uid = e.get('usuario_id')
                try:
                    uid_int = int(uid)
                except (TypeError, ValueError):
                    uid_int = uid
                eventos.append({
                    "usuario": usuarios_por_id.get(uid_int, f"Usuário #{uid}"),
                    "acao": _rotular_acao(e.get('acao', '')),
                    "timestamp": e.get('timestamp', ''),
                    "ip": e.get('ip', ''),
                })
        else:
            flash("Não foi possível consultar o serviço de logs.", "danger")
    except Exception as e:
        flash(f"Erro de conexão com o serviço de logs: {e}", "danger")

    return render_template('admin_logs.html', eventos=eventos, usuario=user['username'])


@app.route('/favoritar', methods=['POST'])
def favoritar():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    movie_id = request.form.get('movie_id')

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT id FROM favoritos WHERE user_id = %s AND movie_id = %s", (user['user_id'], movie_id))
        existente = cursor.fetchone()

        if existente:
            cursor.execute("DELETE FROM favoritos WHERE id = %s", (existente[0],))
            acao = 'desfavoritar'
        else:
            cursor.execute("INSERT INTO favoritos (user_id, movie_id) VALUES (%s, %s)", (user['user_id'], movie_id))
            acao = 'favoritar'

        db.commit()
        cursor.close()
        db.close()
        log_event(user['user_id'], f'{acao}:filme_{movie_id}')
    except Exception as e:
        print(f"Erro ao favoritar: {e}")

    return redirect(url_for('index'))


@app.route('/perfil')
def perfil():
    """Página de perfil do usuário logado: nome, bio, foto e os filmes
    favoritados (reaproveitando a tabela 'favoritos' desde a atividade 2)."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    dados_perfil = get_or_create_perfil(user['user_id'], user['username'])
    foto_url = gerar_url_foto(dados_perfil.get('foto_key'))

    favoritos_filmes = []
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT movie_id FROM favoritos WHERE user_id = %s", (user['user_id'],))
        ids_favoritos = {row['movie_id'] for row in cursor.fetchall()}
        cursor.close()
        db.close()

        if ids_favoritos:
            for m in fetch_tom_hanks_movies():
                if m['id'] in ids_favoritos:
                    favoritos_filmes.append(m)
    except Exception as e:
        print(f"Erro ao buscar favoritos do perfil: {e}")

    return render_template(
        'perfil.html',
        usuario=user['username'],
        role=user.get('role', 'usuario'),
        perfil=dados_perfil,
        foto_url=foto_url,
        favoritos_filmes=favoritos_filmes,
        meu_user_id=user['user_id'],
    )


@app.route('/perfil/<int:user_id>/editar', methods=['POST'])
def editar_perfil(user_id):
    """Edita nome de exibição e bio. Só o dono do perfil pode editar —
    o backend confere a identidade de quem está logado (user['user_id']
    vindo do JWT validado pelo auth_service), e NUNCA confia no user_id
    que vier no corpo da requisição além do que está na própria URL."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    if user_id != int(user['user_id']):
        log_event(user['user_id'], f'403_editar_perfil_negado:alvo_{user_id}')
        abort(403)

    nome_exibicao = (request.form.get('nome_exibicao') or user['username']).strip()[:100]
    bio = (request.form.get('bio') or '').strip()[:280]

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO perfis (user_id, nome_exibicao, bio)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE nome_exibicao = VALUES(nome_exibicao), bio = VALUES(bio)
        """, (user_id, nome_exibicao, bio))
        db.commit()
        cursor.close()
        db.close()
        log_event(user['user_id'], 'editar_perfil')
        flash("Perfil atualizado!", "success")
    except Exception as e:
        print(f"Erro ao editar perfil: {e}")
        flash("Erro ao atualizar o perfil.", "danger")

    return redirect(url_for('perfil'))


@app.route('/perfil/<int:user_id>/foto', methods=['POST'])
def upload_foto_perfil(user_id):
    """Upload da foto de perfil pro MinIO. Mesma checagem de identidade
    da edição de perfil: só o dono pode trocar a própria foto, mesmo
    manipulando o user_id na requisição."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    if user_id != int(user['user_id']):
        log_event(user['user_id'], f'403_upload_foto_negado:alvo_{user_id}')
        abort(403)

    arquivo = request.files.get('foto')
    if not arquivo or arquivo.filename == '':
        flash("Selecione uma imagem para enviar.", "danger")
        return redirect(url_for('perfil'))

    conteudo = arquivo.read()

    if len(conteudo) > MAX_FOTO_SIZE_BYTES:
        flash("Imagem maior que o limite de 2 MB.", "danger")
        return redirect(url_for('perfil'))

    # Não confia no Content-Type que o navegador mandou — abre a imagem de
    # verdade com Pillow pra confirmar que é um arquivo de imagem válido.
    try:
        img = Image.open(io.BytesIO(conteudo))
        img.verify()
        formato = (img.format or '').lower()
    except Exception:
        flash("O arquivo enviado não é uma imagem válida.", "danger")
        return redirect(url_for('perfil'))

    if formato not in FORMATOS_AGEITOS:
        flash("Formato não suportado. Envie uma imagem JPEG, PNG ou WEBP.", "danger")
        return redirect(url_for('perfil'))

    extensao = 'jpg' if formato == 'jpeg' else formato
    object_key = f"perfis/{user_id}/{uuid.uuid4().hex}.{extensao}"
    content_type = f"image/{formato}"

    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT foto_key FROM perfis WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        foto_antiga = row['foto_key'] if row else None

        minio_client.put_object(
            MINIO_BUCKET, object_key, io.BytesIO(conteudo),
            length=len(conteudo), content_type=content_type
        )

        cursor.execute("""
            INSERT INTO perfis (user_id, nome_exibicao, bio, foto_key)
            VALUES (%s, %s, '', %s)
            ON DUPLICATE KEY UPDATE foto_key = VALUES(foto_key)
        """, (user_id, user['username'], object_key))
        db.commit()
        cursor.close()
        db.close()

        if foto_antiga:
            try:
                minio_client.remove_object(MINIO_BUCKET, foto_antiga)
            except Exception as e:
                print(f"Aviso: não foi possível remover a foto antiga '{foto_antiga}': {e}")

        log_event(user['user_id'], 'upload_foto_perfil')
        flash("Foto de perfil atualizada!", "success")
    except S3Error as e:
        print(f"Erro MinIO ao enviar foto: {e}")
        flash("Erro ao enviar a imagem pro armazenamento. Tente novamente.", "danger")
    except Exception as e:
        print(f"Erro ao salvar foto de perfil: {e}")
        flash("Erro ao atualizar a foto de perfil.", "danger")

    return redirect(url_for('perfil'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
