# Segurança

OpenCLM permite contas e organizações independentes. A implantação deve validar TLS, backups, exposição de rede e permissões para seu uso. Apenas a versão corrente recebe correções neste estágio do projeto.

Não publique documentos, credenciais ou provas de exploração com dados reais em issues. Use o recurso privado **Report a vulnerability** do GitHub quando disponível. Se não estiver habilitado, abra uma issue sem detalhes da vulnerabilidade pedindo um canal privado ao mantenedor; não exponha o material sensível.

Limites conhecidos:

- Dados isolados por organização. Dentro dela, todos os membros consultam os contratos; não há permissões por documento.
- Perfis de acesso simples; sem SSO, MFA ou permissões por documento.
- Dados contratuais dependem de criptografia de disco/backup da infraestrutura.
- Histórico da aplicação não impede manipulação por um administrador do banco.
- Integrações precisam de homologação com as contas e rede da instalação.
- APIs de leitura e IA ainda não têm cotas individuais ou controle distribuído de abuso.

Não registre corpos de contratos, senhas, tokens OAuth ou códigos de callback em logs. Segredos e bancos locais devem permanecer fora do Git.
