import os
import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
import mysql.connector

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "chave_secreta_padrao_desenv")

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "6566259ba55415e75fcdaaec316a8be7")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth_service:5001")

DB_HOST = os.getenv("DB_HOST", "35.226.64.52")
DB_USER = os.getenv("DB_USER", "IAC_2026_02_nicollas_carvalho")
DB_PASSWORD = os.getenv("DB_PASSWORD", "nico11as")
DB_NAME = os.getenv("DB_NAME", "IAC_2026_02_nicollas_carvalho")


def get_db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )


def init_app_tables():
    """Garante a existência das tabelas de favoritos e comentários."""
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
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Erro ao inicializar tabelas da app principal: {e}")


init_app_tables()


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
            res = requests.post(f"{AUTH_SERVICE_URL}/verify-2fa", json={"username": username, "code": code}, timeout=5)
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

    # GET: valida o token antes de mostrar o formulário
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
    except Exception as e:
        print(f"Erro ao salvar comentário: {e}")

    return redirect(url_for('index'))


@app.route('/comentar/excluir', methods=['POST'])
def excluir_comentario():
    """Apaga um comentário. Usuário comum só pode apagar o PRÓPRIO
    comentário; apagar o comentário de outra pessoa é ação exclusiva de
    admin. A checagem é sempre feita aqui no backend, usando o papel
    (role) que o auth_service validou e devolveu no token — então nem
    chamando este endpoint direto pelo Postman/curl dá pra burlar."""
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
        # 403: usuário comum tentando apagar comentário de outra pessoa
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
    except Exception as e:
        print(f"Erro ao excluir comentário: {e}")

    if redirect_to == 'admin' and is_admin:
        return redirect(url_for('admin_comentarios'))
    return redirect(url_for('index'))


@app.route('/admin/comentarios')
def admin_comentarios():
    """Painel de moderação: lista todos os comentários de todos os
    usuários, para o admin poder apagar qualquer um. Ação exclusiva de
    admin — usuário comum recebe 403, mesmo acessando a URL direto."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if user.get('role') != 'admin':
        abort(403)

    movies = fetch_tom_hanks_movies()
    titulos_filmes = {m['id']: m.get('title', f"Filme #{m['id']}") for m in movies}

    usuarios_por_id = {}
    try:
        res = requests.get(f"{AUTH_SERVICE_URL}/users", timeout=5)
        if res.status_code == 200:
            usuarios_por_id = {u['id']: u['username'] for u in res.json().get('users', [])}
    except Exception as e:
        print(f"Erro ao buscar usuários no Auth Service: {e}")

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
        else:
            cursor.execute("INSERT INTO favoritos (user_id, movie_id) VALUES (%s, %s)", (user['user_id'], movie_id))

        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Erro ao favoritar: {e}")

    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
