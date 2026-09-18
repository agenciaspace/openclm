import ipaddress
import re
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./openclm.db"
    app_url: str = "http://localhost:8000"
    base_path: str = ""
    proxy_hostname: str = ""
    secure_cookies: bool = False
    session_hours: int = 12
    ai_enabled: bool = False
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    docusign_enabled: bool = False
    docusign_auth_server: str = "account-d.docusign.com"
    docusign_integration_key: str = ""
    docusign_client_secret: str = ""
    docusign_hmac_secret: str = ""
    salesforce_enabled: bool = False
    salesforce_login_url: str = "https://test.salesforce.com"
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    token_encryption_key: str = ""

    @property
    def public_url(self):
        return self.app_url.rstrip("/") + self.base_path

    @model_validator(mode="after")
    def validate_endpoints(self):
        if self.base_path and not re.fullmatch(r"/[a-z0-9]+(?:-[a-z0-9]+)*", self.base_path):
            raise ValueError("BASE_PATH must be empty or a single URL path segment")
        if self.proxy_hostname and not re.fullmatch(
            r"[a-z0-9]+(?:[.-][a-z0-9]+)*", self.proxy_hostname
        ):
            raise ValueError("PROXY_HOSTNAME must be a hostname")
        app = urlparse(self.app_url)
        if app.scheme not in {"http", "https"} or not app.hostname or app.path not in {"", "/"}:
            raise ValueError("APP_URL must be an HTTP(S) origin without a path")
        if app.scheme == "https" and not self.secure_cookies:
            raise ValueError("Set SECURE_COOKIES=true when using HTTPS")
        if self.ai_enabled:
            target = urlparse(self.ollama_url)
            local = target.hostname in {"localhost", "ollama", "host.docker.internal"}
            try:
                address = ipaddress.ip_address(target.hostname or "")
                local = (
                    address.is_loopback
                    or address in ipaddress.ip_network("10.0.0.0/8")
                    or address in ipaddress.ip_network("172.16.0.0/12")
                    or address in ipaddress.ip_network("192.168.0.0/16")
                    or address in ipaddress.ip_network("fc00::/7")
                )
            except ValueError:
                pass
            if (
                target.scheme not in {"http", "https"}
                or not local
                or target.username
                or target.query
                or target.fragment
                or target.path not in {"", "/"}
            ):
                raise ValueError("OLLAMA_URL must point to a local/private server")
            if "cloud" in self.ollama_model.lower() or "/" in self.ollama_model:
                raise ValueError("Cloud models are not allowed")
        if self.docusign_auth_server not in {"account-d.docusign.com", "account.docusign.com"}:
            raise ValueError("Invalid DocuSign OAuth server")
        if self.docusign_enabled and not all(
            [
                self.docusign_integration_key,
                self.docusign_client_secret,
                self.docusign_hmac_secret,
                self.token_encryption_key,
            ]
        ):
            raise ValueError(
                "DocuSign requires application client ID/secret, integrator HMAC secret and TOKEN_ENCRYPTION_KEY"
            )
        if self.docusign_enabled:
            Fernet(self.token_encryption_key.encode())
        validate_salesforce_origin(self.salesforce_login_url)
        if self.salesforce_enabled:
            if not all(
                [
                    self.salesforce_client_id,
                    self.salesforce_client_secret,
                    self.token_encryption_key,
                ]
            ):
                raise ValueError("Salesforce requires client ID/secret and TOKEN_ENCRYPTION_KEY")
            Fernet(self.token_encryption_key.encode())
        return self


def validate_salesforce_origin(value):
    target = urlparse(value)
    host = target.hostname or ""
    if (
        target.scheme != "https"
        or not (
            host in {"login.salesforce.com", "test.salesforce.com"}
            or host.endswith(".my.salesforce.com")
        )
        or target.username
        or target.password
        or target.port not in {None, 443}
        or target.path not in {"", "/"}
        or target.query
        or target.fragment
    ):
        raise ValueError("Invalid Salesforce origin")
    return value.rstrip("/")
