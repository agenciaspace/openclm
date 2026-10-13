"""Run an isolated, synthetic browser fixture. Never binds to a public interface."""

import tempfile

import uvicorn

from openclm.config import Settings
from openclm.db import Base
from openclm.main import create_app
from openclm.models import User
from openclm.security import passwords
from openclm.seed import seed_templates
from tests.conftest import PASSWORD


def main():
    with tempfile.TemporaryDirectory(prefix="openclm-browser-") as directory:
        app = create_app(
            Settings(
                _env_file=None,
                database_url=f"sqlite:///{directory}/browser.db",
                app_url="http://127.0.0.1:38787",
                base_path="/openclm",
                secure_cookies=False,
                ai_enabled=False,
                docusign_enabled=False,
                salesforce_enabled=False,
            )
        )
        Base.metadata.create_all(app.state.engine)
        with app.state.sessions() as db:
            for role in ["admin", "editor", "reviewer", "viewer"]:
                db.add(
                    User(
                        email=f"{role}@example.com",
                        name=role.title(),
                        role=role,
                        password_hash=passwords.hash(PASSWORD),
                    )
                )
            db.commit()
            seed_templates(db)
        uvicorn.run(app, host="127.0.0.1", port=38787, access_log=False)


if __name__ == "__main__":
    main()
