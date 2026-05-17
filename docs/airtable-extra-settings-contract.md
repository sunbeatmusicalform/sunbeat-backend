# Airtable Extra Settings Contract

Escopo: contrato minimo de `workspace_workflow_settings.extra_settings.airtable`
para workflows com sincronizacao Airtable, com foco atual em `company_registry`
e `people_registry`.

Este contrato nao cria uma nova camada de configuracao. A fonte de verdade
continua sendo a linha `(workspace_slug, workflow_type)` em
`workspace_workflow_settings`, mesclada com os defaults herdaveis de
`app.services.workspace_config`.

## Principios

- O bloco `extra_settings.airtable` e row-scoped: cada linha ja representa um
  workflow, entao nao deve haver sub-blocos como `company_registry` ou
  `people_registry` dentro dele.
- O bloco deve aceitar patches pequenos. A ausencia de uma chave significa
  "usar o default herdavel", nao "desabilitar".
- Flags efetivas de sync continuam fora do JSONB, nas colunas top-level:
  `airtable_sync_enabled`, `drive_sync_enabled`, `post_submit_email_enabled`,
  `edit_email_enabled` e `edit_mode_enabled`.
- Chaves com prefixo `_` sao metadata descritiva vinda dos defaults. Elas podem
  ser retornadas para leitura, mas nao precisam ser gravadas pelo admin manual
  nem pela futura Setup AI.
- `field_map` e `merge_keys` sao runtime-owned nesta etapa. Podem ser
  documentados e lidos por ferramentas, mas nao devem substituir os builders de
  payload existentes sem uma frente propria.

## Shape minimo

```json
{
  "airtable": {
    "base_id_override": null,
    "<workflow>_table_override": null,
    "merge_keys": null,
    "field_map": null
  }
}
```

## Leitura/escrita assistida

O backend expoe uma superficie minima, protegida pelo mesmo `X-Admin-Token` das
demais configuracoes internas:

- `GET /internal/config/{workspace_slug}/{workflow_type}/airtable`
- `PATCH /internal/config/{workspace_slug}/{workflow_type}/airtable`

O `GET` retorna `airtable_sync_enabled`, `effective`, `raw`, `origins` e o
metadata de contrato. O `PATCH` aceita escrita parcial:

```json
{
  "airtable_sync_enabled": true,
  "airtable": {
    "base_id_override": "appXXXXXXXXXXXXXX",
    "table_override": "[V2] - Pessoas Sandbox"
  }
}
```

`table_override` e um alias de escrita assistida. O backend grava a chave
especifica do workflow (`company_registry_table_override` ou
`people_registry_table_override`) para preservar compatibilidade com os services
atuais.

### Chaves comuns

| Chave | Tipo | Obrigatoria? | Regra |
| --- | --- | --- | --- |
| `base_id_override` | string ou null | Nao | Se ausente/null, usa a base de env/default do workflow. |
| `merge_keys` | array ou null | Nao | Metadata/contrato. Se ausente, usa a politica runtime-owned do workflow. |
| `field_map` | object ou null | Nao | Metadata/contrato. Se ausente, usa o field map hardcoded no service. |

### Chaves de tabela aceitas hoje

| Workflow | Chave aceita | Default herdavel |
| --- | --- | --- |
| `company_registry` | `company_registry_table_override` | `[V2] - Empresas` |
| `people_registry` | `people_registry_table_override` | `[V2] - Pessoas` |

`table_override` generico nao e consumido pelos sync services atuais. A rota
interna de escrita assistida aceita esse alias e normaliza para a chave
especifica do workflow antes de persistir.

## Politica por workflow

### company_registry

- Tabela default: `[V2] - Empresas`.
- Base: `base_id_override` ou `AIRTABLE_BASE_ID`.
- Create: sempre cria no submit inicial.
- Update: apenas no edit/resubmit quando existe `airtable_record_id` salvo em
  `submissions.airtable_project_id`.
- Merge key efetiva: `airtable_record_id` local, nao documento/e-mail.
- `field_map`: definido em `app.services.airtable_company_registry`.
- Risco conhecido: submit inicial repetido pode duplicar no Airtable se nao
  houver idempotencia/record id no fluxo chamador.

### people_registry

- Tabela default: `[V2] - Pessoas`.
- Base: `base_id_override`, `AIRTABLE_PEOPLE_REGISTRY_BASE_ID` ou
  `AIRTABLE_BASE_ID`.
- Profiles aceitos inicialmente: `atabaque_people_v1`,
  `atabaque_cadastro_v1`, `operational_contact`.
- Create: cria quando nao encontra match no Airtable.
- Update: atualiza quando encontra match.
- Merge keys efetivas, em ordem:
  1. `Documento` normalizado.
  2. `E-mail principal` em lowercase.
- `field_map`: definido em `app.services.people_registry_airtable_sync`.
- Aliases de roles aceitos pelo service: `artist -> artista`,
  `producer -> produtor`, `composer -> compositor`, `lyricist -> letrista`,
  `interpreter -> interprete`, `partner -> socio`, `manager -> assessor`,
  `label -> gravadora`, `distributor -> distribuidora`,
  `publisher -> editora`, `contact/responsible -> contato`.

## Exemplos

### Atabaque usando apenas defaults herdaveis

Persistencia minima recomendada: nao gravar `extra_settings.airtable` quando o
default ja resolve corretamente.

```json
{
  "workspace_slug": "atabaque",
  "workflow_type": "company_registry",
  "airtable_sync_enabled": true,
  "extra_settings": {}
}
```

O runtime mescla esse payload com o default e resolve `[V2] - Empresas`.

### company_registry com override explicito

```json
{
  "airtable": {
    "base_id_override": "appXXXXXXXXXXXXXX",
    "company_registry_table_override": "[V2] - Empresas"
  }
}
```

### people_registry com override explicito

```json
{
  "airtable": {
    "base_id_override": "appXXXXXXXXXXXXXX",
    "people_registry_table_override": "[V2] - Pessoas"
  }
}
```

### Config parcial com merge keys documentadas

Este exemplo e valido como contrato/documentacao. O runtime atual continua
usando a politica hardcoded do service se `merge_keys` estiver ausente.

```json
{
  "airtable": {
    "people_registry_table_override": "[V2] - Pessoas",
    "merge_keys": [
      {
        "source": "party.document_id",
        "airtable_field": "Documento",
        "normalization": "strip punctuation and spaces",
        "priority": 1
      },
      {
        "source": "contact.email_primary",
        "airtable_field": "E-mail principal",
        "normalization": "lowercase",
        "priority": 2
      }
    ]
  }
}
```

### Update parcial futuro pela Setup AI

Uma escrita assistida deve fazer deep merge e alterar apenas o bloco necessario:

```json
{
  "airtable": {
    "base_id_override": "appNewBaseForThisWorkspace"
  }
}
```

Nao deve sobrescrever `operational_base`, `drive`, `email`, metadata `_...` nem
o restante de `extra_settings.airtable`.

## Regras de compatibilidade

- Config antiga sem override explicito de tabela: valida; usa default herdavel.
- Config parcial: valida; deep merge preserva defaults e chaves nao enviadas.
- `field_map` ausente: valida; o service usa o builder atual.
- `merge_keys` ausente: valida; o service usa a politica atual do workflow.
- Valor `null` em override: remove a preferencia daquele nivel e deixa fallback
  resolver.
- Chaves desconhecidas ja existentes no banco nao sao consumidas pelo runtime.
  Novos patches pela rota assistida rejeitam chaves fora do contrato para evitar
  configuracao ambigua.

## Impacto esperado

- `company_registry` permanece compativel com `[V2] - Empresas`.
- `people_registry` permanece compativel com `[V2] - Pessoas`.
- A futura Setup AI pode ler o contrato e escrever patches parciais em
  `workspace_workflow_settings.extra_settings.airtable`.
- Nenhuma configuracao deve ser duplicada fora de
  `workspace_workflow_settings.extra_settings`.
