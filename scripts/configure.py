"""Create installation secrets locally; never overwrite an existing configuration."""

import base64
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
content = (root / ".env.example").read_text()
content = content.replace(
    "POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}\n"
)
key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
content = content.replace("TOKEN_ENCRYPTION_KEY=\n", f"TOKEN_ENCRYPTION_KEY={key}\n")
try:
    descriptor = os.open(root / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit(".env já existe. Nenhum arquivo foi alterado.") from None
with os.fdopen(descriptor, "w") as file:
    file.write(content)
print(
    ".env criado com segredos exclusivos. Guarde uma cópia segura antes de configurar integrações."
)
