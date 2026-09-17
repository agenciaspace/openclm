# Salesforce

The integration connects an individual OpenCLM editor/admin to Salesforce using OAuth authorization code + PKCE. It reads opportunities and records a link on an existing OpenCLM contract. It does not modify Salesforce records or automatically import documents, contacts, or all opportunities.

## Configure

1. Create an External Client App in your Salesforce sandbox/Developer org. Enable the OAuth web server flow, require PKCE and the client secret, and grant `api` and `refresh_token` scopes. The connecting user must have API access and permission to read Opportunity and related Account names.
2. Register the exact callback `https://YOUR-OPENCLM/api/v1/integrations/salesforce/callback`. For the LegalOps demo: `https://openclm.leonn.dev/api/v1/integrations/salesforce/callback`.
3. Set `SALESFORCE_ENABLED=true`, `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`, `SALESFORCE_LOGIN_URL` and `TOKEN_ENCRYPTION_KEY` in the server environment. Use `https://test.salesforce.com` for sandbox, `https://login.salesforce.com` for production, or your HTTPS `*.my.salesforce.com` origin. Restart only the OpenCLM service.
4. In **Configurações → Salesforce**, connect your account. Consent happens on Salesforce.
5. Open a contract → **Vincular oportunidade** → search by name → **Vincular**. The opportunity link appears beside the approval workflow, and the action is recorded in the audit history.

Search returns at most 25 recent matching opportunities. Each user has their own encrypted connection; all users in this single-organization installation can see contract links. Only the contract owner or an administrator can change a link. Refresh tokens are renewed on an expired session response. Disconnect removes locally stored authorization but retains contract links; revoke app authorization in Salesforce to invalidate credentials there too.

REST API version: v66.0. Automated tests mock the provider. A real sandbox connection is required to verify app policy, permissions and token behavior before production use.

References: [OAuth](https://developer.salesforce.com/docs/platform/api-rest/guide/quickstart-oauth.html), [REST API guide](https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/api_rest.pdf).
