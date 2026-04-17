# People Registry Airtable Sync

Este bloco conecta o `people_registry_records` do Supabase ao Airtable sem mudar a fonte canônica: o registro continua sendo persistido primeiro na Sunbeat, e só depois disso o sync roda.

## Escopo atual

- workflow: `people_registry`
- workspace inicial: `atabaque`
- profiles habilitáveis:
  - `atabaque_people_v1`
  - `atabaque_cadastro_v1`
  - `operational_contact`

## Tabela Airtable

- tabela alvo atual: `Dados Cadastrais`
- merge key principal: `CPF / CNPJ`
- fallback de merge: `Endereço de e-mail`

Observação: a tabela `Dados Cadastrais` usa vários campos fórmula para leitura consolidada. O sync escreve nos campos PF/PJ subjacentes corretos e usa os campos fórmula apenas para lookup.

## Status local de sync

Como o schema atual do Supabase ainda não tem o valor `skipped`, este bloco usa:

- `pending`: registro recém-persistido e ainda não sincronizado
- `synced`: create/update no Airtable concluído
- `failed`: tentativa de sync falhou
- `blocked`: equivalente local de `skipped` enquanto o schema não for expandido

Os campos atualizados em `people_registry_records` continuam sendo:

- `airtable_sync_status`
- `airtable_sync_error`
- `airtable_base_id`
- `airtable_table_name`
- `airtable_record_id`

## Ativação por config/env

O sync só roda quando os dois toggles abaixo estão `true`:

- `AIRTABLE_PEOPLE_REGISTRY_SYNC_ENABLED`
- `AIRTABLE_PEOPLE_REGISTRY_ATABAQUE_ENABLED`

Configuração adicional:

- `AIRTABLE_PEOPLE_REGISTRY_BASE_ID`
  - opcional; se vazio, usa `AIRTABLE_BASE_ID`
- `AIRTABLE_PEOPLE_REGISTRY_ATABAQUE_TABLE`
  - padrão atual: `Dados Cadastrais`

## Campos escritos

Campos comuns:

- `idpessoa`
- `Pessoa Física ou Jurídica?`
- `Endereço de e-mail`
- `Empresa Responsável`
- `Status Dados Cadastrais`
- `Observações`

Campos PF:

- `Nome Completo:`
- `Nome Artístico:`
- `CPF:`
- `Telefone:`
- `Endereço Completo (Rua, Numero, Bairro, Cidade e Estado):`
- `Banco1:`
- `Agência1:`
- `Conta1:`
- `Nome do titular da conta:`
- `CPF ou CNPJ do titular da conta1:`
- `Pix1:`
- `E-mail para envio de contratos e relatórios1:`

Campos PJ:

- `Razão Social:`
- `Nome Fantasia:`
- `CNPJ:`
- `Endereço CNPJ`
- `Banco:`
- `Agência:`
- `Conta:`
- `Titular da conta:`
- `CPF ou CNPJ do titular da conta:`
- `Pix:`
- `E-mail para envio de Contratos e Relatórios:`
