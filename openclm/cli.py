import argparse
import getpass

from pydantic import ValidationError
from sqlalchemy import select

from .config import Settings
from .db import make_engine, session_factory
from .models import AuditEvent, Token, User
from .schemas import UserCreate
from .security import passwords
from .seed import seed_templates


def main():
    parser = argparse.ArgumentParser(description="OpenCLM administration on your own server")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument(
        "--role", choices=["admin", "editor", "reviewer", "viewer"], default="admin"
    )
    commands.add_parser("seed")
    disable = commands.add_parser("disable-user")
    disable.add_argument("--email", required=True)
    reset = commands.add_parser("reset-password")
    reset.add_argument("--email", required=True)
    args = parser.parse_args()
    engine = make_engine(Settings().database_url)
    with session_factory(engine)() as db:
        if args.command == "seed":
            print(
                "Modelos demonstrativos criados."
                if seed_templates(db)
                else "Modelos já existentes. Nenhuma alteração."
            )
            return
        user = db.scalar(select(User).where(User.email == args.email.lower()))
        if args.command == "disable-user":
            if not user:
                parser.error("Usuário não encontrado.")
            if (
                user.role == "admin"
                and len(
                    db.scalars(
                        select(User).where(User.role == "admin", User.active.is_(True))
                    ).all()
                )
                <= 1
            ):
                parser.error("Crie outro administrador antes de desativar o último.")
            user.active = False
        else:
            if args.command == "create-user" and user:
                parser.error("E-mail já cadastrado.")
            if args.command == "reset-password" and not user:
                parser.error("Usuário não encontrado.")
            password = getpass.getpass("Senha (mínimo 12 caracteres): ")
            if password != getpass.getpass("Repita a senha: "):
                parser.error("As senhas não coincidem.")
            try:
                payload = UserCreate(
                    email=args.email,
                    name=args.name if args.command == "create-user" else user.name,
                    role=args.role if args.command == "create-user" else user.role,
                    password=password,
                )
            except ValidationError:
                parser.error("Verifique e-mail, nome e senha (12–256 caracteres).")
            if args.command == "create-user":
                user = User(
                    email=str(payload.email).lower(),
                    name=payload.name,
                    role=payload.role,
                    password_hash=passwords.hash(password),
                )
                db.add(user)
                db.flush()
            else:
                user.password_hash = passwords.hash(password)
        if args.command != "create-user":
            for token in db.scalars(select(Token).where(Token.user_id == user.id)):
                db.delete(token)
        db.add(AuditEvent(action="admin.cli." + args.command, details={"user_id": user.id}))
        db.commit()
        print("Operação concluída.")
    engine.dispose()


if __name__ == "__main__":
    main()
