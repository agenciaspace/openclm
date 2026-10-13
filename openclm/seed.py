from sqlalchemy import select

from .models import Template, Workflow
from .schemas import Question


def seed_templates(db, *, commit=True):
    from .db import LEGACY_ORGANIZATION_ID
    from .models import Membership, Organization, User

    if not db.info.get("organization_id"):
        if not db.get(Organization, LEGACY_ORGANIZATION_ID):
            users = db.scalars(select(User).order_by(User.created_at, User.id)).all()
            if not users:
                return False
            owner = next((u for u in users if u.role == "admin" and u.active), users[0])
            db.add(
                Organization(id=LEGACY_ORGANIZATION_ID, name="Minha organização", owner_id=owner.id)
            )
            db.flush()
            for user in users:
                db.add(
                    Membership(
                        organization_id=LEGACY_ORGANIZATION_ID,
                        user_id=user.id,
                        role=user.role,
                        active=user.active,
                    )
                )
            db.flush()
        db.info["organization_id"] = LEGACY_ORGANIZATION_ID
        db.info["tenant_required"] = True
    if db.scalar(select(Template.id).limit(1)):
        return False
    workflow = Workflow(
        name="Revisão jurídica", steps=[{"name": "Aprovação jurídica", "approver_id": None}]
    )
    db.add(workflow)
    db.flush()
    common = [
        Question(key="party_name", label="Nome da contratante"),
        Question(key="party_id", label="CPF/CNPJ da contratante"),
        Question(key="counterparty_name", label="Nome da contratada"),
        Question(key="counterparty_id", label="CPF/CNPJ da contratada"),
        Question(key="effective_date", label="Data de início", type="date"),
        Question(key="term_months", label="Prazo em meses", type="number"),
        Question(key="jurisdiction", label="Cidade / foro"),
    ]
    db.add(
        Template(
            name="Prestação de serviços",
            description="Modelo demonstrativo com objeto, valor e prazo. Adapte ao seu caso antes de usar.",
            workflow_id=workflow.id,
            questions=[
                q.model_dump()
                for q in common
                + [
                    Question(key="scope", label="Descrição dos serviços", type="textarea"),
                    Question(key="fee", label="Valor mensal e moeda (ex.: R$ 5.000,00)"),
                ]
            ],
            body="CONTRATO DE PRESTAÇÃO DE SERVIÇOS\n\n1. PARTES\n{{party_name}}, inscrita sob {{party_id}}, denominada CONTRATANTE, e {{counterparty_name}}, inscrita sob {{counterparty_id}}, denominada CONTRATADA.\n\n2. OBJETO\n{{scope}}\n\n3. REMUNERAÇÃO\nA remuneração mensal será de {{fee}}. As condições de faturamento e pagamento deverão ser definidas pelas partes antes da assinatura.\n\n4. VIGÊNCIA\nInício em {{effective_date}}, pelo prazo de {{term_months}} meses.\n\n5. DISPOSIÇÕES FINAIS\nAs partes deverão definir as regras de confidencialidade, proteção de dados, responsabilidade e rescisão aplicáveis à contratação.\n\n6. FORO\n{{jurisdiction}}.\n\nMODELO DEMONSTRATIVO — completar e revisar antes da assinatura.",
        )
    )
    db.add(
        Template(
            name="Acordo de confidencialidade",
            description="Ponto de partida para um NDA bilateral. Exige adaptação e revisão.",
            workflow_id=workflow.id,
            questions=[
                q.model_dump()
                for q in common
                + [
                    Question(
                        key="purpose", label="Finalidade da troca de informações", type="textarea"
                    )
                ]
            ],
            body="ACORDO DE CONFIDENCIALIDADE\n\n1. PARTES\n{{party_name}} ({{party_id}}) e {{counterparty_name}} ({{counterparty_id}}).\n\n2. FINALIDADE\nAs informações serão compartilhadas para: {{purpose}}.\n\n3. CONFIDENCIALIDADE\nAs partes se comprometem a proteger as informações recebidas e a utilizá-las exclusivamente para a finalidade acima. Deverão definir o escopo das informações protegidas, as exceções e as condições de divulgação obrigatória.\n\n4. PRAZO\nEste acordo terá início em {{effective_date}}, com vigência de {{term_months}} meses. As partes deverão definir a duração do dever de sigilo após o encerramento.\n\n5. FORO\n{{jurisdiction}}.\n\nMODELO DEMONSTRATIVO — completar e revisar antes da assinatura.",
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return True
