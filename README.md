# OpenCLM

**Seus contratos. Sua infraestrutura. Seu controle.**

Uma base open-source para gestão do ciclo de vida de contratos, com hospedagem própria, documentos gerados a partir de formulários, aprovações e IA local opcional. Licença MIT; sem conta OpenCLM, telemetria ou serviços de nuvem obrigatórios.

> **v0.1 — base funcional para desenvolvimento e avaliação.** Uma organização por instalação. Não é uma plataforma de CLM completa ou uma implantação de produção já auditada.

## O que funciona

- Interface em português: contratos, filtros, modelos/formulários, workflows, equipe e integrações.
- Login com senha Argon2, sessão HttpOnly, proteção CSRF e perfis administrador/editor/revisor/leitor.
- Formulários com texto, texto longo, número, data, e-mail, opções e sim/não; validação também no servidor.
- Modelos de texto com `{{chave_da_pergunta}}`, geração de **DOCX e TXT**, visualização e versões preservadas.
- Aprovação sequencial, aprovadores específicos ou revisores habilitados, devolução com justificativa e proibição de autoaprovação.
- API `/api/v1`, OpenAPI, chaves revogáveis com validade, respostas estruturadas, paginação de contratos e histórico.
- **Conectar com DocuSign**: login e consentimento por pessoa, descoberta da conta padrão, renovação automática e tokens criptografados. Envio de contratos aprovados, webhooks HMAC e conciliação de envelopes.
- **Ollama local**: resumo, pontos de atenção e sugestões de perguntas; sem ações automáticas sobre os contratos.
- PostgreSQL no Docker Compose, SQLite para desenvolvimento, migrações Alembic e testes automatizados.

## Iniciar com Docker

Requisitos: Git, Python 3 para gerar a configuração inicial e Docker com Compose v2.

```sh
git clone https://github.com/agenciaspace/openclm.git
cd openclm
python3 scripts/configure.py
docker compose up --build -d --wait

# Crie a conta inicial. A senha será solicitada sem aparecer no terminal.
docker compose exec app python -m openclm.cli create-user \
  --email admin@suaempresa.com --name Administrador --role admin

# Opcional: dois modelos demonstrativos e um fluxo de revisão jurídica.
docker compose exec app python -m openclm.cli seed
```

Abra **http://localhost:8000**. A API é apresentada em **http://localhost:8000/docs**; o arquivo OpenAPI está em `/openapi.json`. Todos os recursos dessa documentação são servidos localmente, sem CDN.

Crie um revisor em **Configurações → Equipe**, ou pelo mesmo comando com `--role reviewer`. O autor de um contrato não pode aprová-lo, inclusive quando administrador.

O Compose publica somente a aplicação, no loopback da máquina. Banco e Ollama não possuem portas públicas. Contratos, respostas, versões e tokens criptografados ficam no volume `postgres_data`. **Não execute `docker compose down -v` se quiser preservar os dados.**

## Executar sem Docker

Requisitos: Python 3.12+ e [uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
python3 scripts/configure.py
uv sync --frozen
uv run alembic upgrade head
uv run python -m openclm.cli create-user \
  --email admin@suaempresa.com --name Administrador
uv run python -m openclm.cli seed
uv run uvicorn openclm.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

O banco local é `openclm.db`. O arquivo `.env` e os bancos locais são ignorados pelo Git. O script de configuração gera segredos exclusivos e recusa sobrescrever um `.env` existente.

## Primeiro fluxo de uso

1. Crie um workflow ou use **Revisão jurídica** do seed.
2. Crie um modelo: adicione perguntas e use suas chaves no texto. Exemplo: `A empresa {{empresa}} contrata {{servico}}.`
3. Clique em **Novo contrato**, preencha as respostas e gere o documento.
4. Baixe o DOCX ou envie o contrato para aprovação.
5. Entre com a conta do revisor para aprovar ou devolver com um motivo.
6. Após a última aprovação, o responsável pode enviar ao DocuSign se tiver conectado sua conta.

Os modelos do seed são **demonstrativos e incompletos**. Adapte cláusulas e condições antes de assinar. Eles não são uma biblioteca jurídica validada.

## DocuSign: conectar e usar

Para a pessoa que usa o CLM: **Configurações → Conectar com DocuSign → entrar → autorizar**. O OpenCLM associa a conta padrão do DocuSign, guarda a autorização de forma criptografada e renova o acesso quando necessário. A pessoa não precisa copiar tokens, informar account IDs ou gerar chaves RSA.

Para quem hospeda a instalação: há uma configuração única do aplicativo OAuth e da chave HMAC do integrador. É necessária porque cada instalação tem seu próprio domínio e mantém seus próprios segredos. O projeto não depende de um intermediário central de autenticação.

Consulte [configuração do DocuSign](docs/docusign.md) para credenciais do aplicativo, callback, ambiente de demonstração, produção e recuperação de falhas. **A conexão não torna o DocuSign local**: cada envio compartilha o documento e os dados dos signatários com esse provedor, mediante confirmação no formulário de envio.

## IA no seu servidor

A IA começa desativada e não interfere nas funções de CLM. Para usar o serviço local incluído no Compose:

```sh
docker compose --profile ai up -d
docker compose exec ollama ollama pull qwen3:8b
# Em .env: AI_ENABLED=true e OLLAMA_MODEL=qwen3:8b
docker compose up -d app
```

O download inicial do modelo usa a Internet; a inferência posterior ocorre na sua infraestrutura. O Compose define `OLLAMA_NO_CLOUD=1` para desabilitar recursos de nuvem do Ollama. A aplicação recusa URLs públicas de IA e modelos com indicação remota, não baixa modelos automaticamente e não possui fallback para provedores externos. Consulte a [documentação oficial do modo local do Ollama](https://docs.ollama.com/faq#how-do-i-disable-ollama-cloud-features).

Sem Docker, configure `OLLAMA_URL=http://127.0.0.1:11434`, `AI_ENABLED=true` e inicie seu Ollama com os recursos de nuvem desativados. Escolha o modelo conforme memória, hardware e licença dos pesos; a licença MIT do OpenCLM não substitui a licença do modelo.

Esta versão aceita até 16 mil caracteres por análise, informa quando o limite é excedido e exige revisão humana. A qualidade depende do modelo instalado. Para garantir ausência de tráfego externo em infraestrutura própria, restrinja também a saída de rede após provisionar imagens, pacotes e pesos.

## Dados e permissões

| Perfil | Consultar contratos | Criar modelos/contratos | Editar e enviar | Aprovar | Administrar equipe/workflows |
| --- | --- | --- | --- | --- | --- |
| Leitor | Sim | Não | Não | Não | Não |
| Editor | Sim | Sim | Seus contratos | Não | Não |
| Revisor | Sim | Não | Não | Etapas autorizadas de outros autores | Não |
| Administrador | Sim | Sim | Todos | Etapas autorizadas de outros autores | Sim |

**Não há isolamento por departamento ou cliente.** Todas as pessoas autenticadas consultam a base da instalação. Use instalações separadas para organizações diferentes. Chaves de API herdam o perfil de sua conta; não têm permissões próprias por endpoint nesta versão.

As senhas e chaves de API são armazenadas como hashes. Tokens OAuth são criptografados com `TOKEN_ENCRYPTION_KEY`. Os documentos e as respostas ficam no banco da sua instalação; a criptografia do disco e dos backups é responsabilidade da implantação. O histórico é acrescentado pela aplicação e não pode ser editado pela API; ele não é um arquivo inviolável contra administradores do banco.

## Desenvolvimento e operação

- [Arquitetura, modelo de dados e próximos passos](docs/architecture.md)
- [Guia de API e exemplos](docs/api.md)
- [Servidor, HTTPS, backup e atualização](docs/self-hosting.md)
- [Integração DocuSign](docs/docusign.md)
- [Contribuição](CONTRIBUTING.md) e [segurança](SECURITY.md)

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
node --check openclm/static/app.js
```

O CI verifica os testes e inicia a imagem Docker com PostgreSQL. Testes de DocuSign e Ollama usam transporte simulado: não enviam documentos, não dependem de credenciais externas e não equivalem a uma homologação com a conta real do provedor.

## Limites desta versão

Ainda não inclui editor colaborativo/redline, importação de modelos DOCX, upload de contratos existentes, extração de PDFs, PDF assinado arquivado automaticamente, SSO/MFA, regras condicionais de workflow, filas duráveis, permissões por documento ou multi-organização. Integrações externas dependem da configuração do servidor e da disponibilidade/autorização dos provedores.

English: OpenCLM is an MIT-licensed, self-hosted contract lifecycle management foundation. Contracts, forms, document versions and approval workflows live on your infrastructure. Local Ollama AI and user-authorized DocuSign eSignature are optional.

## Salesforce and live demonstration

Salesforce OAuth + PKCE, opportunity search and audited contract links are available. See [configuration and scope](docs/salesforce.md). DocuSign supports authorized signature dispatch and authenticated status callbacks; see [DocuSign setup](docs/docusign.md).

The LegalOps [live demo](https://openclm.leonn.dev) runs on an isolated installation and requires a demo account. Example templates are labeled `[DEMO]`. External integrations display their actual configuration/connection state; a live demo does not mean external provider credentials are installed.
