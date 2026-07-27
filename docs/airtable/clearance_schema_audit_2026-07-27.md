# Auditoria read-only do schema Airtable — Rights Clearance

Data: 2026-07-27

Base: `Workstation Atabaque` (`appGaV0kdkc2NEt0F`)

Escopo: somente estrutura; nenhum registro foi criado, editado ou removido.

Revalidação operacional após autorização: concluída em 27/07/2026. A
visualização foi devolvida à configuração original de 16 campos ocultos.

## Tabelas confirmadas

| Tabela | ID |
| --- | --- |
| `[V2] Clearance` | `tblOVPZ8AUXxVLWYU` |
| `[V2] Clearance Partes` | `tbltwVgpYVOg9s0Oe` |
| `[V2] Clearance Itens` | `tblIek0Syahhlpdf1` |
| `[V2] - Pessoas` | `tblPyd8sGVBKMyEz1` |

## Contrato confirmado — `[V2] Clearance`

Os campos de negócio usados pelo backend existem com os nomes e tipos
esperados:

- `Nome do Caso` — texto;
- `Formato do Clearance` — single select;
- `Escopo` — single select;
- `Status` — single select;
- `Nome do Solicitante` — texto;
- `E-mail do Solicitante` — e-mail;
- `Empresa do Solicitante` — texto;
- `Cliente / Contratante` — texto;
- `Título do Projeto / Campanha` — texto;
- `Território` — texto;
- `Período de Licenciamento` — texto;
- `Tipo de Utilização` — single select;
- `Uso Pretendido` — texto longo;
- `Marcas / Produto / Campanha` — texto;
- `Links de Referência` — texto longo;
- `Data de Solicitação` — data;
- `Observações Operacionais` — texto longo;
- `Partes do Caso` e `Itens do Caso` — links.

Opções confirmadas:

- `Formato do Clearance`: `music_release_clearance_intake`,
  `music_project_track`, `audiovisual_product_sync`, `brand_product_use`,
  `licensing_general`, `participation_collaboration`, `other`;
- `Escopo`: `musical`, `nao_musical`, `hibrido`;
- `Status`: `Inbox`, `Em análise`, `Aguardando retorno`, `Aprovado`,
  `Formalizado`, `Arquivado`.

## Campos de integração confirmados

Na revalidação anterior à criação, o Airtable rejeitou `Canal de Entrada` como
nome duplicado. A inspeção da lista completa de campos confirmou que os quatro
campos já haviam sido criados e estavam apenas ocultos na visualização:

| Campo | Tipo confirmado | Opção/valor usado |
| --- | --- | --- |
| `Canal de Entrada` | single select | inclui `Formulário` |
| `Status da Sincronização Airtable` | single select | inclui `synced` |
| `ID da Submissão` | texto de uma linha | UUID/ID interno da submissão |
| `URL de Edição` | URL | link público com token de edição |

Não foi criada nenhuma duplicata e nenhum registro de cliente foi escrito. O
contrato estrutural que bloqueava
`sync_rights_clearance_submission_to_airtable` está atendido.

`URL de Edição` deve ser tratada como informação confidencial, pois contém um
token de capacidade. O acesso à base e a esse campo deve permanecer limitado à
equipe operacional autorizada.

## Contrato confirmado — `[V2] Clearance Itens`

Os nomes usados pelo backend existem:

- `Nome do Item`;
- `Caso de Clearance`;
- `Tipo de Direito`;
- `Natureza do Item`;
- `Título da Obra`;
- `Título do Fonograma`;
- `ISRC`;
- `Status da Liberação`;
- `Observações Jurídicas / Operacionais`.

Opções verificadas e compatíveis com o código:

- `Tipo de Direito` inclui `Fonograma / Master`;
- `Natureza do Item` inclui `Fonograma`.

## Contrato confirmado — `[V2] Clearance Partes`

Os nomes usados pelo backend e pelo fluxo de People Registry existem:

- `Nome da Parte no Caso`;
- `Caso de Clearance`;
- `Item do Caso`;
- `Nome da Parte`;
- `Papel no Caso`;
- `E-mail de Assinatura`;
- `Percentual / Participação`;
- `Status de Aprovação`;
- `Pessoa Vinculada`;
- `Status do Cadastro`;
- `Observações`.

## Contrato confirmado — `[V2] - Pessoas`

Foram confirmados os campos usados no link-back e no cadastro, incluindo
`Nome de Exibição`, `Tipo de Pessoa`, `Documento`, `Funções`,
`E-mail principal`, `Telefone Principal`, `Status do Cadastro`,
`Fonte do Cadastro`, `Documentos / Arquivos` e `Observações de Cadastro`.

## Gate antes de habilitar escrita

1. campos de integração revisados — concluído;
2. manter `AIRTABLE_RIGHTS_CLEARANCE_MUSICAL_ENABLED=false` durante migration e
   deploy;
3. manter `people_invite_auto_create_enabled=false`;
4. validar primeiro com um único caso controlado e sem envio de e-mail;
5. confirmar IDs dos registros criados e ausência de duplicação;
6. só então habilitar escrita para o workspace Atabaque.
