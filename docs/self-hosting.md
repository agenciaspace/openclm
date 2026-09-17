# Operar no seu servidor

## HTTPS e acesso de rede

O Compose publica a aplicação somente em `127.0.0.1:8000`. Coloque um proxy HTTPS no mesmo servidor e configure `APP_URL=https://clm.suaempresa.com` e `SECURE_COOKIES=true`. Nunca exponha diretamente PostgreSQL ou Ollama à Internet.

Exemplo de trecho Nginx, com certificados provisionados por você:

```nginx
server {
    listen 443 ssl;
    server_name clm.suaempresa.com;
    ssl_certificate /etc/ssl/clm/fullchain.pem;
    ssl_certificate_key /etc/ssl/clm/privkey.pem;
    client_max_body_size 2m;

    # Evita registrar o code/state do callback OAuth em URLs de acesso.
    access_log off;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 150s;
    }
}
```

Configure separadamente o redirecionamento HTTP→HTTPS, a renovação dos certificados e um limite de tentativas de login no proxy. A aplicação possui proteção local de 10 falhas de login por endereço em 10 minutos; atrás de proxies, endereços podem ser agrupados. Ela não confia indiscriminadamente em `X-Forwarded-For`. Ajuste a política do proxy para sua rede antes de expor a instância.

## Configuração e segredos

`scripts/configure.py` cria `.env` com permissão 0600 e segredos aleatórios. Nunca publique esse arquivo. Use um gerenciador de segredos ou arquivos de ambiente protegidos para produção. Contas iniciais são criadas pela CLI; não há senha padrão ou cadastro público.

Os nomes e conteúdos de documentos não são enviados a serviços de analytics. A configuração Docker desabilita access logs da aplicação para evitar códigos OAuth em URLs. Considere também os logs do proxy e do provedor de infraestrutura.

Não exponha um host Ollama com encaminhamento para nuvem no endereço privado configurado. O administrador controla o destino e os pesos; a garantia operacional de rede local depende dessa configuração e da política de saída do servidor.

## Backup

Contratos e versões são armazenados no PostgreSQL. Faça um dump consistente com permissões restritas:

```sh
umask 077
docker compose exec -T db pg_dump -U openclm -d openclm -Fc > openclm-backup.dump
```

Guarde também, separadamente e criptografados, a configuração e `TOKEN_ENCRYPTION_KEY`. Quem tiver o dump e a chave poderá decifrar os tokens OAuth. Teste a restauração em uma instância vazia antes de depender do backup.

Exemplo de restauração para um **banco vazio**:

```sh
docker compose exec -T db pg_restore -U openclm -d openclm < openclm-backup.dump
```

Para SQLite de desenvolvimento, pare a aplicação antes de copiar `openclm.db` ou use a API de backup do SQLite. Não copie arquivos de um banco em escrita sem mecanismo consistente.

## Atualização

Faça backup, leia as alterações e planeje a migração antes de atualizar:

```sh
git pull --ff-only
docker compose up --build -d --wait
```

O container executa `alembic upgrade head` antes de iniciar. Para múltiplas réplicas, execute a migração uma vez em um job separado. Não faça downgrade sobre dados de produção sem um plano de restauração validado.

## Contas e recuperação

```sh
docker compose exec app python -m openclm.cli reset-password --email pessoa@empresa.com
docker compose exec app python -m openclm.cli disable-user --email pessoa@empresa.com
```

Redefinição de senha e desativação invalidam as sessões e chaves de API da pessoa. A CLI registra a operação no histórico e impede desativar o último administrador ativo. Conexões OAuth podem ser removidas em Configurações pelo próprio usuário; para revogar também o consentimento externo, use o portal DocuSign.

## Saúde e capacidade

`GET /health` verifica conexão com o banco e presença da tabela de migrações. A imagem roda como usuário sem privilégios; o filesystem da aplicação é somente leitura no Compose. O banco e o Ollama usam volumes persistentes.

Monitore espaço em disco, disponibilidade do banco, backups e tempo de resposta das integrações. A IA é executada de forma síncrona com timeout de 120 segundos; não há fila distribuída ou controle de cotas por usuário nesta base. Dimensione o modelo e restrinja concorrência no Ollama conforme seu hardware.

## LegalOps demonstration deployment

The demo at `https://openclm.leonn.dev` runs from `/root/openclm` with SQLite,
behind a dedicated Cloudflare Tunnel. Services: `openclm-demo.service` and
`openclm-demo-tunnel.service`. It binds only `127.0.0.1:8787`; the tunnel does
not modify existing host tunnels or WhatsApp gateways. Both services start on boot.

Demo accounts and their initial passwords are stored only in
`/root/.local/share/openclm-demo-access.txt` (mode 0600). Server secrets are in
`/root/openclm/.env` (mode 0600). Do not commit either file or the database.
Use the administrator and separate reviewer to exercise the approval flow.
Templates and the initial contract are explicitly labeled as demonstration data.

For an update: back up the SQLite database with SQLite's backup API and keep a
protected backup of `.env`; pull the reviewed main revision, run `uv sync --frozen`
and `uv run alembic upgrade head`, then restart only `openclm-demo.service`.
Use `/health` and an authenticated browser flow to verify the deployed revision.
No automated host deployment or recurring backup is configured by this setup.
