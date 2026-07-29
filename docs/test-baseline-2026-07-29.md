# Baseline de testes — sunbeat-backend @ main (2026-07-29)

Suíte completa na `main` limpa (commit `cb6ca33`): **83 passed, 6 failed**.

## Falhas pré-existentes (não introduzidas pela branch feature/drive-config-endpoint)

| Arquivo | Testes | Provável causa |
|---|---|---|
| `tests/test_airtable_upsert.py` | 3 (reactivation/status field) | drift desde a doc de 27/07 que registrava 98/98 na branch do Codex — a main divergiu |
| `tests/test_people_registry_airtable_sync.py` | 1 (`test_build_fields_targets_v2_pessoas_columns`) | idem |
| `tests/test_release_drafts_first_stage_email.py` | 2 (idempotency key Resend) | idem |

## Observações

- A documentação de 27/07 registrava 98/98 verdes — na **branch `codex/atabaque-meeting-readiness`**, não na main. As 6 falhas acima provavelmente são corrigidas pelos 4 commits daquela branch (o merge do Codex, Tarefa 0 do pacote, deve resolvê-las).
- Ambiente local: venv Python 3.12 em `sunbeat-backend/.venv` (não commitado), deps de `requirements.txt` + pytest.
- Novos testes adicionados hoje: `tests/test_drive_config.py` — 9/9 verdes, isolados e junto da suíte.
- Fragilidade de suite detectada: módulos de teste usam `os.environ.setdefault` para tokens; quem importa `app.core.config` primeiro define o valor. Testes novos devem fixar atributos via `patch.object(settings, ...)`.
