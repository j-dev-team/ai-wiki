# AI Wiki Agent Protocol

All primary agent commands return the same envelope:

```json
{"protocol_version":"1.0","status":"ok","data":{},"meta":{},"error":null}
```

Errors use `status=error` and a stable `error.code`. Agents must branch on the
code rather than matching human-readable messages.

## Retrieval

Use `context` first. It combines keyword, vector, and relation retrieval and
returns complete JSON values within the requested token budget. Pending
unverified drafts are excluded unless `--include-unverified` is explicit.

Every citation has a document ID, canonical JSON Pointer path, verification
level, and source IDs. Cite only keys returned by the current context.

## Mutation

Patch uses the RFC 6902 subset `test`, `add`, `replace`, and `remove`.
`--if-version` is mandatory. Protected identity and creation fields cannot be
changed. Schema, source references, and quality regression are checked before
the atomic YAML/index/vector update.

Legacy v1 documents are normalized only in memory. Reading does not rewrite
them; the one document successfully patched is written as v2.
