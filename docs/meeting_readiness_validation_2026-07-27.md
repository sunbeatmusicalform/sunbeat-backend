# Evidências de prontidão — entrega Atabaque

Data: 2026-07-27

## Escopo validado

### Backend

- branch: `codex/atabaque-meeting-readiness`;
- PR draft: `#37`;
- estado remoto observado: aberto e mergeável;
- suíte executada em imagem local baseada em Python 3.11;
- resultado: **98 testes, 98 aprovados**;
- imagem local construída com sucesso;
- produção atual: `cb6ca33`;
- último workflow `Fly Deploy` do commit atual de produção: sucesso;
- `https://sunbeat-backend.fly.dev/health`: `status=ok`.

### Frontend

- branch: `codex/atabaque-people-meeting`;
- PR draft: `#47`;
- estado remoto observado: aberto e mergeável;
- Vercel Preview do commit `8c2c1eb`: `READY`;
- lint: **0 erros, 28 avisos não bloqueantes**;
- build de produção local: sucesso, 42 páginas estáticas geradas;
- produção atual: `e8baeed`;
- produção `https://sunbeat.pro`: HTTP 200;
- Vercel Runtime Errors, últimas 24 horas: nenhum erro encontrado.

## Supabase

- schema de produção auditado em leitura;
- `people_registry_invites` ainda não existe;
- `people_registry_records.id` e `edit_token` são UUID;
- migration corrigida para preservar UUID, ativar RLS e retirar acesso direto
  de `anon` e `authenticated`;
- migration aplicada duas vezes em PostgreSQL 16 descartável;
- reaplicação concluída sem duplicar estruturas;
- `service_role` recebeu apenas CRUD;
- banco descartável removido após a validação;
- nenhuma migration foi aplicada em produção.

## Airtable

- base `Workstation Atabaque` auditada em leitura;
- IDs, nomes, tipos e opções principais das tabelas de Clearance, Itens, Partes
  e Pessoas foram confirmados;
- os mappings de formato, escopo, status, tipo de direito e natureza do item são
  compatíveis com as opções vivas;
- quatro campos de metadados esperados pelo backend ainda não existem em
  `[V2] Clearance`:
  - `Canal de Entrada`;
  - `Status da Sincronização Airtable`;
  - `ID da Submissão`;
  - `URL de Edição`;
- nenhuma célula, registro, campo ou configuração foi alterada durante a
  auditoria.

## Rollback registrado

### Frontend

- deployment anterior pronto para rollback:
  `dpl_gx1UeWHfDJ13sqYtMrTZWw3eLbyx`;
- commit: `e8baeedd2536ef2c935bc5bcaad8a8b3741fed67`;
- target: `production`;
- estado: `READY`.

### Backend

- commit implantado antes do PR: `cb6ca33e7a017a2a0846486da2b193bce2680883`;
- workflow Fly correspondente:
  `https://github.com/sunbeatmusicalform/sunbeat-backend/actions/runs/30053149720`;
- resultado do workflow: sucesso.

### Banco

- rollback destrutivo não deve remover tabela ou coluna;
- se o fluxo precisar ser interrompido, manter auto-create e sync desligados,
  reverter as aplicações e preservar a tabela nova inativa;
- qualquer `DROP` exige nova auditoria, contagem zero e aprovação separada.

## Gates restantes

1. autorização para criar os quatro campos ausentes no Airtable;
2. confirmação dos nomes/valores das flags efetivamente configuradas no Fly;
3. autorização nominal para aplicar a migration no Supabase;
4. merge/deploy do backend;
5. smoke test sem e-mail e sem auto-create;
6. merge/promoção do frontend;
7. smoke test autenticado, isolamento por workspace e navegação;
8. convite real e e-mail somente com destinatário nominal aprovado.
