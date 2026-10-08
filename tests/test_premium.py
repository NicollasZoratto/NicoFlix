"""Atividade 7 — Stripe: webhook assinado, checkout e benefício Premium."""
import hashlib
import hmac
import json
import time
from unittest.mock import patch, MagicMock

WHSEC = "whsec_teste_unitario"


def _user(uid=1, role="usuario"):
    return {"valid": True, "user_id": uid, "username": "nico", "role": role}


def _evento_pago(uid="7"):
    return json.dumps({
        "id": "evt_1", "object": "event", "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_1", "client_reference_id": uid, "payment_status": "paid",
                            "customer": "cus_123", "subscription": "sub_123"}},
    }).encode()


def _assinar(payload, secret=WHSEC, ts=None):
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _post(app_mod, payload, sig):
    headers = {"Stripe-Signature": sig} if sig is not None else {}
    return app_mod.app.test_client().post('/stripe/webhook', data=payload, headers=headers,
                                          content_type='application/json')


# --- Webhook: validação de assinatura ---

def test_webhook_sem_assinatura_e_rejeitado(catalog_app):
    with patch.object(catalog_app, 'STRIPE_WEBHOOK_SECRET', WHSEC), \
         patch('mysql.connector.connect') as conn:
        res = _post(catalog_app, _evento_pago(), None)
    assert res.status_code == 400
    conn.assert_not_called()


def test_webhook_assinatura_forjada_e_rejeitada(catalog_app):
    p = _evento_pago()
    with patch.object(catalog_app, 'STRIPE_WEBHOOK_SECRET', WHSEC), \
         patch('mysql.connector.connect') as conn:
        res = _post(catalog_app, p, _assinar(p, secret="whsec_atacante"))
    assert res.status_code == 400
    conn.assert_not_called()


def test_webhook_corpo_adulterado_e_rejeitado(catalog_app):
    p = _evento_pago("7")
    sig = _assinar(p)
    with patch.object(catalog_app, 'STRIPE_WEBHOOK_SECRET', WHSEC), \
         patch('mysql.connector.connect') as conn:
        res = _post(catalog_app, _evento_pago("8"), sig)
    assert res.status_code == 400
    conn.assert_not_called()


def test_webhook_sem_secret_configurado_retorna_503(catalog_app):
    with patch.object(catalog_app, 'STRIPE_WEBHOOK_SECRET', ""):
        res = _post(catalog_app, _evento_pago(), "t=1,v1=x")
    assert res.status_code == 503


def test_webhook_valido_ativa_premium(catalog_app):
    p = _evento_pago("7")
    with patch.object(catalog_app, 'STRIPE_WEBHOOK_SECRET', WHSEC), \
         patch.object(catalog_app, 'log_event') as log, \
         patch('mysql.connector.connect') as conn:
        res = _post(catalog_app, p, _assinar(p))
    assert res.status_code == 200
    cur = conn.return_value.cursor.return_value
    sql, params = cur.execute.call_args[0]
    assert 'assinaturas' in sql and 'premium' in sql
    assert params == (7, "cus_123", "sub_123")
    log.assert_called_once_with(7, 'assinatura_premium_ativada')


def test_webhook_cancelamento_remove_premium(catalog_app):
    p = json.dumps({"type": "customer.subscription.deleted",
                    "data": {"object": {"id": "sub_123"}}}).encode()
    with patch.object(catalog_app, 'STRIPE_WEBHOOK_SECRET', WHSEC), \
         patch.object(catalog_app, 'log_event'), \
         patch('mysql.connector.connect') as conn:
        res = _post(catalog_app, p, _assinar(p))
    assert res.status_code == 200
    sql, params = conn.return_value.cursor.return_value.execute.call_args[0]
    assert 'premium = 0' in sql and params == ("sub_123",)


def test_webhook_nao_pago_nao_ativa(catalog_app):
    p = json.dumps({"type": "checkout.session.completed", "data": {"object": {
        "client_reference_id": "7", "payment_status": "unpaid"}}}).encode()
    with patch.object(catalog_app, 'STRIPE_WEBHOOK_SECRET', WHSEC), \
         patch('mysql.connector.connect') as conn:
        res = _post(catalog_app, p, _assinar(p))
    assert res.status_code == 200
    conn.assert_not_called()


# --- Checkout ---

def test_assinar_exige_login(catalog_app):
    with patch.object(catalog_app, 'get_current_user', return_value=None):
        res = catalog_app.app.test_client().post('/assinar')
    assert res.status_code == 302 and '/login' in res.headers['Location']


def test_assinar_cria_checkout_e_redireciona(catalog_app):
    fake = MagicMock(url="https://checkout.stripe.com/c/pay/cs_test_abc")
    with patch.object(catalog_app, 'STRIPE_SECRET_KEY', 'sk_test_x'), \
         patch.object(catalog_app, 'get_current_user', return_value=_user(5)), \
         patch.object(catalog_app, 'usuario_e_premium', return_value=False), \
         patch.object(catalog_app, 'log_event'), \
         patch('stripe.checkout.Session.create', return_value=fake) as create:
        res = catalog_app.app.test_client().post('/assinar', data={"user_id": "999"})
    assert res.status_code == 303
    assert res.headers['Location'] == fake.url
    kw = create.call_args.kwargs
    assert kw['mode'] == 'subscription'
    assert kw['client_reference_id'] == '5'  # vem do JWT, não do formulário


# --- Benefício Premium: limite de favoritos ---

def _favoritar(app_mod, premium, total):
    with patch.object(app_mod, 'get_current_user', return_value=_user(1)), \
         patch.object(app_mod, 'usuario_e_premium', return_value=premium), \
         patch.object(app_mod, 'log_event') as log, \
         patch('mysql.connector.connect') as conn:
        cur = conn.return_value.cursor.return_value
        cur.fetchone.side_effect = [None, (total,)]
        res = app_mod.app.test_client().post('/favoritar', data={"movie_id": "10"})
    sqls = [c[0][0] for c in cur.execute.call_args_list]
    return res, sqls, log


def test_gratis_no_limite_nao_consegue_favoritar(catalog_app):
    res, sqls, log = _favoritar(catalog_app, premium=False, total=catalog_app.FAVORITOS_LIMITE_GRATIS)
    assert res.status_code == 302
    assert not any(s.startswith('INSERT INTO favoritos') for s in sqls)
    assert 'limite_favoritos_gratis_atingido' in log.call_args[0][1]


def test_premium_favorita_alem_do_limite(catalog_app):
    res, sqls, _ = _favoritar(catalog_app, premium=True, total=999)
    assert any(s.startswith('INSERT INTO favoritos') for s in sqls)


def test_gratis_abaixo_do_limite_favorita(catalog_app):
    res, sqls, _ = _favoritar(catalog_app, premium=False, total=1)
    assert any(s.startswith('INSERT INTO favoritos') for s in sqls)
