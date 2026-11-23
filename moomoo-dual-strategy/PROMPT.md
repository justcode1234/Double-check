# Independently verify Moo Moo two-vote paper execution

Pull GitHub `main`, record the exact commit checked, and read [AGENTS.md](AGENTS.md). Linux is the intended execution host. The Windows audit checked commit `c5b00f4` and found a filled 15-share QQQ order tagged `two_vote` in the Moomoo competition paper account. Treat that as a prior observation to verify independently.

## Check the Linux runner and the broker

1. Check the Linux scheduler, runner process, current logs, and saved risk and sleeve state. Verify whether the runner is active, which code revision it is using if the evidence supports that, and whether the scheduler is ready for the next authorized session.
2. Use the official Moomoo paper-trading and quote skills. Use read-only calls against the uniquely identified Moomoo × Qoder competition paper account. Reconcile holdings, working orders, and filled or cancelled orders with the runner's outbox and sleeve ledger. Follow history pagination until the broker reports that there are no more pages. Verify the QQQ fill by its order tag and matching receipt. Report discrepancies and keep account IDs and credentials private.

## Reproduce the failure cases offline

Use a fake broker and temporary state for these checks. Run the relevant repository tests as well.

| Check | What to verify |
| --- | --- |
| H and two-vote holding the same ticker | With H long 20 SPY shares, see whether two-vote can short 20 SPY shares and then fail to close at the scheduled flatten because the broker reports zero net shares. |
| Partial exit and restart | After four 19-share lots, fill only one share of a 76-share exit. Check whether a fresh agreement can open another lot while the four original lots still have shares. Separately, interrupt execution after the fill is saved but before the lot count is saved, reload state, and check whether the limit still holds. |
| Reserved profits | Start with an $80,000 entry budget. Close one trade for a $500 gain, then another for a $1,000 loss. Before fees, a separately protected reserve should hold $500 and the entry budget should be $79,000. Check the actual ledger values. |
| Order history pagination | Put an acknowledged order's terminal fill on page two. Check whether the runner finds it and avoids unnecessary cancellation or a duplicate submission. |
| Dashboard attribution | Check that a broker-confirmed QQQ position owned by two-vote appears under that sleeve. If ownership records are missing, verify that the dashboard marks ownership as unknown. |

Report each check as verified, failed, or blocked, with the evidence and test results. State whether unattended paper execution is ready and why. Keep this verification read-only. Leave the timer, running processes, orders, account state, and source code untouched.
