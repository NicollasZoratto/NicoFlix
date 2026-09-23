# 🎬 Catálogo de Filmes - Tom Hanks - NicoFlix

Projeto desenvolvido para a disciplina de Cloud Computing na FATEC.

## 👨‍🏫 Professor Responsável

- Professor **@siriani**

## 🚀 Arquitetura e Tecnologias

- **Consumo de API:** TMDB (The Movie Database) para dados e pôsteres em tempo real.
- **Backend:** Python / Flask, dividido em três containers (`web`, `auth_service`, `log_service`) + `redis`.
- **Persistência de Dados:** MariaDB / MySQL (usuários, papéis, favoritos e comentários) + Redis Streams (log de auditoria).
- **Containerização:** Docker e Portainer.

```
Navegador ──HTTPS──▶ web (único ponto público, :8223)
                        │
                        ├──rede interna──▶ auth_service (login, papéis, esqueci-senha)
                        │                        │
                        │                        └──envia e-mail──▶ Mailtrap/Brevo (SMTP)
                        │
                        └──rede interna──▶ log_service (auditoria) ──▶ redis (Streams)
```

`auth_service`, `log_service` e `redis` **não publicam porta pro host** — só são alcançáveis de dentro da rede Docker (`nicoflix-net`). O `web` continua sendo o único ponto de entrada público.

## 🔐 Autenticação e papéis (Atividades 3 e 4)

- **Papéis de usuário:** cada usuário tem uma coluna `role` (`usuario` ou `admin`). O primeiro usuário cadastrado vira `admin` automaticamente; os demais recebem `usuario`. O papel viaja dentro do JWT emitido pelo `auth_service` e é devolvido por `/validate-token`, pra o catálogo consultar sempre que precisar.
- **Esqueci minha senha:** usuário informa e-mail → `auth_service` gera token (`reset_tokens`: `token`, `usuario_id`, `criado_em`, `expira_em`, `usado`) válido por **30 minutos** → envia e-mail com link → catálogo valida o token (existe? não usado? não expirado?) antes de deixar redefinir a senha.

### Permissões por papel

**`usuario` (padrão de todo cadastro, exceto o primeiro):**
- Login / cadastro / logout e redefinição de senha.
- Ver o catálogo, favoritar/desfavoritar filmes.
- Criar, editar e apagar o **próprio** comentário.

**`admin` (tudo que `usuario` pode, mais):**
- Acessar o painel de moderação `/admin/comentarios` e apagar o comentário de **qualquer usuário**.
- Acessar o painel de auditoria `/admin/logs` e consultar os últimos eventos do sistema.

### Ação exclusiva de admin e enforcement

Apagar comentário de outro usuário usa o mesmo endpoint (`POST /comentar/excluir`) que apagar o próprio: o backend compara o `user_id` do comentário-alvo com o `user_id` de quem está autenticado. Se forem diferentes, só `role == "admin"` passa — senão, **403 Forbidden**, mesmo chamando a rota direto (Postman/curl), sem passar pela tela. O mesmo vale para `/admin/comentarios` e `/admin/logs`. O papel usado nessa checagem vem do JWT decodificado pelo `auth_service`, nunca de algo que o cliente mandou.

### Padrão A ou B?

Hoje o projeto usa, na prática, o **Padrão A (enforcement centralizado)** — mesmo o papel estando dentro do JWT como claim. O catálogo não tem o `JWT_SECRET`, então não consegue validar o token sozinho: em toda requisição autenticada ele chama `POST /validate-token` no `auth_service`, que decodifica o JWT e devolve `role`. Ou seja, toda ação sensível já depende de uma ida-e-volta de rede — na prática, o mesmo custo e o mesmo ponto único de falha do Padrão A.

Pra migrar pro **Padrão B (claims no token)** de verdade, o catálogo precisaria verificar a assinatura do JWT sozinho — compartilhando o `JWT_SECRET` (HS256, como hoje) ou, melhor, usando um par de chaves assimétrico (RS256: `auth_service` assina com a chave privada, catálogo valida com a pública). Aí o catálogo decodificaria o token localmente com `pyjwt`, sem chamar `/validate-token`. Vantagem: menos latência e um serviço a menos no caminho crítico. Desvantagem: se o papel de alguém mudar no banco, só teria efeito quando o token expirasse — hoje, como cada ação já bate no `auth_service`, a mudança tem efeito imediato na próxima requisição.

## 📜 Logs e auditoria (Atividade 5)

Novo microsserviço `log_service`, container separado, mesma rede interna do Docker, sem porta publicada — mesmo princípio do `auth_service`.

### Eventos logados

| Evento | Onde é logado |
|---|---|
| `login` | `auth_service`, ao concluir o 2FA com sucesso |
| `logout` | `web`, antes de limpar a sessão |
| `favoritar` / `desfavoritar` | `web`, na rota `/favoritar` |
| `comentar` | `web`, na rota `/comentar` |
| `apagar_comentario_proprio` / `apagar_comentario_moderacao` | `web`, na rota `/comentar/excluir` |
| `403_apagar_comentario_negado` | `web`, quando um `usuario` tenta apagar comentário de outra pessoa |
| `403_acesso_moderacao_negado` | `web`, quando um `usuario` tenta acessar `/admin/comentarios` |
| `403_acesso_logs_negado` | `web`, quando um `usuario` tenta acessar `/admin/logs` |

Tentativas negadas por permissão (403) são o evento mais valioso pra auditoria de segurança — são logadas mesmo que a ação nunca chegue a acontecer.

### Estrutura de cada log

```
usuario_id   — quem executou a ação
acao         — string da ação (ex: "favoritar:filme_31562")
timestamp    — ISO 8601 em UTC, gerado no próprio log_service
ip           — request.remote_addr de quem fez a chamada (bônus)
```

### Por que Redis Streams (`XADD`) e não uma lista

Optamos pelo Redis Streams, como recomendado, em vez de uma lista simples (`LPUSH`/`RPUSH`), por três motivos:
1. **IDs ordenáveis e únicos de fábrica** — cada entrada recebe um ID tipo `<timestamp>-<seq>`, então a ordem cronológica vem de graça, sem precisar de um campo auxiliar de ordenação.
2. **Leitura em intervalo sem reprocessar tudo** — `XREVRANGE` já devolve do mais recente pro mais antigo com `COUNT`, sem precisar carregar a lista inteira e inverter em código.
3. **Trim automático de memória** — usamos `XADD ... MAXLEN ~ 10000`, então o stream nunca cresce sem limite; numa lista simples isso teria que ser controlado manualmente.

### Endpoint de consulta — só admin

`GET /admin/logs` no catálogo (`web`) lista os últimos 100 eventos, do mais recente pro mais antigo, com nome de usuário (buscado no `auth_service`) em vez de só o ID. Protegido pelo mesmo controle de acesso da atividade 4: usuário comum recebe **403**, mesmo acessando a URL direto — igual em `/admin/comentarios`.

O `log_service` em si não tem checagem de papel nas suas rotas (`/log`, `/events`) porque ele não é alcançável de fora da rede Docker — quem decide se um usuário pode *ver* os eventos é sempre o catálogo, consultando o papel validado pelo `auth_service`, nunca o `log_service` diretamente.

### Demonstração prática

1. Login como `usuario` comum → favoritar um filme → comentar → tentar abrir `/admin/logs` (403).
2. Login como `admin` → abrir `/admin/logs` → os quatro eventos do passo 1 aparecem na ordem certa, incluindo o `403_acesso_logs_negado`.

## ✉️ E-mail via Mailtrap

1. Copie `.env.example` para `.env` (esse arquivo fica fora do Git — veja `.gitignore`).
2. Crie uma conta gratuita em [mailtrap.io](https://mailtrap.io), abra **Email Testing → Inboxes → (sua inbox) → SMTP Settings** e copie host/porta/usuário/senha da aba "Flask" ou "Python".
3. Preencha `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` no `.env`.
4. `docker-compose up --build` lê o `.env` automaticamente.

Sem essas variáveis preenchidas, o e-mail não é enviado de verdade — o link de redefinição só aparece no log do container `auth_service` (`docker-compose logs -f auth_service`), o que ainda é suficiente pra testar o fluxo localmente.

## 🧪 Testes

Testes automatizados com `pytest`, mockando banco (MySQL), Redis e chamadas HTTP entre serviços — não precisa de containers rodando pra executar.

```bash
pip install --break-system-packages -r auth_service/requirements.txt -r app_principal/requirements.txt -r log_service/requirements.txt -r tests/requirements-dev.txt
python -m pytest tests/ -v
```

Cobertura atual (20 testes): papel do primeiro usuário cadastrado, login/JWT, `/forgot-password` sem revelar existência de e-mail, 403 em `/admin/comentarios` e `/admin/logs` pra usuário comum, exclusão de comentário próprio vs. de terceiros, favoritar/desfavoritar com log gerado, e as rotas do `log_service` (`/log`, `/events`, `/health`).

## 🎨 Front-end

Paleta trocada de roxo/rosa pra um tema índigo/ciano mais sóbrio (`app_principal/static/style.css`), com tipografia (Inter + Space Grotesk), cards de filme com proporção de pôster fixa, badge dourado pra `admin` vs. cinza pra `usuario`, e as telas de login/2FA/esqueci-senha/moderação/logs todas usando o mesmo sistema de design (sem depender mais do Bootstrap).

## 🐛 Correções feitas nas revisões anteriores

- `templates/verify_2fa.html` não existia — a rota `/verify-2fa` sempre quebrava com `TemplateNotFound` (500). Corrigido.
- `index.html` usava variáveis (`usuario`, `filmes`) diferentes das que o `app.py` passava (`username`, `movies`), e chamava `favoritos.get(...)` como se fosse dicionário — mas é lista. Corrigido.
- O formulário de comentário postava pra `/` (só aceita GET) com campos que não batiam com a rota `/comentar`. Favoritar e comentar viraram ações separadas, cada uma na sua rota.

## 🔒 Segurança

- Credenciais sensíveis (TMDB, banco, JWT, SMTP) vêm de variáveis de ambiente, lidas de um `.env` local (veja `.env.example`) — o `.env` de verdade nunca é commitado (`.gitignore`).
- `auth_service`, `log_service` e `redis` não publicam porta pro host — só acessíveis pela rede interna do Docker.
- `/forgot-password` sempre devolve a mesma mensagem genérica, exista ou não o e-mail, pra não permitir enumerar usuários cadastrados.
