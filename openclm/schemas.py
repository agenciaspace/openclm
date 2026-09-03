import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

Role = Literal["admin", "editor", "reviewer", "viewer"]
Status = Literal[
    "draft",
    "in_review",
    "rejected",
    "approved",
    "sending",
    "signature_pending",
    "signed",
    "signature_declined",
    "archived",
]
Short = Annotated[str, Field(min_length=1, max_length=200)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def safe_document_characters(cls, value):
        def check(item):
            if isinstance(item, str) and re.search(
                r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", item
            ):
                raise ValueError("Unsupported control character in input")
            if isinstance(item, dict):
                for key, child in item.items():
                    check(key)
                    check(child)
            elif isinstance(item, list):
                for child in item:
                    check(child)

        check(value)
        return value


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Login(Input):
    model_config = ConfigDict(str_strip_whitespace=False)
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserCreate(Input):
    model_config = ConfigDict(str_strip_whitespace=False)
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=256)
    role: Role


class UserOut(Output):
    id: str
    email: str
    name: str
    role: Role
    active: bool


class Question(Input):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=200)
    type: Literal["text", "textarea", "number", "date", "email", "select", "boolean"] = "text"
    required: bool = True
    options: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=50
    )
    help_text: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def select_options(self):
        if self.type == "select" and (
            not self.options or len(set(self.options)) != len(self.options)
        ):
            raise ValueError("Select questions need distinct options")
        if self.type != "select" and self.options:
            raise ValueError("Only select questions accept options")
        return self


class Step(Input):
    name: str = Field(min_length=1, max_length=120)
    approver_id: str | None = None


class WorkflowCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    steps: list[Step] = Field(min_length=1, max_length=20)


class WorkflowOut(WorkflowCreate, Output):
    id: str
    created_at: datetime


class TemplateCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    body: str = Field(min_length=1, max_length=100000)
    questions: list[Question] = Field(min_length=1, max_length=100)
    workflow_id: str

    @model_validator(mode="after")
    def valid_placeholders(self):
        keys = [question.key for question in self.questions]
        if len(keys) != len(set(keys)):
            raise ValueError("Question keys must be unique")
        placeholders = re.findall(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}", self.body)
        if set(placeholders) - set(keys):
            raise ValueError("Every placeholder must reference a question key")
        remainder = re.sub(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}", "", self.body)
        if "{{" in remainder or "}}" in remainder or "{%" in self.body:
            raise ValueError("Only simple {{question_key}} placeholders are supported")
        return self


class TemplateOut(TemplateCreate, Output):
    id: str
    created_at: datetime


class ContractCreate(Input):
    title: Short
    counterparty: Short
    template_id: str
    answers: dict[str, Any]


class ContractEdit(Input):
    revision: int = Field(ge=1)
    title: Short
    counterparty: Short
    answers: dict[str, Any]


class Transition(Input):
    revision: int = Field(ge=1)
    action: Literal["submit", "approve", "reject", "archive"]
    comment: str = Field(default="", max_length=2000)


class ContractSummary(Output):
    id: str
    title: str
    counterparty: str
    template_id: str
    owner_id: str
    status: Status
    version: int
    revision: int
    current_step: int
    created_at: datetime
    updated_at: datetime


class ContractOut(ContractSummary):
    answers: dict[str, Any]
    content: str
    workflow_snapshot: list[Step]


class ContractPage(BaseModel):
    items: list[ContractSummary]
    total: int
    limit: int
    offset: int


class VersionOut(Output):
    version: int
    title: str
    counterparty: str
    content: str
    answers: dict[str, Any]
    sha256: str
    created_at: datetime


class AuditOut(Output):
    id: str
    actor_id: str | None
    contract_id: str | None
    action: str
    details: dict
    created_at: datetime


class ApiKeyCreate(Input):
    name: str = Field(min_length=1, max_length=80)
    days: int = Field(default=30, ge=1, le=365)


class ApiKeyOut(Output):
    id: str
    name: str
    expires_at: datetime


class ApiKeyCreated(ApiKeyOut):
    token: str


class Signer(Input):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr


class SignRequest(Input):
    revision: int = Field(ge=1)
    signers: list[Signer] = Field(min_length=1, max_length=10)
    consent_to_external_transfer: Literal[True]

    @model_validator(mode="after")
    def unique_signers(self):
        emails = [str(s.email).lower() for s in self.signers]
        if len(emails) != len(set(emails)):
            raise ValueError("Signer emails must be distinct")
        return self


class SignatureOut(Output):
    id: str
    envelope_id: str | None
    status: str
    document_version: int


class AIRequest(Input):
    task: Literal["summary", "risks", "questions"] = "summary"


class AIResponse(BaseModel):
    text: str
    model: str
    provider: Literal["ollama"] = "ollama"
    document_version: int
    requires_review: bool = True


def valid_date(value: str):
    return date.fromisoformat(value)
