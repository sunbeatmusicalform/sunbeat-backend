# Workflow Operational Bases

Escopo: mapa operacional dos workflows reais da Atabaque. A fonte herdavel fica em
`workspace_workflow_settings.extra_settings`, com fallback em
`app.services.workspace_config`.

## Matriz resumida

| Workflow | Base operacional principal | Tabelas locais | Sync Airtable | Drive |
| --- | --- | --- | --- | --- |
| `release_intake` | Supabase via `app.modules.submissions` | `submissions`, `tracks`, `submissions_revisions` | `app.services.airtable` para projetos e faixas | `sync_submission_to_google_drive` |
| `rights_clearance` | Supabase via `app.modules.submissions` | `submissions`, `tracks`, `submissions_revisions` | `app.services.airtable_rights_clearance` para case, itens e partes | `sync_clearance_to_google_drive` |
| `company_registry` | Supabase via `app.modules.submissions` | `submissions`, `submissions_revisions` | `app.services.airtable_company_registry` para `[V2] - Empresas` | Nao mapeado nesta etapa |
| `people_registry` | Supabase via `app.services.people_registry` | `people_registry_records` | `app.services.people_registry_airtable_sync` para `[V2] - Pessoas` | Nao mapeado nesta etapa |

## Estrutura herdavel

Cada workflow herda blocos em `extra_settings`:

- `operational_base`: store, tabelas e servico principal.
- `airtable`: metadados do destino operacional e chaves de override ja consumidas pelos syncs.
- `drive`: metadados do destino operacional e chaves de override ja consumidas quando existem.

As chaves com prefixo `_` sao descritivas. Os servicos continuam consumindo apenas
os overrides operacionais existentes, como `base_id_override`,
`projects_table_override`, `company_registry_table_override` e
`people_registry_table_override`.

O contrato minimo do bloco `extra_settings.airtable` esta documentado em
[`airtable-extra-settings-contract.md`](./airtable-extra-settings-contract.md).

## Divergencias atuais

- `company_registry` e `people_registry` usam servicos de sync separados, como
  esperado nesta etapa.
- `people_registry` continua `profile_adapter` e nao deve ser misturado com o
  renderer generico de `company_registry`.
- Google Drive ainda nao possui destino operacional real para `company_registry`
  ou `people_registry`.
- A tela interna de workflows exibe labels operacionais no frontend; o backend
  agora tambem carrega o mapa herdavel em `workspace_config.py`.
