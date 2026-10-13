# Prontidão para lançamento público — 13/10/2026

**Resultado: ainda não liberar cadastro público.** A versão atual funciona como
instalação de uma única organização. A suíte existente passou, mas isso não cobre
o novo requisito de hospedar várias organizações independentes gratuitamente.

**Entrega pretendida: 24/10/2026.** A Fenalaw começa em 27/10/2026, conforme as
[informações oficiais](https://www.fenalaw.com.br/informacoes-gerais/). São 11 dias
corridos desde esta avaliação. As datas abaixo são metas de execução, não garantia
de aprovação de integrações externas.

## Evidência executada

| Verificação | Resultado | Limite da evidência |
| --- | --- | --- |
| Suíte original | 47 testes passaram | Integrações externas simuladas |
| Novas verificações de autenticação/permissões | 23 testes passaram | Modelo atual de organização única |
| Navegadores | 20 testes passaram | Chromium desktop/celular, WebKit tablet e Firefox desktop; emulação, não aparelhos físicos |
| PostgreSQL 17 descartável | 4 etapas passaram | Instalação/migrações; criação e aprovação independente; backup/restauração; downgrade/upgrade em base de teste |
| Dependências Python de produção | 39 pacotes, nenhum alerta conhecido encontrado pelo pip-audit | Consulta pontual; não certifica ausência de vulnerabilidades |
| Ruff e sintaxe JavaScript | Passaram | Verificação estática |
| Instalação HTTPS | Página e health 200; contratos, usuários e modelos anônimos 401 | Apenas leitura; sem teste de carga em produção |

Os testes de navegador fazem login nos quatro perfis, percorrem as telas,
verificam acesso à administração, integração desabilitada e logout. Também
cadastram um leitor, preenchem o formulário, geram e baixam DOCX e submetem o
contrato à aprovação sem oferecer autoaprovação ao autor. Não houve transbordamento
horizontal nas telas verificadas. Cadastro público, convites e troca de organização
ainda não existem e, portanto, não estão cobertos como jornadas bem-sucedidas.

Os novos testes de API cobrem 15 superfícies privadas depois de logout e repetição
do cookie antigo; matriz dos quatro perfis para criação de contratos, usuários e
auditoria; desativação e expiração para sessões e chaves de API.

Na restauração PostgreSQL, o login continuou funcionando, o contrato permaneceu
aprovado, o histórico existiu e o SHA-256 do documento restaurado coincidiu com o
original. A base pública continua em SQLite: este ensaio **não migrou produção**
nem comprovou um backup externo da instalação pública.

## Lacunas observadas e critérios de conclusão

| Prioridade | Área | Situação atual | Critério de conclusão |
| --- | --- | --- | --- |
| P0 | Organizações e isolamento | Sem entidade de organização/membership e sem organização nos contratos, modelos, workflows ou conexões DocuSign. Leitor consegue consultar contrato, respostas, versões, documento e histórico do administrador da instalação. Isso é comportamento documentado, não uma violação entre tenants existentes. | Duas organizações reais na mesma instância; nenhuma leitura ou alteração cruzada por ID, busca, exportação, documento, API, aprovação ou callback. |
| P0 | Cadastro gratuito | Criação de usuário exige administrador. Login manda o visitante criar conta pelo servidor. | Visitante cria e verifica sua conta, cria organização e primeiro contrato pelo navegador, sem cartão, cobrança ou intervenção técnica. |
| P0 | Convites e perfis por organização | Quatro perfis globais; administrador atribui senha inicial. Sem convite ou alteração de associação pela API/UI. Reset/desativação existem somente na CLI. | Convite com validade e uso único; aceite vinculado ao e-mail verificado; papéis por organização; remoção e mudança de perfil com efeito imediato; proteção do último proprietário. |
| P0 | Recuperação de conta | Sem recuperação ou verificação de e-mail para a pessoa usuária. | Recuperação com resposta genérica, token de uso único e prazo curto; senha definida pelo titular; sessões antigas revogadas; e-mail transacional homologado. |
| P0 para DocuSign público | Conta/aplicativo e homologação | Código OAuth, envio, HMAC e conciliação existe; integração desativada na instalação. Não houve envio real. | Aplicativo elegível em produção, conexão de contas independentes, envio autorizado, conclusão, webhook e reconciliação reais; isolamento por organização e idempotência comprovados. |
| P1 para ciclo completo de assinatura | Documento assinado | Download oferece documento gerado em DOCX/TXT; não arquiva PDF assinado/certificado. | PDF final e certificado privados, íntegros e acessíveis apenas à organização correta; falhas recuperáveis. |
| P0 operacional | Banco, backups e observabilidade | PostgreSQL passou no ensaio; produção ainda é SQLite. Não foi comprovada rotina de backup externo, restauração do ambiente público ou monitoramento de falhas de integração. | Migração com preservação de usuários/documentos/histórico; backup externo e restauração ensaiados; alertas operacionais; rollback documentado e testado. |
| P0 para abertura irrestrita | Uso gratuito e abuso | Limites de corpo, paginação e tentativas de login existem; sem orçamento/quota por organização para os recursos do serviço público. | Limites transparentes e ajustáveis para convites, organizações, armazenamento e operações caras; rate limit; métricas; exclusão/exportação; linguagem pública coerente com hospedagem gerenciada. |

O inventário automatizado identificou **9 lacunas bloqueantes**, **1 controle
existente aprovado** e **1 integração que exige validação real**. Esse número é de
checagens do inventário, não uma contagem de vulnerabilidades. As verificações de
operação e abuso acima também são necessárias e não são certificadas pelo inventário.

## Arquitetura de implementação

1. **Identidade e associação:** manter `User` como identidade; criar `Organization`,
   `Membership` e `Invitation`. O perfil pertence à associação. Uma pessoa pode
   participar de várias organizações. Proprietário, administrador, editor, revisor
   e leitor têm responsabilidades explícitas. Administração da plataforma não deve
   ser confundida com administração de uma organização.
2. **Dados e autorização:** atribuir organização a contratos, modelos, workflows,
   auditoria, conexões e solicitações de assinatura. Centralizar a checagem de
   associação ativa e escopo em todas as consultas. Restrições relacionais devem
   impedir referências cruzadas. Chaves de API devem ficar vinculadas à organização;
   alterar a organização ativa não pode ampliar silenciosamente suas permissões.
3. **Migração:** criar uma organização legada para os dados atuais; associar usuários
   existentes sem trocar senhas; preservar documentos, versões, aprovações e
   auditoria. Validar contagens e hashes antes/depois. Novas organizações recebem
   modelos próprios. Ensaiar migração e rollback antes de alterar o serviço público.
4. **Integrações:** vincular OAuth state, conexões, remetentes e envelopes ao contexto
   da organização. Revalidar associação no callback e no envio. Webhooks devem
   resolver conta/envelope/organização por dados confiáveis, sem aceitar uma escolha
   arbitrária do cliente. Usar fila persistente/reconciliação para indisponibilidades.
5. **Produto gratuito:** entrada por cadastro e convite; organização ativa visível;
   equipe administrada pelo navegador; primeiro contrato guiado. A gratuidade do
   OpenCLM não inclui conta, licença ou consumo de assinatura do DocuSign. Não
   apresentar integrações desativadas como funcionalidades já disponíveis.

## Sequência até a Fenalaw

| Meta | Entrega | Aprovação objetiva |
| --- | --- | --- |
| 13–16/10 | Organizações, associações, autorização central e migração dos dados atuais | Testes negativos entre duas organizações em todas as superfícies, incluindo chaves e relações cruzadas |
| 17–19/10 | Cadastro, recuperação, convites, perfis e onboarding gratuito | Duas pessoas completam entrada, convite e primeiro contrato em navegadores independentes |
| 20–21/10 | DocuSign real, armazenamento final e recuperação de falhas | Fluxo real comprovado em duas contas; iniciar elegibilidade do aplicativo desde 13/10 |
| 22–23/10 | PostgreSQL do serviço, backup externo, monitoramento, carga e regressão responsiva | Restore completo, migração ensaiada, teste de concorrência, sem falha P0/P1 aberta |
| 24/10 | Verificação final e publicação | Evidências completas para os fluxos prometidos; rollback disponível; dependências externas resolvidas |

O caminho crítico é **isolamento → onboarding → homologação → publicação**.
Elegibilidade DocuSign e entrega de e-mails são dependências externas que devem ser
tratadas em paralelo ao desenvolvimento, sem esperar o dia da homologação.
Caso o DocuSign não esteja elegível, isso deve aparecer como pendência de escopo;
um botão de conexão desabilitado não satisfaz o requisito de integração pronta.

## DocuSign: dependência externa a resolver

A documentação oficial atual distingue integração privada, de terceiros, pública
e embarcada. Para um aplicativo público usado por clientes de diferentes contas,
o enquadramento indicado é **Public integration**, que exige participação no
programa de parceiros. A elegibilidade da conta concreta ainda não foi verificada.

O anúncio oficial de 03/10/2025 removeu a antiga exigência geral de 20 chamadas para
Go-Live; ela não deve ser usada como critério atual. Isso não elimina a necessidade
de testes funcionais nem a validação de elegibilidade em Apps & Keys. Referências:
[Go-Live](https://developers.docusign.com/platform/go-live/) e
[explicação oficial das modalidades e elegibilidade](https://community.docusign.com/go-live-70/no-more-20-api-calls-introducing-the-streamlined-integration-go-live-experience-in-apps-keys-25623).

Para esta instalação, o callback a cadastrar será
`https://legalops.dev/openclm/api/v1/integrations/docusign/callback` e o webhook será
`https://legalops.dev/openclm/api/v1/webhooks/docusign`. Credenciais devem ser
instaladas no ambiente do servidor, nunca no repositório, relatório ou navegador.

## Reprodução

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
npm ci
npx playwright install --with-deps chromium firefox webkit
npm run test:browser
uv run python -m scripts.postgres_readiness
uv run python -m scripts.production_readiness --output artifacts/readiness.json
```

O último comando retorna **2 quando há pendências**, **1 em erro de execução** e
0 apenas se todas as checagens forem aprovadas. Descobrir uma rota nova nunca a
aprova automaticamente: o inventário registra que sua jornada precisa de validação.
Ele não substitui os futuros testes de isolamento, carga e provedores reais.

Os scripts criam bancos descartáveis. O teste PostgreSQL exige Docker e usa somente
um container com nome único, dados em tmpfs e porta vinculada a 127.0.0.1; remove-o
ao terminar. O servidor de navegador também se limita a 127.0.0.1 e usa credenciais
sintéticas. As verificações mutáveis não devem ser apontadas para a base pública.

A CI passa a executar regressão de API, navegador e PostgreSQL, além de armazenar
os relatórios em `readiness-evidence`. A etapa de inventário aceita o código 2 para
publicar o diagnóstico sem confundir lacunas de produto com falhas da suíte atual.
**CI verde não equivale a autorização para lançamento público.**

Não foram executados teste de carga, teste em aparelho físico, auditoria completa
de acessibilidade, auditoria de segurança exaustiva, envio de e-mail ou assinatura
real. Nenhuma configuração, conta, senha ou dado de produção foi alterado nesta
rodada de avaliação.
