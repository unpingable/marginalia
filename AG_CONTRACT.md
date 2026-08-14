# Marginalia / Agent Governor contract

Marginalia M0 is qualified against:

- package: `agent-governor==2.8.1`
- source commit: `e279a94326a0a13dbe43473846b53e4c3a9b31f2`
- published annotated tag: `marginalia-chat-contract-m0`
- governed-chat contract: `capabilities.governed_chat.contract_version == "1"`

The package version prevents an unbounded dependency resolution. The source
commit identifies the exact local/container build input, and the annotated tag
makes that input reconstructible from the configured AG origin rather than
depending on an unpublished sibling checkout. At startup the
`GovernedChatAdapter` also fails closed unless AG advertises context-scoped
pending state, authoritative receipts, and the same governor state directory
that Marginalia was configured to use.

For a sibling checkout, the expected source layout is:

```text
git/
├── agent_gov/
└── agent_gov_ui/
    └── marginalia/
```

`sync-deps.sh` verifies the sibling checkout commit before staging AG into a
container build context.
