# Auditoria read-only — People Registry no Supabase

Data: 27/07/2026

Projeto: `sunbeat-core`

Project ref: `pjawmgcnccrdcpjmworg`

## Escopo inicial

Foram consultados somente catálogos do Postgres:

- tabelas e estado de RLS;
- colunas e tipos;
- constraints;
- índices;
- policies;
- grants por role.

Nenhum payload de cliente foi lido.

## Resultado

### `people_registry_invites`

- antes da migration, a tabela não existia;
- após autorização, a tabela foi criada e permaneceu com zero registros.

### `people_registry_records`

- a tabela existe;
- `id` é `uuid`;
- `edit_token` já existe como `uuid NOT NULL DEFAULT gen_random_uuid()`;
- existe índice único `people_registry_records_edit_token_idx`;
- antes da migration, RLS estava desabilitado e `anon`/`authenticated`
  possuíam grants amplos;
- após a migration, RLS está habilitado e não há grants de tabela para
  `anon` ou `authenticated`;
- a contagem permaneceu em 13 registros, sem leitura de payloads.

## Aplicação autorizada em produção

Em 27/07/2026, a migration
`docs/supabase/people_registry_invites.sql` foi aplicada ao projeto
`sunbeat-core` (`pjawmgcnccrdcpjmworg`) via sessão autenticada do Supabase CLI.

SHA-256 do arquivo aplicado:
`2027436425e52acc6767333e04798fd5d746fedf4724048f823304fd88112996`.

Pré-checagem:

- projeto `ACTIVE_HEALTHY`, região `sa-east-1`;
- `people_registry_records`: 13 registros;
- `people_registry_invites`: ausente;
- `people_registry_records`: RLS desabilitado;
- `id` e `edit_token`: `uuid NOT NULL DEFAULT gen_random_uuid()`.

Pós-checagem:

- `people_registry_records`: 13 registros;
- `people_registry_invites`: 0 registros;
- RLS habilitado nas duas tabelas;
- nenhum grant de tabela para `anon` ou `authenticated`;
- `service_role` mantém CRUD nas duas tabelas;
- os três índices de convites e o índice único de `edit_token` existem;
- a foreign key para `people_registry_records(id)` usa `ON DELETE SET NULL`;
- a constraint de status contém todos os oito estados esperados;
- `airtable_clearance_part_id` é anulável;
- nenhum registro de cliente foi criado, alterado ou removido.

Nota de privilégio: além de CRUD, o catálogo gerenciado do Supabase reporta
`REFERENCES`, `TRIGGER` e `TRUNCATE` para `service_role`. Isso já decorre do
modelo de grants da plataforma e não expõe as tabelas a `anon` ou
`authenticated`. Uma redução adicional de privilégios deve ser tratada como
hardening separado, com teste de compatibilidade.

## Impacto na migration proposta

A versão anterior assumia `edit_token text`, o que divergia do schema real.
Como o serviço já gera tokens com `uuid4()`, a correção segura é preservar UUID
e falhar explicitamente se outro tipo for encontrado.

A migration revisada:

1. preserva/cria `edit_token uuid`;
2. valida o tipo antes de prosseguir;
3. reutiliza o nome do índice único existente;
4. habilita RLS em registros e convites;
5. revoga acesso direto de `anon` e `authenticated`;
6. concede CRUD apenas a `service_role`.

## Compatibilidade da aplicação

- o backend gera `edit_token` e tokens de convite como UUID;
- formulários públicos usam a API FastAPI;
- Sunbeat Tables usa cliente admin server-side após verificar o workspace;
- não foi encontrado acesso browser direto a `people_registry_records`.

## Validação em banco descartável

Em 27/07/2026, os dois arquivos foram executados em um PostgreSQL 16 local
descartável, com os papéis padrão `anon`, `authenticated` e `service_role`:

1. `people_registry_records.sql`;
2. `people_registry_invites.sql`;
3. reaplicação integral de `people_registry_invites.sql`.

Resultado:

- as duas execuções da migration terminaram sem erro;
- a reaplicação não criou índices ou constraints duplicados;
- RLS ficou habilitado em `people_registry_records` e
  `people_registry_invites`;
- `anon` e `authenticated` ficaram sem grants nas duas tabelas;
- `service_role` recebeu apenas `SELECT`, `INSERT`, `UPDATE` e `DELETE`;
- as duas tabelas permaneceram com zero registros no banco descartável.

O container de teste foi encerrado e removido ao final.

## Gate

Validação descartável e idempotência: concluídas.

Concluído:

- autorização nominal registrada nesta task;
- migration aplicada e reinspecionada;
- RLS, grants, índices, constraints, tipos e contagens confirmados;
- nenhuma linha criada em `people_registry_invites`;
- configuração do código preserva
  `people_invite_auto_create_enabled=false`;
- configuração do workflow `people_registry` preserva
  `post_submit_email_enabled=false` e `edit_email_enabled=false`.

Ainda antes de ativar o fluxo:

- guardar o commit/release implantado para rollback;
- validar rotas públicas e dashboard com `service_role`;
- fazer o primeiro smoke controlado sem envio de e-mail;
- só então considerar habilitar auto-create.

## Security Advisor

O Supabase Security Advisor confirmou `rls_disabled_in_public` para
`people_registry_records`.

Também apontou problemas fora do escopo deste PR:

- outras tabelas públicas sem RLS;
- duas funções com `search_path` mutável;
- uma policy permissiva em `workspace_users`;
- proteção contra senhas vazadas desabilitada.

Esses itens devem entrar em uma frente P0 de hardening separada. Não foram
alterados para evitar uma mudança ampla e não relacionada no banco do cliente.

Referências de remediation:

- https://supabase.com/docs/guides/database/database-linter?lint=0013_rls_disabled_in_public
- https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable
- https://supabase.com/docs/guides/database/database-linter?lint=0024_permissive_rls_policy
- https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection
