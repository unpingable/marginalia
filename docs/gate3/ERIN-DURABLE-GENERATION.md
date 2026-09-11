# Durable generation: writer onboarding

Durable custody is used for every generation. The project control enables or pauses
generation itself; it is not an optional reliability-mode choice.

1. Open the project and choose **Edit** in the right-hand project workspace.
2. Find **Generation** and enable **Generation enabled**.
3. Optionally choose a fallback model. Fallback runs only after a qualified
   terminal provider failure; an unknown outcome is reconciled and never retried
   on another model.
4. Choose **Save direction**.

The expandable **What does this change?** explanation sits directly below the
control. Turning the setting off pauses the composer and stops new dispatches. It does not abandon
or synchronously reroute work Marginalia already holds.

When a response is accepted, its header shows the actual provider and upstream
model recorded for that response. The line beneath it shows reported token usage
when available and labels provider cost as one of:

- **known** — currently used for zero API charge on a local model; local hardware
  and electricity are deliberately not priced;
- **estimated** — calculated from explicit per-model input/output rates in the
  deployment catalog;
- **unavailable** — the provider route did not expose an exact charge and no
  deployment rate was configured.

If a tab closes or loses its acknowledgement, reopen the conversation. Marginalia
looks up the saved delivery identity, displays custody or unknown reconciliation,
and refreshes the accepted turn without creating a second one. Do not start a new
generation while the UI says the outcome is unknown.
