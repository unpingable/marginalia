# Compatibility

Marginalia M1 is deliberately commit-qualified rather than broadly compatible.

| Dependency/contract | Required value |
|---|---|
| Python | `>=3.11` |
| `agent-governor` package | `2.8.1` |
| AG source | `e279a94326a0a13dbe43473846b53e4c3a9b31f2` |
| AG published ref | `marginalia-chat-contract-m0` |
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

The normal M1 runtime is fiction-only. Remaining donor non-chat endpoints are
disabled unless `MARGINALIA_ENABLE_DONOR_ROUTES=1`; that switch exists for the
historical compatibility suite, not as a supported product profile. Those
quarantined endpoints still share the exact AG package pin and are not covered
by a general semver compatibility promise.
