# Compatibility

Marginalia M0 is deliberately commit-qualified rather than broadly compatible.

| Dependency/contract | Required value |
|---|---|
| Python | `>=3.11` |
| `agent-governor` package | `2.8.1` |
| AG source | `e279a94326a0a13dbe43473846b53e4c3a9b31f2` |
| `receipt-v1` | `0.1.0`, from the qualified AG source |
| JSON-RPC protocol | `1.0` |
| governed-chat contract | `1` |

Runtime negotiation additionally requires:

```json
{
  "context_scoped_pending": true,
  "authoritative_receipts": true
}
```

Marginalia also compares the daemon's reported `governor_dir` with its own
configured root. A version/capability/root mismatch fails the governed-chat
boundary rather than silently running with split state.

The remaining donor non-chat endpoints use direct Python imports and therefore
share the exact AG package pin. They are not covered by a general semver
compatibility promise.
