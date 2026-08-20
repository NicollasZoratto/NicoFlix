# 🎬 Catálogo de Filmes - Tom Hanks - NicoFlix

Projeto desenvolvido para a disciplina de Cloud Computing na FATEC.

## 👨‍🏫 Professor Responsável

- Professor **@siriani**

## 🚀 Arquitetura e Tecnologias

- **Consumo de API:** TMDB (The Movie Database) para dados e pôsteres em tempo real.
- **Backend:** Python / Flask.
- **Persistência de Dados:** MariaDB / MySQL (Armazenando usuários e favoritos isolados por conta).
- **Containerização:** Docker e Portainer.

## 🔒 Segurança

Todas as credenciais sensíveis (Chave TMDB e senhas do MariaDB) são injetadas via Variáveis de Ambiente no Portainer.
