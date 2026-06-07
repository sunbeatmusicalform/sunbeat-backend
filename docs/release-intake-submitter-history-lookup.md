# Release Intake submitter history lookup

## Summary

This contract adds a read-only lookup source for `release_intake` suggestions based on
values previously submitted by the same submitter in the same workspace.

It does not accept `submitter_email` in the public request. The backend resolves the
submitter from an existing `draft_token` or `edit_token`, then searches only
`release_intake` submissions from that same workspace and normalized email.

## Endpoint

```http
GET /release-intake/history-lookup?workspace_slug=atabaque&field=primary_artists&query=ana&limit=5&draft_token=...
```

`edit_token` can be used instead of `draft_token`. Requests with no token or both
tokens return an empty safe response.

## Public response

Only these fields are returned:

```json
{
  "ok": true,
  "items": [
    {
      "value": "Ana Teste",
      "field": "primary_artists",
      "source": "submitter_history",
      "count": 2,
      "lastUsedAt": "2026-06-01T00:00:00+00:00"
    }
  ]
}
```

## Allowed fields

- `primary_artists`
- `featured_artists`
- `interpreters`
- `authors`
- `publishers`
- `phonographic_producer`
- `producers_musicians`
- `existing_profile_links`
- `cover_link`
- `presskit_link`
- `promo_assets_link`

## Privacy and safety

- No arbitrary `submitter_email` parameter is accepted.
- Short queries return `{ "ok": true, "items": [] }` without querying the database.
- Unknown fields return `{ "ok": true, "items": [] }` without querying the database.
- Invalid tokens, workspace mismatches, and non-`release_intake` tokens return empty.
- The service selects history candidates but returns only whitelisted aggregate values.
- The response never includes payloads, drafts, submission IDs, draft tokens, edit
  tokens, submitter email, Airtable IDs, Drive IDs, phone numbers, documents, or
  internal notes.

## Non-goals

This does not add UI, autocomplete, People Registry writes, submit changes,
draft/autosave changes, upload changes, edit-mode changes, Airtable, Drive, email,
SQL migrations, or schema renderer activation.
