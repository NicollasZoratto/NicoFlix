from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import os

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'chave_secreta_padrao_desenv')

# Credenciais do Banco via Variáveis de Ambiente (sem hardcode)
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '35.226.64.52'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'IAC_2026_02_nicollas_carvalho'),
    'password': os.getenv('DB_PASSWORD', 'nico11as'),
    'database': os.getenv('DB_NAME', 'IAC_2026_02_nicollas_carvalho')
}

# Chave do TMDB via Variável de Ambiente
TMDB_API_KEY = os.getenv('TMDB_API_KEY', '')

def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

def inicializar_banco():
    """Cria as tabelas caso não existam."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INT AUTO_INCREMENT PRIMARY KEY,
                usuario VARCHAR(50) NOT NULL UNIQUE,
                senha VARCHAR(255) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        
        # Tabela para isolamento de Favoritos e Comentários por Usuário
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS favoritos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                usuario_id INT NOT NULL,
                tmdb_id INT NOT NULL,
                comentario TEXT,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
                UNIQUE KEY unique_user_movie (usuario_id, tmdb_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Erro ao inicializar o banco: {e}")

# Consumo de API do TMDB (Busca ID do Tom Hanks e depois sua filmografia)
def buscar_filmes_tom_hanks():
    if not TMDB_API_KEY:
        return []
    try:
        # Busca o person_id de Tom Hanks
        search_url = f"https://api.themoviedb.org/3/search/person?api_key={TMDB_API_KEY}&query=Tom+Hanks"
        res_search = requests.get(search_url)
        if res_search.status_code == 200:
            results = res_search.json().get('results', [])
            if results:
                person_id = results[0]['id']
                # Busca os filmes do Tom Hanks
                credits_url = f"https://api.themoviedb.org/3/person/{person_id}/movie_credits?api_key={TMDB_API_KEY}&language=pt-BR"
                res_credits = requests.get(credits_url)
                if res_credits.status_code == 200:
                    filmes = res_credits.json().get('cast', [])
                    return sorted(filmes, key=lambda x: x.get('popularity', 0), reverse=True)[:18]
    except Exception as e:
        print(f"Erro no TMDB: {e}")
    return []

@app.route("/login", methods=["POST"])
def login():
    usuario = request.form.get("usuario")
    senha = request.form.get("senha")

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM usuarios WHERE usuario = %s", (usuario,))
        user_record = cursor.fetchone()
        cursor.close()
        conn.close()

        if user_record and check_password_hash(user_record["senha"], senha):
            session["usuario_id"] = user_record["id"]
            session["usuario"] = user_record["usuario"]
            return redirect(url_for("index"))
        else:
            flash("Usuário ou senha incorretos.", "danger")
    except Exception as e:
        flash(f"Erro de conexão com o banco: {e}", "danger")

    return redirect(url_for("auth_page"))

@app.route("/register", methods=["POST"])
def register():
    usuario = request.form.get("usuario")
    senha = request.form.get("senha")

    if usuario and senha:
        senha_hash = generate_password_hash(senha, method='pbkdf2:sha256')
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO usuarios (usuario, senha) VALUES (%s, %s)", (usuario, senha_hash))
            conn.commit()
            cursor.close()
            conn.close()
            flash("Conta criada com sucesso! Faça login.", "success")
            return redirect(url_for("auth_page"))
        except mysql.connector.Error as err:
            if err.errno == 1062:
                flash("Este nome de usuário já existe.", "danger")
            else:
                flash(f"Erro MySQL: {err.msg}", "danger")

    return redirect(url_for("auth_page"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth_page"))

@app.route("/auth")
def auth_page():
    if "usuario_id" in session:
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/", methods=["GET", "POST"])
def index():
    if "usuario_id" not in session:
        return redirect(url_for("auth_page"))

    usuario_id = session["usuario_id"]

    if request.method == "POST":
        tmdb_id = request.form.get("tmdb_id")
        comentario = request.form.get("comentario")

        if tmdb_id:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO favoritos (usuario_id, tmdb_id, comentario)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE comentario = VALUES(comentario)
                """, (usuario_id, int(tmdb_id), comentario))
                conn.commit()
                cursor.close()
                conn.close()
                flash("Favorito/comentário salvo com sucesso!", "success")
            except Exception as e:
                flash(f"Erro ao salvar: {e}", "danger")

    filmes_tmdb = buscar_filmes_tom_hanks()

    # Busca os favoritos isolados do usuário logado
    favoritos_usuario = {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT tmdb_id, comentario FROM favoritos WHERE usuario_id = %s", (usuario_id,))
        for row in cursor.fetchall():
            favoritos_usuario[row['tmdb_id']] = row['comentario']
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Erro ao carregar favoritos: {e}")

    return render_template("index.html", filmes=filmes_tmdb, favoritos=favoritos_usuario, usuario=session["usuario"])

if __name__ == "__main__":
    inicializar_banco()
    port = int(os.environ.get("PORT", 80))
    app.run(host="0.0.0.0", port=port)