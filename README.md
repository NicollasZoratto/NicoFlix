# 🎬 Catálogo de Filmes - Tom Hanks - NicoFlix

Projeto desenvolvido para a disciplina de Cloud Computing na FATEC.

## 👨‍🏫 Professor Responsável

- Professor **@siriani**

## 🚀 Arquitetura e Tecnologias

- **Consumo de API:** TMDB (The Movie Database) para dados e pôsteres em tempo real.
- **Backend:** Python / Flask, dividido em dois containers (`web` e `auth_service`).
- **Persistência de Dados:** MariaDB / MySQL (usuários, papéis, favoritos e comentários isolados por conta).
- **Containerização:** Docker e Portainer.

## 🔐 Autenticação e serviços desacoplados (Atividade 3)

- `auth_service` é um container à parte, sem porta publicada para fora (`ports` não definido em `docker-compose.yml`) — só é acessível pela rede interna Docker (`http://auth_service:5001`).
- `web` (catálogo) continua sendo o único ponto de entrada público.
- **Papéis de usuário:** cada usuário tem uma coluna `role` (`usuario` ou `admin`). O primeiro usuário cadastrado vira `admin` automaticamente; os demais recebem `usuario`. O papel viaja dentro do JWT e é devolvido por `/validate-token`, para o catálogo poder consultá-lo quando precisar.
- **Esqueci minha senha:**
  1. Usuário informa o e-mail em `/esqueci-senha`.
  2. `auth_service` gera um token aleatório (`reset_tokens`: `token`, `usuario_id`, `criado_em`, `expira_em`, `usado`) válido por **30 minutos** e envia um e-mail com o link `/reset-senha/<token>`.
  3. Ao abrir o link, o catálogo valida o token no `auth_service` (existe? não usado? não expirado?) antes de mostrar o formulário de nova senha.
  4. Ao confirmar, o token é marcado como usado e não pode ser reaproveitado.
- **Envio de e-mail real:** configurado via variáveis de ambiente SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`) no `docker-compose.yml`/Portainer — use as credenciais de sandbox do **Mailtrap** em desenvolvimento. Se essas variáveis não estiverem preenchidas, o link é apenas impresso no log do container `auth_service` (facilita testar localmente sem configurar SMTP).

## 🛡️ Controle de acesso por papel (Atividade 4 — RBAC)

### Permissões por papel

**`usuario` (padrão de todo cadastro, exceto o primeiro):**
- Fazer login / cadastro / logout e solicitar redefinição de senha.
- Ver o catálogo de filmes do Tom Hanks.
- Favoritar / desfavoritar filmes.
- Criar e editar o **próprio** comentário em um filme.
- Apagar o **próprio** comentário.

**`admin` (tudo que `usuario` pode, mais):**
- Acessar o painel de moderação em `/admin/comentarios`.
- Apagar o comentário de **qualquer usuário**, em qualquer filme (moderação).

(Alternativa que ficou de fora, mencionada no enunciado: um endpoint pra promover/rebaixar o papel de outro usuário. Não foi implementada nesta atividade — o admin hoje é fixo desde o cadastro, é só o primeiro usuário criado.)

### Ação exclusiva de admin

Apagar comentário de outro usuário (moderação). O endpoint é o mesmo usado por qualquer usuário pra apagar o próprio comentário (`POST /comentar/excluir`), mas o backend compara o `user_id` do comentário-alvo com o `user_id` de quem está autenticado:

- Se forem iguais → qualquer usuário pode apagar (é o próprio comentário).
- Se forem diferentes → só quem tem `role == "admin"` pode; caso contrário o Flask responde **403 Forbidden**, mesmo chamando a rota direto (Postman/curl), sem passar pela tela.

O papel (`role`) usado nessa checagem vem do JWT decodificado pelo `auth_service` (`/validate-token`) — o catálogo nunca decide isso sozinho a partir de dado que o cliente mandou.

### Padrão A ou B?

Hoje o projeto usa, na prática, o **Padrão A (enforcement centralizado)** — mesmo o papel estando dentro do JWT como claim. Isso porque o catálogo (`app_principal`) não tem o `JWT_SECRET`: ele não consegue decodificar/validar o token sozinho, então em toda requisição autenticada ele chama `POST /validate-token` no `auth_service`, que decodifica o JWT e devolve `user_id`/`username`/`role`. Ou seja, toda ação sensível já depende de uma ida-e-volta de rede até o `auth_service` — na prática, o mesmo custo e o mesmo ponto único de falha do Padrão A, só que "escondido" dentro de uma chamada que parece só validar o token.

Pra migrar de verdade pro **Padrão B (claims no token)**, o catálogo precisaria conseguir verificar a assinatura do JWT sozinho — ou compartilhando o `JWT_SECRET` (HS256, como hoje) diretamente com o `app_principal`, ou trocando pra um par de chaves assimétrico (RS256), onde o `auth_service` assina com a chave privada e o catálogo só precisa da chave pública pra validar. Aí o catálogo decodificaria o token localmente com `pyjwt`, sem chamar `/validate-token`, e leria o `role` direto do payload. A vantagem seria latência menor e um serviço a menos no caminho crítico; a desvantagem é a mesma citada na atividade: se um admin virar `usuario` (ou vice-versa), isso só valeria depois que o token atual expirasse — hoje, como cada ação já bate no `auth_service`, uma mudança de papel no banco tem efeito imediato na próxima requisição.

## 🐛 Correções feitas nesta revisão

O projeto original apresentava erros 400/500 pelos seguintes motivos, todos corrigidos:

- `templates/verify_2fa.html` não existia — a rota `/verify-2fa` sempre quebrava com `TemplateNotFound` (500).
- `index.html` usava variáveis (`usuario`, `filmes`) diferentes das passadas por `app.py` (`username`, `movies`), e chamava `favoritos.get(...)` como se `favoritos` fosse um dicionário, quando na verdade é uma lista — isso gerava `AttributeError` (500) toda vez que a página carregava.
- O formulário de comentário no `index.html` enviava para `/` (que só aceita `GET`) com campos (`tmdb_id`) que não batiam com a rota `/comentar` (que espera `movie_id`) — os comentários nunca eram salvos e o envio gerava `405 Method Not Allowed`.
- Favoritar e comentar agora são ações separadas, cada uma com sua própria rota (`/favoritar` e `/comentar`).

## 🔒 Segurança

Todas as credenciais sensíveis (Chave TMDB, senha do MariaDB, segredo do JWT e credenciais SMTP) são injetadas via Variáveis de Ambiente no Portainer/`docker-compose.yml`.

> ⚠️ **Atenção:** como este é um repositório público no GitHub, evite commitar o `docker-compose.yml` com senhas reais em texto puro (mesmo como valor padrão). O ideal é usar um arquivo `.env` (adicionado ao `.gitignore`) ou os *secrets* do Portainer, deixando o `docker-compose.yml` versionado apenas com `${VARIAVEL}`.
