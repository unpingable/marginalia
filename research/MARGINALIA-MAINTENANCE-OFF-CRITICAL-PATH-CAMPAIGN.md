# Marginalia — MAINTENANCE-OFF-CRITICAL-PATH

Date recorded: 2026-09-06

Status: **CAPTURED AS CAMPAIGN / SUCCESSOR TO THE PLANNER/EXECUTOR WORK**

The planner/executor campaign made context maintenance *correct*. It is still on
the writer's critical path, and that is an architectural problem rather than a
provider-speed problem. Provider choice becomes an optimization inside this
architecture rather than the thing keeping the product usable.

---

## 1. The invariant

> Context maintenance should be incremental, amortized, and normally off the
> user's critical path. A foreground turn blocks on maintenance only when there
> is literally no qualified context snapshot that can safely admit it.

Target UX: the author almost never sees *"story context preparation is in
progress"*. When she does, it means Marginalia genuinely reached a hard safety
boundary despite background maintenance — not that she happened to submit a turn
when the collector decided to stop the world.

## 2. Observed behaviour

2026-09-06, session `78fc7d45675f4a21`, 102 messages:

```
05:16:18  turn admitted (revision 40)
05:23:32  503 context_maintenance   "story context preparation is in progress"
05:23:45  503                        author retries
05:24:13  503                        author retries
05:25:22  503                        author retries
05:27:56  503                        author retries
05:29:19  maintenance completed, covered_messages=100
05:40:43  maintenance completed, covered_messages=102
```

Roughly six minutes of a writing application declining to write, five of them
spent telling the author to try again. The summary record shows **29 receipt
IDs** — 29 provider calls for 102 messages, each a Claude Code CLI invocation
with process startup.

Nothing here is a wedge. It recovers. It is simply stop-the-world, and it scales
with the manuscript.

## 3. Target shape

```
qualified context snapshot @ revision 80
        +
unsummarized tail 81..103
        |
        +--- still fits admission budget? --- YES --> generate immediately
        |                                        \
        |                                         -> background maintenance
        NO
        |
        -> bounded foreground catch-up only
```

A snapshot asserts not merely *here is the summary* but **this summary is
qualified through revision N**. Everything after N is an ordinary bounded tail.
Maintenance then runs ahead of necessity:

```
rev 80 snapshot
turn 81
turn 82   -> background job summarizes through 82
turn 83      author keeps writing against snapshot@80 + tail
turn 84
             background result CAS-promotes snapshot@82
turn 85   -> context becomes snapshot@82 + tail 83..85
```

The revision CAS machinery already exists and is exactly what this wants.

### 3.1 A moved frontier does not invalidate completed work

If a job truthfully summarizes through revision 102 and the session is now at
104, **promote the snapshot through 102 and leave 103–104 in the tail**. Do not
discard finished work because the frontier moved while it ran. The current
prefix semantics already support this — a summary covering 1–51 stays valid when
52+ are appended — so the gap is in the scheduler, not the data model.

## 4. Why 29 calls — diagnose before optimizing

Do not flip providers first. Ask why 102 messages needs 29 fresh calls at all.
The possibilities imply different fixes:

| if… | then |
| --- | --- |
| the chunk summaries are independent | parallelize with a sane concurrency cap |
| old chunks are being re-summarized | cache and reuse immutable artifacts; process only the delta |
| it is a serial rolling summary | hierarchical structure: prior work stays valid, recompute the new leaf plus a bounded merge |
| the chunk size exists because the Claude Code CLI has a small safe envelope | an implementation constraint has become product architecture — kill that assumption |

The last row is the one to check first. GLM Flash handling a comparable payload
in ~37 seconds over HTTP suggests the 29-process path is paying an enormous
orchestration tax that has nothing to do with the work.

Content-addressed checkpoints and chunk digests already exist (`SummaryWork`,
`message_prefix_hash`), so artifact reuse may be closer than it looks.

## 5. Scope

- summarized-through watermark on the snapshot;
- bounded unsummarized tail as an ordinary admission input;
- proactive background maintenance triggered before the admission ceiling;
- reuse of prior immutable summary artifacts;
- CAS promotion of partial-frontier results;
- deduplicated single-flight workers, so five retries do not start five jobs;
- foreground maintenance retained **only** as an emergency admission mechanism.

## 6. Acceptance

1. An author writing continuously never sees a maintenance 503 while a
   qualified snapshot plus tail fits the budget.
2. Maintenance completing at revision N promotes while the session is at N+k.
3. Repeated foreground attempts during a running job do not start extra jobs.
4. Unchanged prefixes are not re-summarized across consecutive runs.
5. Foreground maintenance still triggers when no snapshot can admit the turn,
   and is still correct when it does.
6. The five-retry sequence of §2 replays as at most one 503.

Case 6 is the acceptance test that matters to the author.

## 7. Dependencies and relations

- **Planner/executor campaign** (shipped) — correctness foundation; this is its
  successor.
- **Backlog 1, durable attempt IDs** — single-flight in §5 wants a logical job
  identity and would duplicate work done separately.
- **Backlog 5, per-model context budget** — admission arithmetic is shared.
- **Maintenance backend qualification** (below) — independent, and a
  prerequisite for any provider change.

## 8. Non-goals

Not a provider swap. Not a rewrite of summary semantics. Not removing foreground
maintenance, which remains the correct emergency behaviour. Not speculative
summarization of turns that have not happened.

## 9. Independent prerequisite — maintenance backend qualification

Separate from this campaign and required before **any** provider change to the
maintenance role, including the obvious one.

Context maintenance writes consequential derived state. Every later turn trusts
the summary, the author never sees it, and a defect there is durable and
invisible. GLM was previously declined as a maintenance backend precisely
because the Claude path had exercised schema enforcement that the OpenRouter
path had not — and that reasoning still stands, with the added weight of a live
complaint about world-rule semantics.

So: test Flash, absolutely. A 37-second HTTP operation is vastly preferable to
29 CLI invocations. But qualify it first, against a **maintenance qualification
corpus** — existing sessions whose expected canon, world-rule, and context
outcome are known — and require it to demonstrate:

- strict schema adherence, including the evidence-ID requirement on every fact;
- preservation of authored facts, with no dropped or merged distinctions;
- no ontology broadening — the summariser must not close a category or
  generalise a scoped rule (see `canon_scope`);
- correct handling of conflict, recorded as uncertainty rather than resolved;
- no promotion of inference to canon;
- stable output across repeated runs on identical input.

The existing summariser prompt already instructs most of this, and the current
Claude-generated summary is a usable reference: it recorded the Jacqueline
perception conflict as `uncertainties/conflicting` rather than deciding it,
which is exactly the behaviour a candidate backend must reproduce.

A candidate that fails any criterion is not a faster maintenance model; it is a
slower path to a corrupted story bible.

---

*Recorded 2026-09-06. Captured as a campaign; nothing implemented.*
