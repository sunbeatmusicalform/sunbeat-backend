# Auditoria read-only — People Registry no Supabase

Data: 27/07/2026

Projeto: `sunbeat-core`

Project ref: `pjawmgcnccrdcpjmworg`

## Escopo

Foram consultados somente catálogos do Postgres:

- tabelas e estado de RLS;
- colunas e tipos;
- constraints;
- índices;
- policies;
- grants por role.

Nenhum registro de cliente foi lido. Nenhuma migration, DDL ou escrita foi
executada.

## Resultado

### `people_registry_invites`

- a tabela ainda não existe;
- a migration permanece necessária antes de ativar o fluxo de convites.

### `people_registry_records`

- a tabela existe;
- `id` é `uuid`;
- `edit_token` já existe como `uuid NOT NULL DEFAULT gen_random_uuid()`;
- existe índice único `people_registry_records_edit_token_idx`;
- RLS está desabilitado;
- não existem policies;
- `anon` e `authenticated` possuem grants amplos na tabela.

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

## Gate

Ainda não aplicar. Antes:

- executar a migration em branch/banco descartável;
- reexecutar para confirmar idempotência;
- rodar advisors de segurança e performance;
- validar rotas públicas e dashboard com `service_role`;
- aprovar separadamente aplicação em produção.

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
