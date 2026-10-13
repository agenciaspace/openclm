# OpenIntake

Entrada: https://legalops.dev/openintake (abre a aplicação compartilhada em `/openclm/intake`). Usa as mesmas contas, organizações e sessão do OpenCLM. Nenhuma solicitação ou anexo é público.

## Funcionalidades entregues

- Catálogo por organização: Contratos/NDA, Privacidade/Dados, Pessoas/Trabalho e Consulta Jurídica. Administradores podem criar, editar e pausar serviços, montar perguntas tipadas, escolher um responsável padrão e configurar o prazo em horas corridas.
- Solicitação guiada em três passos: serviço, informações e conferência. Validação no servidor, protocolo único e proteção contra reenvio duplicado. O pedido preserva a versão do formulário preenchido.
- Fila pesquisável com status, prioridade, área, responsável, prazo e indicadores. O prazo solicitado pelo negócio é apresentado separadamente do prazo de atendimento. A distribuição usa a configuração do serviço; se o responsável perdeu acesso, o pedido vai para a fila sem responsável.
- Histórico, mensagens, resposta final, notas internas do jurídico e anexos autenticados. Um pedido aguardando informações volta à triagem quando o solicitante responde. Orientações aprovadas podem ser publicadas pela própria equipe; não há conteúdo jurídico inventado ou respostas automáticas apresentadas como parecer.
- Vínculo com contratos da mesma organização no OpenCLM e API autenticada para integrações próprias.

## Perfis e privacidade

| Perfil da organização | OpenIntake |
| --- | --- |
| Administrador | Catálogo, orientações, distribuição e todos os pedidos da organização |
| Revisor | Atendimento de todos os pedidos; notas internas; vincular contratos |
| Editor ou Leitor | Criar e acompanhar somente os próprios pedidos; mensagens e anexos |

Permissões do OpenCLM permanecem diferentes: dentro da organização, membros podem consultar contratos. Vincular um pedido a um contrato não torna conversas/anexos do intake visíveis aos demais solicitantes. Notas internas nunca aparecem para solicitantes sem perfil jurídico. Consultas diretas por ID e downloads verificam ambas as fronteiras: organização e acesso ao pedido.

Limites iniciais: 100 serviços e 100 orientações por organização, 10.000 solicitações, 30 novos pedidos por pessoa/hora, 500 mensagens por pedido, cinco anexos de até 1 MB por pedido e 100 MB de anexos por organização. Anexos são baixados como arquivos, não renderizados como HTML. Não há antivírus embutido ou extração automática do conteúdo.

## Referências e escopo

A referência funcional do [Legora Intake](https://legora.com/product/intake) é transformar pedidos em trabalho acompanhado com responsabilidade do jurídico. A referência do [Checkbox](https://www.checkbox.ai/solutions/ai-legal-intake-and-triage) é combinar entrada, encaminhamento, autosserviço e visibilidade. Esta implementação tem interface e código próprios, sem reproduzir marca ou conteúdo desses produtos.

Esta é a primeira versão funcional, não uma equivalência completa com plataformas comerciais. Ainda não inclui agente de IA que execute tarefas, captura de e-mail/Slack/Teams, MCP, regras condicionais entre várias áreas, lembretes externos ou extração de documentos. O canal LISA Ask específico aguarda identificação para comparação. DocuSign permanece fora desta entrega.

## Verificação e operação

`tests/test_intake.py` cobre privacidade entre solicitantes e organizações, notas internas, distribuição, prazos, revisão concorrente, conclusão, anexos, publicação de orientações e vínculo contratual. A suíte roda em SQLite e PostgreSQL. `e2e/intake.spec.js` percorre solicitação, upload, atendimento e resposta em Chrome, Firefox, celular e iPad WebKit. Banco/anexos entram no mesmo backup PostgreSQL privado do OpenCLM.
