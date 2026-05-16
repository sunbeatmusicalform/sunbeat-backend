# People Registry Airtable Sync

Este bloco conecta `people_registry_records` do Supabase ao Airtable sem mudar
a fonte canonica: o registro continua sendo persistido primeiro na Sunbeat, e
so depois disso o sync roda.

## Escopo atual

- workflow: `people_registry`
- workspace inicial: `atabaque`
- profiles habilitaveis:
  - `atabaque_people_v1`
  - `atabaque_cadastro_v1`
  - `operational_contact`

## Tabela Airtable

- tabela alvo atual: `[V2] - Pessoas`
- merge key principal: `Documento`
- fallback de merge: `E-mail principal`

## Status local de sync

Como o schema atual do Supabase ainda nao tem o valor `skipped`, este bloco usa:

- `pending`: registro recem-persistido e ainda nao sincronizado
- `synced`: create/update no Airtable concluido
- `failed`: tentativa de sync falhou
- `blocked`: equivalente local de `skipped` enquanto o schema nao for expandido

Os campos atualizados em `people_registry_records` continuam sendo:

- `airtable_sync_status`
- `airtable_sync_error`
- `airtable_base_id`
- `airtable_table_name`
- `airtable_record_id`

## Ativacao por config/env

O sync so roda quando os dois toggles abaixo estao `true`:

- `AIRTABLE_PEOPLE_REGISTRY_SYNC_ENABLED`
- `AIRTABLE_PEOPLE_REGISTRY_ATABAQUE_ENABLED`

Configuracao adicional:

- `AIRTABLE_PEOPLE_REGISTRY_BASE_ID`
  - opcional; se vazio, usa `AIRTABLE_BASE_ID`
- `AIRTABLE_PEOPLE_REGISTRY_ATABAQUE_TABLE`
  - fallback atual: `[V2] - Pessoas`
- `workspace_workflow_settings.extra_settings.airtable.people_registry_table_override`
  - override preferencial por workspace + workflow

## Campos escritos

Campos comuns em `[V2] - Pessoas`:

- `Nome de exibição`
- `Tipo de pessoa`
- `Nome legal / Razão social`
- `Documento`
- `Funções`
- `E-mail principal`
- `Telefone principal`
- `Site`
- `Instagram`
- `País`
- `Estado / Região`
- `Cidade`
- `CEP`
- `Endereço`
- `Chave Pix`
- `Banco`
- `Agência`
- `Conta`
- `Nome do titular da conta`
- `Documento do titular da conta`
- `Nome do empresário / responsável`
- `Selo / gravadora`
- `Observações internas`

Campo PF adicional:

- `Nome artístico`

Campo PJ adicional:

- `Nome fantasia`
