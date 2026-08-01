"""Configuração publicável de campos por workspace e workflow."""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.admin_auth import _admin_token_is_valid
from app.core.database import supabase
from app.modules.admin_config import _read_raw_row
from app.modules.portal_session import require_portal_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspaces", tags=["form-config"])

Requirement = Literal["optional", "on_submit", "on_step"]


def _field(
    step: str,
    label: str,
    requirement: Requirement = "optional",
    *,
    hint: str = "",
    placeholder: str = "",
    locked: bool = False,
    lock_reason: str = "",
) -> Dict[str, Any]:
    return {
        "step": step,
        "label": label,
        "hint": hint,
        "placeholder": placeholder,
        "visible": True,
        "requirement": requirement,
        "locked": locked,
        "lock_reason": lock_reason,
    }


RELEASE_INTAKE_FIELDS: Dict[str, Dict[str, Any]] = {
    "responsibleName": _field("identificacao", "Seu nome", "on_step", hint="Como você gostaria de ser chamado(a)?", placeholder="Ex.: Marina Duarte"),
    "responsibleEmail": _field("identificacao", "Seu e-mail", "on_step", hint="Usado para rascunhos, confirmações e atualizações.", placeholder="voce@exemplo.com", locked=True, lock_reason="Necessário para identificar o responsável e entregar os links do formulário."),
    "projectName": _field("projeto", "Nome do projeto", "on_step", hint="Título do single, EP ou álbum como deve aparecer nas plataformas.", placeholder="Ex.: Ciranda Elétrica"),
    "releaseType": _field("projeto", "Tipo de lançamento", "on_step"),
    "releaseDate": _field("projeto", "Data de lançamento", "on_step", hint="Recomendamos pelo menos 21 dias de antecedência."),
    "genre": _field("projeto", "Gênero musical", "on_step", placeholder="Selecione"),
    "coverFileName": _field("projeto", "Capa do lançamento", hint="Quadrada, mínimo 1500×1500 px (ideal 3000×3000)."),
    "videoLink": _field("projeto", "Link do vídeo", hint="Clipe, visualizer ou lyric video já publicado ou agendado.", placeholder="https://youtube.com/..."),
    "videoDate": _field("projeto", "Data do vídeo"),
    "additionalFiles": _field("projeto", "Link do kit visual", hint="Pasta com thumbs, banners, fotos e peças de divulgação.", placeholder="https://drive.google.com/..."),
    "notes": _field("projeto", "Observações do projeto", hint="Contexto, referências, restrições de datas ou territórios.", placeholder="Conte pra gente..."),
    "track.title": _field("faixas", "Nome da faixa", "on_step", placeholder="Título da música"),
    "track.mainArtists": _field("faixas", "Artistas principais", "on_step", hint="Busque no cadastro da Atabaque ou convide um novo artista.", placeholder="Digite o nome artístico…"),
    "track.featArtists": _field("faixas", "Participações (feats)", hint="Deixe em branco se não houver.", placeholder="Ex.: Zé Raminho"),
    "track.composers": _field("faixas", "Compositores e autores", "on_step", hint="Necessário para registro do ISRC e créditos editoriais.", placeholder="Nome completo de cada autor"),
    "track.performers": _field("faixas", "Intérpretes", hint="Quem executa a gravação.", placeholder="Ex.: Alaíde Tropical"),
    "track.hasISRC": _field("faixas", "A música já tem ISRC?", "on_step"),
    "track.isrc": _field("faixas", "Código ISRC", "on_step", placeholder="BR___0000000"),
    "track.producer": _field("faixas", "Produtor fonográfico", "on_step", hint="Pessoa ou estúdio responsável pela gravação.", placeholder="Ex.: Estúdio Pedra Selva"),
    "track.profiles": _field("faixas", "Perfis de artista", hint="Informe perfis a criar e links de perfis existentes."),
    "track.audio": _field("faixas", "Áudio da faixa", "on_step", hint="Master em WAV ou FLAC."),
    "focusTrack": _field("faixas", "Faixa foco", "on_step", hint="Marque a faixa que guiará o plano de divulgação."),
    "marketingNumbers": _field("marketing", "Números e resultados relevantes", hint="Shows, streams, audiência, hits, colaborações ou imprensa."),
    "focusDescription": _field("marketing", "Foco do artista e do lançamento", "on_step"),
    "goals": _field("marketing", "Objetivos do lançamento", "on_step"),
    "hasMarketingBudget": _field("marketing", "Há verba para promoção?"),
    "marketingBudget": _field("marketing", "Valor ou faixa de investimento", placeholder="Ex.: R$ 5.000 a R$ 8.000"),
    "dateFlexibility": _field("marketing", "Flexibilidade da data de lançamento"),
    "hasSpecialGuests": _field("marketing", "O lançamento tem participações especiais?", "on_step"),
    "guestsBio": _field("marketing", "Mini bio das participações", "on_step"),
    "guestsPromote": _field("marketing", "As participações vão divulgar junto?"),
    "promoParticipants": _field("marketing", "Participantes na promoção", placeholder="Ex.: Zé Raminho — Instagram e imprensa"),
    "influencers": _field("marketing", "Influenciadores, marcas e parceiros", placeholder="Ex.: @canaldemusica, marca Y — em conversa"),
    "consentTruth": _field("revisao", "Declaração de veracidade e consentimento", "on_submit", locked=True, lock_reason="Obrigatório para proteção jurídica e tratamento dos dados."),
}

RELEASE_INTAKE_STEPS = {
    "identificacao": "Identificação",
    "projeto": "Projeto",
    "faixas": "Faixas",
    "marketing": "Marketing",
    "revisao": "Revisão",
}


def _catalog_field(step: str, label: str, required: bool = False) -> Dict[str, Any]:
    return _field(step, label, "on_step" if required else "optional")


def _catalog(specs: list[tuple[str, str, str, bool]]) -> Dict[str, Dict[str, Any]]:
    return {
        key: _catalog_field(step, label, required)
        for key, step, label, required in specs
    }


RIGHTS_CLEARANCE_STEPS = {
    "solicitante": "Solicitante", "formato": "Formato", "contexto": "Contexto",
    "faixas": "Faixas", "escopo": "Escopo musical", "escopo_av": "Escopo audiovisual",
    "assets": "Referências musicais", "assets_av": "Referências audiovisuais", "revisao": "Revisão",
}
RIGHTS_CLEARANCE_FIELDS = _catalog([
    ("requester_name", "solicitante", "Nome do solicitante", True),
    ("requester_email", "solicitante", "E-mail do solicitante", True),
    ("requester_company", "solicitante", "Empresa do solicitante", True),
    ("requester_role", "solicitante", "Cargo ou papel no projeto", True),
    ("clearance_format", "formato", "Formato de rights clearance", True),
    ("project_title", "contexto", "Título do projeto", True),
    ("responsible_company", "contexto", "Empresa responsável pelo projeto", True),
    ("client_or_distributor", "contexto", "Cliente, distribuidora ou parceiro operacional", True),
    ("release_or_start_date", "contexto", "Data prevista de início ou lançamento", True),
    ("release_type", "contexto", "Tipo de lançamento", False),
    ("project_synopsis", "contexto", "Sinopse ou contexto do pedido", False),
    ("project_synopsis_av", "contexto", "Sinopse ou contexto do pedido", False),
    ("has_brand_association", "contexto", "Associação com marca ou campanha", False),
    ("brand_context", "contexto", "Marca ou contexto associado", False),
    ("general_clearance_notes", "contexto", "Observações gerais de clearance", True),
    ("tracks", "faixas", "Faixas", True),
    ("tracks.title", "faixas", "Título da faixa", True),
    ("tracks.primary_artists", "faixas", "Artistas principais", True),
    ("tracks.authors", "faixas", "Autores / compositores", True),
    ("tracks.publishers", "faixas", "Editoras", False),
    ("tracks.phonogram_owner", "faixas", "Titular do fonograma", True),
    ("tracks.has_isrc", "faixas", "Já possui ISRC?", True),
    ("tracks.isrc_code", "faixas", "Código ISRC", True),
    ("tracks.notes_for_clearance", "faixas", "Observações para o clearance", False),
    ("music_title", "escopo", "Título da música", True),
    ("artist_name", "escopo", "Artista principal", True),
    ("phonogram_owner", "escopo", "Titular do fonograma", True),
    ("composer_author_info", "escopo", "Compositores e autores", True),
    ("publisher_info", "escopo", "Editoras ou publishing", True),
    ("material_type", "escopo", "Tipo de material solicitado", True),
    ("intended_use", "escopo", "Uso pretendido", True),
    ("exclusivity", "escopo", "Pedido de exclusividade", True),
    ("territory", "escopo", "Território", True),
    ("licensing_period", "escopo", "Período de licenciamento", True),
    ("audiovisual_type", "escopo_av", "Tipo de audiovisual ou produto", True),
    ("director_name", "escopo_av", "Direção", True),
    ("product_or_campaign_name", "escopo_av", "Produto, campanha ou peça", True),
    ("scene_description", "escopo_av", "Descrição da cena ou aplicação", True),
    ("sync_duration", "escopo_av", "Duração de sync", True),
    ("media_channels", "escopo_av", "Canais e meios de veiculação", True),
    ("supporting_files", "assets", "Arquivos de apoio", False),
    ("reference_links", "assets", "Links de referência", False),
    ("additional_notes", "assets", "Observações adicionais", False),
    ("supporting_files_av", "assets_av", "Arquivos de apoio", False),
    ("reference_links_av", "assets_av", "Links de referência", False),
    ("additional_notes_av", "assets_av", "Observações adicionais", False),
    ("consentTruth", "revisao", "Declaração de veracidade e consentimento", True),
])

PEOPLE_REGISTRY_STEPS = {
    "identificacao": "Identificação", "contato": "Contato", "endereco": "Endereço",
    "bancario": "Dados bancários", "adicionais": "Informações adicionais", "revisao": "Revisão",
}
PEOPLE_REGISTRY_FIELDS = _catalog([
    ("party_kind", "identificacao", "Tipo de cadastro", True),
    ("display_name", "identificacao", "Nome de exibição", True),
    ("display_name_pj", "identificacao", "Nome / marca", True),
    ("legal_name", "identificacao", "Nome legal", True),
    ("legal_name_pj", "identificacao", "Razão social", True),
    ("stage_name", "identificacao", "Nome artístico", False),
    ("trade_name", "identificacao", "Nome fantasia", False),
    ("document_id", "identificacao", "CPF", False),
    ("document_id_pj", "identificacao", "CNPJ", False),
    ("roles", "identificacao", "Funções", True),
    ("roles_other", "identificacao", "Qual função?", True),
    ("email_primary", "contato", "E-mail", False),
    ("phone_primary", "contato", "Telefone / WhatsApp", False),
    ("website", "contato", "Site", False), ("instagram", "contato", "Instagram", False),
    ("country", "endereco", "País", False), ("state_region", "endereco", "Estado / UF", False),
    ("city", "endereco", "Cidade", False), ("postal_code", "endereco", "CEP", False),
    ("address_line_1", "endereco", "Endereço", False),
    ("pix_key", "bancario", "Chave Pix", False), ("bank_name", "bancario", "Banco", False),
    ("bank_agency", "bancario", "Agência", False), ("account_number", "bancario", "Número da conta", False),
    ("account_holder_name", "bancario", "Titular", False),
    ("account_holder_document_id", "bancario", "CPF/CNPJ do titular", False),
    ("manager_name", "adicionais", "Assessor / Manager", False),
    ("label_name", "adicionais", "Gravadora / Editora", False),
    ("notes_internal", "adicionais", "Observações", False),
    ("consentTruth", "revisao", "Declaração de veracidade e consentimento", True),
])

COMPANY_REGISTRY_STEPS = {
    "empresa": "Empresa", "legal": "Responsável legal", "contrato": "Contrato",
    "financeiro": "Financeiro", "bancario": "Dados bancários", "revisao": "Revisão",
}
COMPANY_REGISTRY_FIELDS = _catalog([
    ("document_type", "empresa", "Tipo de documento", True),
    ("document_number", "empresa", "Número do documento (CPF ou CNPJ)", True),
    ("fantasy_name", "empresa", "Nome fantasia", True), ("legal_name", "empresa", "Razão social", True),
    ("address", "empresa", "Endereço", True), ("city", "empresa", "Cidade", True),
    ("state", "empresa", "Estado (UF)", True), ("zip_code", "empresa", "CEP", True),
    ("legalrep_name", "legal", "Nome completo", True), ("legalrep_phone", "legal", "Telefone / WhatsApp", True),
    ("legalrep_email", "legal", "E-mail", True),
    ("contract_same_as_legal", "contrato", "Mesmo que o responsável legal?", True),
    ("contract_name", "contrato", "Nome completo", True), ("contract_phone", "contrato", "Telefone / WhatsApp", True),
    ("contract_email", "contrato", "E-mail", True),
    ("financial_same_as_legal", "financeiro", "Mesmo que o responsável legal?", True),
    ("financial_same_as_contract", "financeiro", "Mesmo que o responsável pelo contrato?", True),
    ("financial_name", "financeiro", "Nome completo", True), ("financial_phone", "financeiro", "Telefone / WhatsApp", True),
    ("financial_email", "financeiro", "E-mail", True),
    ("bank_name", "bancario", "Banco", True), ("agency", "bancario", "Agência", True),
    ("account", "bancario", "Conta (com dígito)", True), ("account_type", "bancario", "Tipo de conta", True),
    ("pix_key", "bancario", "Chave Pix", False),
    ("consentTruth", "revisao", "Declaração de veracidade e consentimento", True),
])

for _fields in (RIGHTS_CLEARANCE_FIELDS, PEOPLE_REGISTRY_FIELDS, COMPANY_REGISTRY_FIELDS):
    _fields["consentTruth"].update({
        "requirement": "on_submit",
        "locked": True,
        "lock_reason": "Obrigatório para proteção jurídica e tratamento dos dados.",
    })

# O e-mail do solicitante é a identidade operacional do pedido de clearance.
RIGHTS_CLEARANCE_FIELDS["requester_email"].update({
    "locked": True,
    "lock_reason": "Necessário para retorno, rascunhos e atualizações do pedido.",
})

WORKFLOW_CATALOGS: Dict[str, tuple[Dict[str, Dict[str, Any]], Dict[str, str]]] = {
    "release_intake": (RELEASE_INTAKE_FIELDS, RELEASE_INTAKE_STEPS),
    "rights_clearance": (RIGHTS_CLEARANCE_FIELDS, RIGHTS_CLEARANCE_STEPS),
    "people_registry": (PEOPLE_REGISTRY_FIELDS, PEOPLE_REGISTRY_STEPS),
    "company_registry": (COMPANY_REGISTRY_FIELDS, COMPANY_REGISTRY_STEPS),
}


def _workflow_catalog(workflow_type: str) -> tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    catalog = WORKFLOW_CATALOGS.get(workflow_type)
    if catalog is None:
        raise HTTPException(status_code=404, detail="workflow sem catálogo de formulário")
    return catalog


class FormFieldPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visible: Optional[bool] = None
    requirement: Optional[Requirement] = None
    label: Optional[str] = Field(default=None, max_length=120)
    hint: Optional[str] = Field(default=None, max_length=500)
    placeholder: Optional[str] = Field(default=None, max_length=250)

    @field_validator("label", "hint", "placeholder")
    @classmethod
    def clean_text(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else value.strip()


class FormConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: Dict[str, FormFieldPatch]


async def _require_admin_or_portal(
    workspace_slug: str,
    x_admin_token: Optional[str] = Header(default=None),
    x_portal_token: Optional[str] = Header(default=None),
) -> None:
    if x_admin_token and _admin_token_is_valid(x_admin_token.strip()):
        return
    require_portal_session(workspace_slug, x_portal_token)


def _stored_form(workspace_slug: str, workflow_type: str) -> tuple[Dict[str, Any], bool]:
    row = _read_raw_row(workspace_slug, workflow_type)
    extra = (row or {}).get("extra_settings") or {}
    form = extra.get("form") or {}
    return (form if isinstance(form, dict) else {}), row is not None


def _resolve(workspace_slug: str, workflow_type: str) -> Dict[str, Any]:
    form_fields, step_labels = _workflow_catalog(workflow_type)
    stored, row_exists = _stored_form(workspace_slug, workflow_type)
    overrides = stored.get("fields") or {}
    fields: Dict[str, Dict[str, Any]] = {}
    for key, default in form_fields.items():
        resolved = copy.deepcopy(default)
        override = overrides.get(key) if isinstance(overrides, dict) else None
        if isinstance(override, dict):
            for name in ("visible", "requirement", "label", "hint", "placeholder"):
                if name in override and override[name] is not None:
                    resolved[name] = override[name]
        resolved["key"] = key
        resolved["_origin"] = "db" if isinstance(override, dict) else "default"
        fields[key] = resolved
    return {
        "ok": True,
        "workspace_slug": workspace_slug,
        "workflow_type": workflow_type,
        "schema_version": 1,
        "row_exists": row_exists,
        "steps": step_labels,
        "fields": fields,
    }


@router.get("/{workspace_slug}/workflows/{workflow_type}/form-config")
async def get_form_config(workspace_slug: str, workflow_type: str) -> Dict[str, Any]:
    return _resolve(workspace_slug.strip().lower(), workflow_type.strip().lower())


@router.patch("/{workspace_slug}/workflows/{workflow_type}/form-config")
async def patch_form_config(
    workspace_slug: str,
    workflow_type: str,
    body: FormConfigPatch,
    _: None = Depends(_require_admin_or_portal),
) -> Dict[str, Any]:
    slug = workspace_slug.strip().lower()
    workflow = workflow_type.strip().lower()
    form_fields, _ = _workflow_catalog(workflow)
    unknown = sorted(set(body.fields) - set(form_fields))
    if unknown:
        raise HTTPException(status_code=422, detail=f"campos desconhecidos: {', '.join(unknown)}")

    row = _read_raw_row(slug, workflow) or {}
    extra = copy.deepcopy(row.get("extra_settings") or {})
    form = copy.deepcopy(extra.get("form") or {})
    stored_fields = form.setdefault("fields", {})

    for key, patch in body.fields.items():
        default = form_fields[key]
        values = patch.model_dump(exclude_unset=True)
        if default["locked"]:
            if values.get("visible") is False or (
                values.get("requirement") is not None
                and values["requirement"] != default["requirement"]
            ):
                raise HTTPException(status_code=422, detail=f"{key} é protegido: {default['lock_reason']}")
        stored_fields[key] = values

    form["schema_version"] = 1
    extra["form"] = form
    try:
        supabase.table("workspace_workflow_settings").upsert(
            {
                "workspace_slug": slug,
                "workflow_type": workflow,
                "extra_settings": extra,
            },
            on_conflict="workspace_slug,workflow_type",
        ).execute()
    except Exception:
        logger.exception("form_config update failed workspace=%s workflow=%s", slug, workflow)
        raise HTTPException(status_code=500, detail="falha ao persistir configuração do formulário")

    result = _resolve(slug, workflow)
    result["updated"] = sorted(body.fields)
    return result
