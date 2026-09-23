import os
import datetime

import redis
from flask import Flask, request, jsonify

app = Flask(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
STREAM_KEY = os.getenv("LOG_STREAM_KEY", "nicoflix:logs")

# Tamanho máximo do stream (Redis Streams suportam trim automático via
# MAXLEN, evitando crescimento infinito de memória em produção).
STREAM_MAXLEN = int(os.getenv("LOG_STREAM_MAXLEN", "10000"))

_redis_client = None


def get_redis():
    """Conexão Redis reaproveitada entre requisições (lazy singleton)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    return _redis_client


@app.route('/health', methods=['GET'])
def health():
    try:
        get_redis().ping()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 503


@app.route('/log', methods=['POST'])
def registrar_evento():
    """Recebe um evento de auditoria de qualquer serviço interno (auth_service
    ou app_principal) e grava no Redis Stream. Estrutura mínima exigida:
    usuario_id, acao, timestamp. IP é opcional (bônus)."""
    data = request.get_json() or {}
    usuario_id = data.get('usuario_id')
    acao = data.get('acao')
    ip = data.get('ip') or ''

    if usuario_id is None or not acao:
        return jsonify({"error": "usuario_id e acao são obrigatórios"}), 400

    timestamp = datetime.datetime.utcnow().isoformat() + "Z"

    try:
        r = get_redis()
        entry_id = r.xadd(
            STREAM_KEY,
            {
                "usuario_id": str(usuario_id),
                "acao": acao,
                "timestamp": timestamp,
                "ip": ip,
            },
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
        return jsonify({"message": "Evento registrado", "id": entry_id}), 201
    except Exception as e:
        print(f"[LOG SERVICE ERROR] Falha ao gravar evento no Redis: {e}")
        return jsonify({"error": f"Erro interno ao registrar evento: {str(e)}"}), 500


@app.route('/events', methods=['GET'])
def listar_eventos():
    """Retorna os últimos N eventos, do mais recente pro mais antigo.
    Não é exposta pra fora do Docker (sem porta publicada); quem decide
    se o usuário pode VER esses eventos é o app_principal, consultando o
    papel (role) validado pelo auth_service — mesmo padrão da atividade 4."""
    limit = request.args.get('limit', default=50, type=int)
    limit = max(1, min(limit, 1000))

    try:
        r = get_redis()
        raw_entries = r.xrevrange(STREAM_KEY, count=limit)
        eventos = [
            {"id": entry_id, **fields}
            for entry_id, fields in raw_entries
        ]
        return jsonify({"events": eventos, "count": len(eventos)}), 200
    except Exception as e:
        print(f"[LOG SERVICE ERROR] Falha ao ler eventos do Redis: {e}")
        return jsonify({"error": f"Erro interno ao listar eventos: {str(e)}"}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002)
