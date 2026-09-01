import os
import random
import secrets
import smtplib
import datetime
from email.mime.text import MIMEText

import jwt
import mysql.connector
from flask import Flask, request, jsonify

app = Flask(__name__)

DB_HOST = os.getenv("DB_HOST", "35.226.64.52")
DB_USER = os.getenv("DB_USER", "IAC_2026_02_nicollas_carvalho")
DB_PASSWORD = os.getenv("DB_PASSWORD", "nico11as")
DB_NAME = os.getenv("DB_NAME", "IAC_2026_02_nicollas_carvalho")
JWT_SECRET = os.getenv("JWT_SECRET", "chave_secreta_jwt_super_segura")

# URL pública do catálogo (usada para montar o link de redefinição de senha)
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "http://localhost:8223")

# Configuração de e-mail (Mailtrap em dev, Brevo em produção)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "2525"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "no-reply@nicoflix.local")

RESET_TOKEN_TTL_MINUTES = 30

codes_2fa = {}


def get_db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )


def init_db():
    """Cria (ou recria) as tabelas usadas pelo serviço de autenticação."""
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        cursor.execute("DROP TABLE IF EXISTS usuarios")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        cursor.execute("""
            CREATE TABLE usuarios (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'usuario',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # reset_tokens não é derrubada no restart para não invalidar links em teste,
        # mas é criada aqui caso ainda não exista.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reset_tokens (
                id INT AUTO_INCREMENT PRIMARY KEY,
                token VARCHAR(64) UNIQUE NOT NULL,
                usuario_id INT NOT NULL,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expira_em TIMESTAMP NOT NULL,
                usado TINYINT(1) NOT NULL DEFAULT 0,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
            )
        """)
        db.commit()
        cursor.close()
        db.close()
        print("[DB SUCCESS] Tabelas 'usuarios' e 'reset_tokens' prontas.")
    except Exception as e:
        print(f"[DB ERROR] Falha ao preparar tabelas: {e}")


init_db()


def send_email(to_email, subject, body):
    """Envia e-mail via SMTP (Mailtrap/Brevo). Se não houver credenciais
    configuradas, apenas loga o conteúdo no console (modo dev)."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print("[EMAIL - MODO DEV, SMTP NÃO CONFIGURADO] Para:", to_email)
        print(body)
        return True

    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = SMTP_FROM
        msg['To'] = to_email

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] Falha ao enviar e-mail para {to_email}: {e}")
        return False


@app.route('/register', methods=['POST'])
def register_user():
    data = request.get_json() or {}
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    if not username or not email or not password:
        return jsonify({"error": "Usuário, e-mail e senha são obrigatórios"}), 400

    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM usuarios WHERE username = %s OR email = %s", (username, email))
        if cursor.fetchone():
            cursor.close()
            db.close()
            return jsonify({"error": "Usuário ou e-mail já cadastrado"}), 409

        # o primeiro usuário cadastrado vira admin, os demais são 'usuario'
        cursor.execute("SELECT COUNT(*) as total FROM usuarios")
        total = cursor.fetchone()['total']
        role = 'admin' if total == 0 else 'usuario'

        cursor.execute(
            "INSERT INTO usuarios (username, email, password, role) VALUES (%s, %s, %s, %s)",
            (username, email, password, role)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"message": "Usuário registrado com sucesso", "role": role}), 201
    except Exception as e:
        print(f"Erro no cadastro: {e}")
        return jsonify({"error": f"Erro interno ao cadastrar: {str(e)}"}), 500


@app.route('/login', methods=['POST'])
def login_user():
    data = request.get_json() or {}
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({"error": "Informe usuário e senha"}), 400

    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, username, password FROM usuarios WHERE username = %s AND password = %s", (username, password))
        user = cursor.fetchone()
        cursor.close()
        db.close()

        if not user:
            return jsonify({"error": "Usuário ou senha incorretos"}), 401

        code = str(random.randint(100000, 999999))
        codes_2fa[username] = code
        print(f"[2FA LOG] Código gerado para {username}: {code}")

        return jsonify({
            "message": "Credenciais válidas",
            "dev_code": code
        }), 200
    except Exception as e:
        print(f"Erro no login: {e}")
        return jsonify({"error": f"Erro interno no login: {str(e)}"}), 500


@app.route('/verify-2fa', methods=['POST'])
def process_verify_2fa():
    data = request.get_json() or {}
    username = data.get('username')
    code = data.get('code')

    if not username or not code:
        return jsonify({"error": "Dados de 2FA incompletos"}), 400

    expected_code = codes_2fa.get(username)
    if not expected_code or str(expected_code).strip() != str(code).strip():
        return jsonify({"error": "Código 2FA inválido ou expirado"}), 400

    codes_2fa.pop(username, None)

    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, username, role FROM usuarios WHERE username = %s", (username,))
        user = cursor.fetchone()
        cursor.close()
        db.close()

        if not user:
            return jsonify({"error": "Usuário não encontrado"}), 404

        payload = {
            "user_id": user['id'],
            "username": user['username'],
            "role": user['role'],
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=2)
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")

        return jsonify({"token": token, "username": user['username'], "role": user['role']}), 200
    except Exception as e:
        print(f"Erro no verify-2fa: {e}")
        return jsonify({"error": f"Erro ao gerar JWT: {str(e)}"}), 500


@app.route('/validate-token', methods=['POST'])
def validate_token():
    data = request.get_json() or {}
    token = data.get('token')

    if not token:
        return jsonify({"valid": False, "error": "Token ausente"}), 400

    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return jsonify({
            "valid": True,
            "user_id": decoded["user_id"],
            "username": decoded["username"],
            "role": decoded.get("role", "usuario")
        }), 200
    except jwt.ExpiredSignatureError:
        return jsonify({"valid": False, "error": "Token expirado"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"valid": False, "error": "Token inválido"}), 401


@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json() or {}
    email = data.get('email')

    if not email:
        return jsonify({"error": "Informe o e-mail"}), 400

    generic_response = {"message": "Se o e-mail existir em nossa base, um link de redefinição foi enviado."}

    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, username FROM usuarios WHERE email = %s", (email,))
        user = cursor.fetchone()

        if not user:
            # Não revela se o e-mail existe ou não (evita enumeração de usuários)
            cursor.close()
            db.close()
            return jsonify(generic_response), 200

        token = secrets.token_urlsafe(32)
        expira_em = datetime.datetime.utcnow() + datetime.timedelta(minutes=RESET_TOKEN_TTL_MINUTES)

        cursor.execute(
            "INSERT INTO reset_tokens (token, usuario_id, expira_em) VALUES (%s, %s, %s)",
            (token, user['id'], expira_em)
        )
        db.commit()
        cursor.close()
        db.close()

        link = f"{PUBLIC_APP_URL}/reset-senha/{token}"
        corpo = (
            f"Olá, {user['username']}!\n\n"
            f"Recebemos uma solicitação para redefinir sua senha no NicoFlix.\n"
            f"Clique no link abaixo para criar uma nova senha. Ele expira em {RESET_TOKEN_TTL_MINUTES} minutos:\n\n"
            f"{link}\n\n"
            f"Se você não pediu isso, pode ignorar este e-mail."
        )
        send_email(email, "NicoFlix - Redefinição de senha", corpo)

        return jsonify(generic_response), 200
    except Exception as e:
        print(f"Erro no forgot-password: {e}")
        return jsonify({"error": f"Erro interno: {str(e)}"}), 500


@app.route('/validate-reset-token', methods=['POST'])
def validate_reset_token():
    data = request.get_json() or {}
    token = data.get('token')

    if not token:
        return jsonify({"valid": False, "error": "Token ausente"}), 400

    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM reset_tokens WHERE token = %s", (token,))
        row = cursor.fetchone()
        cursor.close()
        db.close()

        if not row:
            return jsonify({"valid": False, "error": "Link inválido"}), 404
        if row['usado']:
            return jsonify({"valid": False, "error": "Este link já foi utilizado"}), 400
        if row['expira_em'] < datetime.datetime.utcnow():
            return jsonify({"valid": False, "error": "Link expirado. Solicite um novo."}), 400

        return jsonify({"valid": True}), 200
    except Exception as e:
        print(f"Erro no validate-reset-token: {e}")
        return jsonify({"valid": False, "error": f"Erro interno: {str(e)}"}), 500


@app.route('/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json() or {}
    token = data.get('token')
    new_password = data.get('new_password')

    if not token or not new_password:
        return jsonify({"error": "Dados incompletos"}), 400

    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM reset_tokens WHERE token = %s", (token,))
        row = cursor.fetchone()

        if not row:
            cursor.close()
            db.close()
            return jsonify({"error": "Link inválido"}), 404
        if row['usado']:
            cursor.close()
            db.close()
            return jsonify({"error": "Este link já foi utilizado"}), 400
        if row['expira_em'] < datetime.datetime.utcnow():
            cursor.close()
            db.close()
            return jsonify({"error": "Link expirado. Solicite um novo."}), 400

        cursor.execute("UPDATE usuarios SET password = %s WHERE id = %s", (new_password, row['usuario_id']))
        cursor.execute("UPDATE reset_tokens SET usado = 1 WHERE id = %s", (row['id'],))
        db.commit()
        cursor.close()
        db.close()

        return jsonify({"message": "Senha redefinida com sucesso"}), 200
    except Exception as e:
        print(f"Erro no reset-password: {e}")
        return jsonify({"error": f"Erro interno: {str(e)}"}), 500


@app.route('/users', methods=['GET'])
def list_users():
    """Lista básica de usuários (id, username, role), usada pelo catálogo
    para exibir nomes no painel de moderação. Não expõe e-mail nem senha.
    Só acessível dentro da rede interna do Docker (auth_service não publica porta)."""
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, username, role FROM usuarios")
        users = cursor.fetchall()
        cursor.close()
        db.close()
        return jsonify({"users": users}), 200
    except Exception as e:
        print(f"Erro ao listar usuários: {e}")
        return jsonify({"error": f"Erro interno: {str(e)}"}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
