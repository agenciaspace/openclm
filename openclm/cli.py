import argparse
import getpass

from pydantic import ValidationError
from sqlalchemy import select

from .config import Settings
from .db import LEGACY_ORGANIZATION_ID, make_engine, session_factory
from .models import AuditEvent, Membership, Organization, Token, User
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
    create.add_argument("--organization-id", default=LEGACY_ORGANIZATION_ID)
    seed = commands.add_parser("seed")
    seed.add_argument("--organization-id", default=LEGACY_ORGANIZATION_ID)
    disable = commands.add_parser("disable-user")
    disable.add_argument("--email", required=True)
    reset = commands.add_parser("reset-password")
    reset.add_argument("--email", required=True)
    args = parser.parse_args()
    engine = make_engine(Settings().database_url)
    with session_factory(engine)() as db:
        if args.command == "seed":
            if args.organization_id != LEGACY_ORGANIZATION_ID and not db.get(
                Organization, args.organization_id
            ):
                parser.error("Organização não encontrada.")
            if db.get(Organization, args.organization_id):
                db.info.update(organization_id=args.organization_id, tenant_required=True)
            print(
                "Modelos e serviços preparados."
                if seed_templates(db)
                else "Modelos e serviços já existentes. Nenhuma alteração."
            )
            return
        user = db.scalar(select(User).where(User.email == args.email.lower()))
        if args.command == "disable-user":
            if not user:
                parser.error("Usuário não encontrado.")
            if db.scalar(select(Organization.id).where(Organization.owner_id == user.id)):
                parser.error("Transfira a propriedade das organizações antes de desativar a conta.")
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
                organization = db.get(Organization, args.organization_id)
                if not organization:
                    if args.organization_id != LEGACY_ORGANIZATION_ID or args.role != "admin":
                        parser.error("Crie a organização com um administrador primeiro.")
                    organization = Organization(
                        id=LEGACY_ORGANIZATION_ID, name="Minha organização", owner_id=user.id
                    )
                    db.add(organization)
                    db.flush()
                db.add(Membership(organization_id=organization.id, user_id=user.id, role=args.role))
            else:
                user.password_hash = passwords.hash(password)
        if args.command != "create-user":
            for token in db.scalars(select(Token).where(Token.user_id == user.id)):
                db.delete(token)
        db.flush()
        for membership in db.scalars(select(Membership).where(Membership.user_id == user.id)):
            db.add(
                AuditEvent(
                    organization_id=membership.organization_id,
                    action="admin.cli." + args.command,
                    details={"user_id": user.id},
                )
            )
        db.commit()
        print("Operação concluída.")
    engine.dispose()


if __name__ == "__main__":
    main()
