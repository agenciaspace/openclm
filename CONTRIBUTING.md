# Contribuir

1. Abra uma issue descrevendo o problema ou incremento pretendido.
2. Crie uma branch e instale as dependências com `uv sync --frozen`.
3. Faça a alteração com testes para regras de domínio, permissões, migrações e integrações afetadas.
4. Execute `uv run pytest -q`, `uv run ruff check .` e `uv run ruff format --check .`.
5. Envie um pull request com comportamento, validação e limitações.

Preserve os princípios de hospedagem própria e ausência de telemetria. Integrações externas devem ser opcionais, explicitar transferência de dados e manter credenciais no servidor. Não inclua documentos reais, senhas ou dados de clientes em fixtures.

Mudanças de persistência exigem migrações Alembic. Não altere uma migração já publicada: crie uma nova. Templates e workflows referenciados por contratos devem continuar reprodutíveis. Não permita que IA aprove contratos ou execute instruções contidas nos documentos.

As contribuições são distribuídas sob a licença MIT deste repositório.
