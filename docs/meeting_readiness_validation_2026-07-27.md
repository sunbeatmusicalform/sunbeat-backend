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
- `people_registry_invites` foi criada e permanece com zero registros;
- `people_registry_records.id` e `edit_token` são UUID;
- migration corrigida para preservar UUID, ativar RLS e retirar acesso direto
  de `anon` e `authenticated`;
- migration aplicada duas vezes em PostgreSQL 16 descartável;
- reaplicação concluída sem duplicar estruturas;
- migration aplicada em produção após autorização;
- contagem de `people_registry_records` permaneceu em 13;
- RLS está habilitado nas duas tabelas e `anon`/`authenticated` não possuem
  grants de tabela;
- índices, tipos, foreign key e constraint de status foram reinspecionados;
- banco descartável removido após a validação;
- nenhum registro de cliente foi criado, editado ou removido.

## Airtable

- base `Workstation Atabaque` auditada em leitura;
- IDs, nomes, tipos e opções principais das tabelas de Clearance, Itens, Partes
  e Pessoas foram confirmados;
- os mappings de formato, escopo, status, tipo de direito e natureza do item são
  compatíveis com as opções vivas;
- os quatro campos de metadados esperados pelo backend já existem em
  `[V2] Clearance` e estavam ocultos na visualização:
  - `Canal de Entrada`;
  - `Status da Sincronização Airtable`;
  - `ID da Submissão`;
  - `URL de Edição`;
- os tipos foram confirmados e as opções `Formulário` e `synced` existem;
- nenhuma duplicata foi criada, nenhum registro foi alterado e a visualização
  foi devolvida à configuração original de 16 campos ocultos.

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

1. confirmar os nomes/valores das flags efetivamente configuradas no Fly;
2. merge/deploy do backend;
3. smoke test sem e-mail e sem auto-create;
4. merge/promoção do frontend;
5. smoke test autenticado, isolamento por workspace e navegação;
6. convite real e e-mail somente com destinatário nominal aprovado.
