# Practice-account capability report

Observed through authenticated Moomoo MCP on 2026-11-17 UTC (2026-11-18 local).

## Verified

- Full catalog contains 104 tools. No paper-account create/rename/initial-balance operation is exposed. Account listing is not account creation.
- One ordinary U.S. practice margin account is returned, separately from the named challenge account. No new accounts were created.
- Ordinary practice account: market ID 100, account type 1, title explicitly states Paper Trading. The simulated-account endpoint itself scopes this evidence; the response does not include a literal `SIMULATE` field.
- Read-only practice funds: cash $1,000,000, NAV $1,000,000, gross margin buying power $2,000,000. Positions and queried order history are empty. No balance was changed. Margin buying power will not be used as the cash budget.
- The official competition account is excluded. No funds, positions, orders, resets or changes were requested for it.
- Only paper market (3) and limit (1) orders are exposed. No native paper stop. Placement is non-idempotent; order notes are storage only, not broker deduplication keys.
- Dedicated paper fills and fee-query endpoints are not exposed. Cumulative order fills plus position reconciliation are required; broker fees stay unknown if unavailable.
- Broker calendar returns Nov 17,18,19,20,23,24,25 and half-day Nov27. Nov26 is absent. Nov27 has 12,600 trading seconds.

## Fallback and blockers

Candidate fallback is one ordinary practice account with two separately audited $50,000 sub-ledgers. Its broker cash/NAV remain distinct from those allocations. Shared account-level netting, cash and margin are not independent accounts. A is individual stocks only; B is SPY only.

Both portfolios currently remain SHADOW, and no strategy orders have been placed. One supervised validation round trip (BUY then close-only SELL, 1 US.SPY, both filled and reconciled; broker flat afterwards) ran on 2026-11-17 against an isolated validation ledger. See `evidence/practice-lifecycle.json`. That round trip is not strategy activity. The user has explicitly authorized the discovered ordinary practice account for this experiment; the gitignored `private/accounts.json` now records that authorization and both $50,000 strategy mappings. This resolves account selection, not the historical, lifecycle, independent-monitor or Cloud release gates. The competition account remains denied.

Manual second-account creation under this profile is not verified. The official materials expose Discover → Paper Trade as the app entry point, but do not prove an additional same-market account creation flow. Ask Moomoo support whether two independent same-profile U.S. practice accounts are supported; do not create extra user identities.

The local `moomoo` and `gh` executables were not found. Authenticated Desktop MCP access is working, but it does not give an independent Python sentinel or Cloud container the same credentials. A separate supervised transport and Cloud Vault remain required. No Cloud Agent connector is exposed in this session; official setup is Extensions → Plugins → Qoder Cloud Agents (Qoder publisher), then a fresh session.

## Sources

- https://open.moomoo.com/cli/sim (currently renders `futu sim` examples despite Moomoo branding; verify the installed executable's help before use).
- https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/ (fees, no reset, ordinary practice recommendation and Cloud setup).
- Current MCP `sim_trade_account_list`, `sim_trade_cash_info`, `sim_trade_position_list`, `sim_trade_history_order_list`, `sim_trade_input_order`, `quote_trading_days` schemas/responses.

Practice results do not count toward challenge ranking. Only the official competition account does, and it is outside this experiment's authorization.
