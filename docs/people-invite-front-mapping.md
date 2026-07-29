# Mapeamento do formulário de convite para `PeopleRegistryPayload`

Este documento descreve como o formulário de pessoas do frontend deve montar o
objeto `person` enviado para:

```text
POST /people-registry/invites/{token}/respond
```

O endpoint recebe o seguinte envelope:

```json
{
  "person": {},
  "participation": {}
}
```

Atualmente, o frontend envia os valores planos do formulário diretamente em
`person`. Isso não corresponde ao contrato do backend, que exige
`workspace_slug`, `profile` e os grupos `party`, `contact`, `address`,
`banking`, `additional_info` e `meta`.

## Origem dos valores estruturais

Estes valores não devem ser digitados nem escolhidos pela pessoa convidada:

| Destino em `PeopleRegistryPayload` | Origem | Regra |
| --- | --- | --- |
| `workspace_slug` | resposta de leitura do convite | Usar `invite.workspace_slug`. |
| `profile` | resposta de leitura do convite | Usar `invite.profile`; não inferir pelo formulário. |
| `workflow_type` | constante do frontend | Enviar `people_registry` ou omitir para usar o default do backend. |
| `edit_token` | não aplicável ao convite | O token já está no path; não copiar para o payload. |
| `meta.form_version` | versão do formulário | Usar uma versão explícita, por exemplo `people_invite_v1`. |
| `meta.source` | constante opcional | Pode ser omitido; o backend registra a origem do convite. |
| `additional_info.external_refs` | backend | Não enviar. O backend acrescenta as referências do caso de clearance. |

## Identificação e papéis

O formulário possui nomes de campos diferentes para pessoa física e pessoa
jurídica. O adaptador deve selecionar o conjunto correto com base em
`party_kind`.

| Campo do formulário | Destino | Transformação |
| --- | --- | --- |
| `party_kind` | `party.party_kind` | Copiar `pf` ou `pj`. |
| `display_name` | `party.display_name` | Usar quando `party_kind === "pf"`. |
| `display_name_pj` | `party.display_name` | Usar quando `party_kind === "pj"`. |
| `legal_name` | `party.legal_name` | Usar quando `party_kind === "pf"`. |
| `legal_name_pj` | `party.legal_name` | Usar quando `party_kind === "pj"`. |
| `stage_name` | `party.stage_name` | Enviar somente para PF e somente se preenchido. |
| `trade_name` | `party.trade_name` | Enviar somente para PJ e somente se preenchido. |
| `document_id` | `party.document_id` | Usar quando `party_kind === "pf"`. |
| `document_id_pj` | `party.document_id` | Usar quando `party_kind === "pj"`. |
| `roles` | `party.roles` | Copiar a lista, removendo valores vazios e duplicados. |

`party.display_name` e `party.legal_name` são obrigatórios no backend para PF e
PJ. O frontend atual não marca os nomes legais como obrigatórios. Antes do
envio, a interface deve exigir o nome legal correspondente; não é recomendado
silenciosamente substituir o nome legal pelo nome de exibição.

## Contato

| Campo do formulário | Destino | Transformação |
| --- | --- | --- |
| `email_primary` | `contact.email_primary` | Remover espaços nas extremidades; enviar somente se preenchido. |
| `phone_primary` | `contact.phone_primary` | Remover espaços nas extremidades; preservar o formato informado. |
| `website` | `contact.website` | Enviar somente se preenchido. |
| `instagram` | `contact.instagram` | Enviar somente se preenchido. |

O formulário já exige pelo menos documento ou e-mail. Essa regra deve continuar
no frontend; o backend também valida a estrutura do cadastro.

## Endereço

| Campo do formulário | Destino |
| --- | --- |
| `country` | `address.country` |
| `state_region` | `address.state_region` |
| `city` | `address.city` |
| `postal_code` | `address.postal_code` |
| `address_line_1` | `address.address_line_1` |

O backend também aceita `address.address_line_2`, mas o formulário atual não
possui esse campo. Valores vazios devem ser omitidos, e não enviados como
strings vazias.

## Dados bancários

| Campo do formulário | Destino |
| --- | --- |
| `pix_key` | `banking.pix_key` |
| `bank_name` | `banking.bank_name` |
| `bank_agency` | `banking.bank_agency` |
| `account_number` | `banking.account_number` |
| `account_holder_name` | `banking.account_holder_name` |
| `account_holder_document_id` | `banking.account_holder_document_id` |

Esses dados são sensíveis. Devem existir apenas no corpo HTTPS da resposta ao
convite e não devem ser incluídos em logs, analytics, URLs ou mensagens de erro
do frontend.

## Informações adicionais

| Campo do formulário | Destino |
| --- | --- |
| `manager_name` | `additional_info.manager_name` |
| `label_name` | `additional_info.label_name` |
| `notes_internal` | `additional_info.notes_internal` |

`consentTruth` é um controle de validação da interface. Ele não pertence ao
`PeopleRegistryPayload` e não deve ser enviado.

## Participação no clearance

O formulário atual não coleta os campos de participação e envia
`participation: {}`. Isso é aceito pelo backend e aplica
`confirmation_status = "confirmado"`, mas não registra papel musical,
remuneração ou observações adicionais.

Quando esses dados forem incorporados à interface, devem ser enviados fora de
`person`:

| Destino no envelope | Tipo/observação |
| --- | --- |
| `participation.confirmation_status` | Default `confirmado`. |
| `participation.musical_role` | Papel confirmado pela pessoa. |
| `participation.remuneration_type` | Modelo de remuneração. |
| `participation.participation_percent` | Percentual, quando aplicável. |
| `participation.fixed_amount` | Valor fixo, quando aplicável. |
| `participation.notes` | Observações da participação. |

O contexto recebido no convite serve para orientar a tela, mas não deve ser
convertido automaticamente em uma confirmação feita pela pessoa sem
apresentação e aceite explícitos.

## Adaptador recomendado

O mapeamento deve viver no frontend, em um módulo tipado próprio, por exemplo:

```text
src/lib/peoplePayload.ts
```

`src/lib/api.ts` deve chamar esse adaptador antes de executar
`respondInvite`. Manter a transformação fora do componente evita duplicação,
permite teste unitário e preserva no backend um único contrato canônico. Um
adaptador no backend só seria indicado temporariamente para compatibilidade com
clientes antigos; não deve virar um segundo formato permanente.

Exemplo de implementação:

```ts
type InviteIdentity = {
  workspace_slug: string;
  profile: string;
};

export function toPeopleRegistryPayload(
  invite: InviteIdentity,
  values: PeopleFormValues,
) {
  const isPj = values.party_kind === "pj";
  const compact = <T extends Record<string, unknown>>(value: T) =>
    Object.fromEntries(
      Object.entries(value).filter(([, item]) => item !== "" && item != null),
    );

  return {
    workspace_slug: invite.workspace_slug,
    workflow_type: "people_registry",
    profile: invite.profile,
    party: compact({
      party_kind: values.party_kind,
      display_name: isPj ? values.display_name_pj : values.display_name,
      legal_name: isPj ? values.legal_name_pj : values.legal_name,
      stage_name: isPj ? undefined : values.stage_name,
      trade_name: isPj ? values.trade_name : undefined,
      document_id: isPj ? values.document_id_pj : values.document_id,
      roles: [...new Set(values.roles ?? [])].filter(Boolean),
    }),
    contact: compact({
      email_primary: values.email_primary?.trim(),
      phone_primary: values.phone_primary?.trim(),
      website: values.website?.trim(),
      instagram: values.instagram?.trim(),
    }),
    address: compact({
      country: values.country,
      state_region: values.state_region,
      city: values.city,
      postal_code: values.postal_code,
      address_line_1: values.address_line_1,
    }),
    banking: compact({
      pix_key: values.pix_key,
      bank_name: values.bank_name,
      bank_agency: values.bank_agency,
      account_number: values.account_number,
      account_holder_name: values.account_holder_name,
      account_holder_document_id: values.account_holder_document_id,
    }),
    additional_info: compact({
      manager_name: values.manager_name,
      label_name: values.label_name,
      notes_internal: values.notes_internal,
    }),
    meta: {
      form_version: "people_invite_v1",
    },
  };
}
```

Uso no envio:

```ts
const person = toPeopleRegistryPayload(invite, values);
await api.respondInvite(invite.token, person);
```

A implementação atual de `respondInvite` recebe apenas `personPayload` e monta
o envelope HTTP com `participation: {}`. Se a assinatura dessa função for
alterada no futuro para receber o envelope completo, esse empacotamento deve
ser removido de dentro de `api.ts`. O corpo HTTP final precisa ter exatamente
um envelope com `person` e `participation`, sem um segundo nível
`person.person`.

## Exemplo do corpo final

```json
{
  "person": {
    "workspace_slug": "atabaque",
    "workflow_type": "people_registry",
    "profile": "titular",
    "party": {
      "party_kind": "pf",
      "display_name": "Lia do Tambor",
      "legal_name": "Lia Silva",
      "stage_name": "Lia do Tambor",
      "document_id": "000.000.000-00",
      "roles": ["artista"]
    },
    "contact": {
      "email_primary": "lia@example.com",
      "phone_primary": "+55 81 90000-0000"
    },
    "address": {
      "country": "Brasil",
      "state_region": "PE",
      "city": "Recife"
    },
    "banking": {
      "pix_key": "lia@example.com"
    },
    "additional_info": {
      "manager_name": "Ana"
    },
    "meta": {
      "form_version": "people_invite_v1"
    }
  },
  "participation": {
    "confirmation_status": "confirmado"
  }
}
```

## Incompatibilidades adjacentes identificadas

Além do payload plano, há dois contratos do frontend que precisam ser alinhados
em uma etapa própria:

1. A tipagem de `VerifyPersonResponse` espera campos pessoais completos dentro
   de `dados_cadastrais` e `v2_pessoas`. O endpoint de averiguação retorna
   matches sanitizados (`record_id`, `display_name`, `match_by`) para não expor
   e-mail, documento ou dados bancários.
2. A tela procura no contexto remoto chaves como `parte`, `papel`, `caso`,
   `projeto` e `faixa`. O backend entrega `party_name`, `requested_role`,
   `clearance_case_name`, `project_title` e `track_title`.

Essas diferenças não exigem flexibilizar o backend. A correção recomendada é
atualizar os tipos e o adaptador de contexto do frontend.
