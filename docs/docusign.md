# Conectar com DocuSign

## Experiência da pessoa usuária

1. Abra **Configurações** e clique em **Conectar com DocuSign**.
2. Entre no DocuSign e autorize o aplicativo da sua organização.
3. Retorne automaticamente ao OpenCLM. O cartão mostra o nome da conta padrão conectada.
4. Em um contrato aprovado, escolha **Enviar ao DocuSign**, informe signatários e confirme o compartilhamento.

Não são necessárias chaves RSA, account IDs ou tokens copiados pela pessoa usuária. A conexão é individual: os envios usam a conta de quem enviou. Nesta versão, é escolhida a conta padrão retornada pelo DocuSign, sem seletor de contas múltiplas.

## Configuração única de uma instalação

1. Crie um aplicativo em **Apps and Keys** no DocuSign Developer. Use **Confidential Authorization Code Grant** com integração/client ID e client secret.
2. Cadastre exatamente o callback da instalação:

   ```text
   https://clm.suaempresa.com/api/v1/integrations/docusign/callback
   ```

3. Na conta DocuSign proprietária do aplicativo, configure a chave HMAC de Connect do integrador. O OpenCLM envia `integratorManaged=true` e `includeHMAC=true` nas notificações por envelope, de forma que as contas conectadas não precisem configurar suas próprias chaves. Configure esse recurso conforme a disponibilidade da conta/ambiente DocuSign.
4. Configure o servidor:

   ```dotenv
   APP_URL=https://clm.suaempresa.com
   SECURE_COOKIES=true
   DOCUSIGN_ENABLED=true
   DOCUSIGN_AUTH_SERVER=account-d.docusign.com
   DOCUSIGN_INTEGRATION_KEY=client-id-do-aplicativo
   DOCUSIGN_CLIENT_SECRET=segredo-do-aplicativo
   DOCUSIGN_HMAC_SECRET=chave-hmac-da-conta-do-integrador
   TOKEN_ENCRYPTION_KEY=chave-gerada-por-scripts-configure.py
   ```

5. Reinicie a aplicação. O botão de conexão estará disponível para administradores e editores.

Use o ambiente de demonstração para homologação. Em produção, conclua o processo de Go-Live aplicável ao seu aplicativo, configure as credenciais do ambiente de produção e use `DOCUSIGN_AUTH_SERVER=account.docusign.com`. O cliente precisa de uma conta/plano DocuSign com o acesso necessário à API. O software não fornece conta, plano, créditos de assinatura ou aprovação de Go-Live.

O envio exige um `APP_URL` HTTPS acessível ao DocuSign. O login OAuth em localhost pode ser desenvolvido com um callback local previamente registrado, mas a entrega de webhooks exige um endereço acessível ao provedor.

## Como o acesso é protegido

- O botão inicia um fluxo Authorization Code de cliente confidencial, com client secret mantido no servidor.
- `state` aleatório, armazenado como hash, expira em 10 minutos, é vinculado à sessão e só pode ser consumido uma vez.
- O cookie HttpOnly usa SameSite=Lax para o retorno GET do OAuth; escritas com sessão exigem cabeçalho CSRF e origem válida.
- Escopos solicitados: `signature extended`. A renovação ocorre quando o token está prestes a expirar. Longos períodos sem uso, revogação ou política do provedor podem exigir nova conexão.
- Access/refresh tokens são criptografados com Fernet antes de ir ao banco. Não aparecem na API, no navegador nem no histórico.
- O endereço da API é descoberto pelo `/oauth/userinfo`, aceitando apenas hosts HTTPS DocuSign. Não é fornecido pelo navegador.
- Cada solicitação guarda o remetente e a conta; respostas de outra conta não atualizam o contrato.
- O webhook valida HMAC no corpo bruto antes de interpretar o JSON. Repetições idênticas não são reaplicadas; eventos atrasados não desfazem estados terminais.

Mantenha uma cópia segura de `TOKEN_ENCRYPTION_KEY` fora do repositório. A perda da chave exige reconectar as contas. A rotação exige recriptografar as conexões ou removê-las e reconectá-las; não troque essa chave durante uma operação sem planejar a migração.

**Desconectar** apaga os tokens locais e invalida autorizações locais ainda pendentes. Não cancela envelopes já enviados nem revoga o consentimento no portal do DocuSign. A pessoa pode revogar o consentimento também no provedor.

## Envio e tratamento de falhas

O servidor reserva uma única solicitação por contrato **antes** de chamar o provedor e usa seu ID como `transactionId`. O documento enviado corresponde à versão aprovada, acrescida do bloco de assinaturas; as âncoras têm um identificador único por envio.

Se a resposta ao envio for incerta, o contrato fica em `sending` e a solicitação em `dispatch_uncertain`. Não é criado outro envelope automaticamente. Consulte a conta DocuSign e use a conciliação após localizar o envelope:

```http
POST /api/v1/contracts/{contract_id}/signature/reconcile?envelope_id={envelope_uuid}
Authorization: Bearer CHAVE_DE_ADMINISTRADOR
```

A conciliação verifica, pela API autenticada, o campo `openclm_request_id` no envelope. Também pode atualizar um envelope conhecido caso um webhook tenha sido perdido. O remetente precisa manter a conta original conectada. Se nenhum envelope existir após um envio incerto, a liberação da reserva exige intervenção administrativa no banco nesta base; não há um botão de reenvio que possa duplicar assinaturas.

Esta versão guarda o estado da assinatura e a versão original. O download automático do PDF concluído e do certificado de conclusão está no roadmap; o DOCX baixado no OpenCLM não é apresentado como documento assinado.

## Referências oficiais

- [Confidential Authorization Code Grant](https://developers.docusign.com/platform/auth/confidential-authcode-get-token/)
- [Refresh tokens e escopo extended](https://www.docusign.com/blog/developers/authorization-code-grant-refresh-tokens)
- [HMAC gerenciado pelo integrador](https://www.docusign.com/blog/developers/introducing-hmac-partners-docusign-connect)
- [Notificações JSON SIM e HMAC](https://www.docusign.com/blog/developers/event-notifications-using-json-sim-and-hmac)
- [Criação de envelopes](https://developers.docusign.com/docs/esign-rest-api/reference/envelopes/envelopes/create/)
