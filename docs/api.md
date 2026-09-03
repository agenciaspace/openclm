# API v1

O contrato completo é gerado em `/openapi.json`; `/docs` é um explorador servido localmente. IDs são UUIDs, nomes de campos usam inglês e datas de registros são UTC (representadas sem offset nesta versão). Campos desconhecidos nos corpos de escrita são rejeitados.

## Autenticação

No navegador, login cria cookie HttpOnly. Inclua `X-OpenCLM: 1` nas escritas com sessão e use a mesma origem da aplicação.

```http
POST /api/v1/auth/login
Content-Type: application/json
X-OpenCLM: 1

{"email":"admin@suaempresa.com","password":"SUA_SENHA"}
```

Em **Configurações → Chaves da API**, crie uma chave e guarde o valor exibido uma única vez. Alternativamente, use `POST /auth/api-keys` com sessão autenticada. Clientes de integração usam:

```sh
curl http://localhost:8000/api/v1/contracts \
  -H "Authorization: Bearer $OPENCLM_API_KEY"
```

A variável `OPENCLM_API_KEY` deve vir de um gerenciador de segredos. Chaves expiram em até 365 dias e são revogáveis. Uma chave herda as permissões atuais da conta, sem permitir elevação de perfil.

## Recursos

| Método e caminho, após `/api/v1` | Uso |
| --- | --- |
| `GET /contracts?q=&status=&limit=30&offset=0` | Busca/paginação; máximo 100 por página |
| `POST /contracts` | Criar contrato a partir de modelo e respostas |
| `GET /contracts/{id}` | Dados, respostas, conteúdo e workflow |
| `PATCH /contracts/{id}` | Nova versão de rascunho/devolvido |
| `GET /contracts/{id}/answers` | Respostas estruturadas e versão |
| `GET /contracts/{id}/versions` | Versões preservadas |
| `GET /contracts/{id}/document?format=docx&version=1` | Baixar DOCX; `txt` também disponível |
| `POST /contracts/{id}/transitions` | `submit`, `approve`, `reject` ou `archive` |
| `GET/POST /templates` | Modelos com perguntas tipadas |
| `GET /templates/{id}/questions` | Schema do formulário |
| `GET/POST /workflows` | Workflows sequenciais |
| `GET/POST /users` | Equipe; criação exige administrador |
| `GET /audit?contract_id={id}&limit=50&offset=0` | Histórico do contrato; global exige administrador |
| `POST /contracts/{id}/ai` | Tarefa local `summary`, `risks` ou `questions` |
| `GET /integrations/docusign/status` | Estado da conexão do usuário, sem tokens |
| `POST /integrations/docusign/connect` | URL de autorização, exige sessão de navegador |
| `DELETE /integrations/docusign/connection` | Remover tokens locais |
| `POST /contracts/{id}/signature` | Enviar versão aprovada com consentimento explícito |
| `GET /contracts/{id}/signature` | Estado do envelope |
| `POST /contracts/{id}/signature/reconcile?envelope_id={uuid}` | Conferir conta, vínculo e estado no provedor |

## Exemplo de contrato

Crie um modelo com a pergunta `empresa` e texto `Contrato com {{empresa}}.`. Use seu ID real no corpo abaixo:

```json
{
  "title": "Prestação de serviços — Acme",
  "counterparty": "Acme Ltda",
  "template_id": "UUID_DO_MODELO",
  "answers": {"empresa": "Acme Ltda"}
}
```

As chaves em `answers` precisam corresponder às perguntas do modelo. Números devem ser números JSON; booleanos devem ser `true` ou `false`; datas, `YYYY-MM-DD`. Perguntas opcionais ausentes viram `null`. Respostas desconhecidas ou tipos incorretos retornam 422.

## Concorrência e erros

Consulte o contrato e envie sua `revision` atual em edições/transições:

```json
{"action":"submit","revision":1,"comment":"Pronto para revisão"}
```

Se outra pessoa mudar o contrato, a operação retorna 409. Consulte novamente antes de tentar. A API não aceita saltar diretamente para aprovado/assinado.

| Código | Significado |
| --- | --- |
| 401 | Login/chave ausente, inválido ou expirado |
| 403 | Perfil, autor, aprovador ou origem sem permissão |
| 404 | Recurso não encontrado |
| 409 | Estado inválido, revisão desatualizada, duplicação ou conexão a recuperar |
| 413 | Corpo acima de 2 MB ou documento além do contexto de IA |
| 422 | Dados, perguntas ou formulário inválidos |
| 429 | Limite de tentativas de login |
| 502 | Integração externa indisponível ou resultado do envio incerto |
| 503 | Recurso desativado, banco/serviço indisponível ou webhook a repetir |

Envios DocuSign têm reserva única por contrato e `transactionId`. Criação comum de contratos não possui `Idempotency-Key` nesta versão; uma nova chamada POST cria outro contrato. Integrações devem controlar retentativas conforme o recurso.
