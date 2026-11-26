# PRD: Real-Time Mule-Network Interception for UPI Payments

**Status:** Draft — business framing under review
**Author:** Security Engineering (internal proposal)
**Audience:** Payments Engineering leadership, Risk & Compliance, Head of Security

> Note on framing choices made in this draft (flag if wrong): this is written as a
> realistic internal security-team pitch to an existing UPI-connected payment
> gateway's leadership, grounded in how UPI settlement actually works, with success
> measured in money/risk first and engineering metrics second. These were
> recommended defaults from an in-progress review — correct any of them before this
> is treated as final.

## 1. Business context

Our fraud model scores each UPI transaction independently and takes ~2 seconds per
check. An attacker who compromises one account can launder large sums by splitting
the amount across thousands of small transfers through a network of mule accounts,
then cashing out before any human or system reconstructs the pattern. No single
transfer in the chain looks anomalous — the fraud signal only exists in the *shape*
of the money movement across accounts, which our current per-transaction model
structurally cannot see.

This is not hypothetical: UPI's growth has been accompanied by a parallel growth in
mule-account networks used for exactly this kind of layering, and RBI/NPCI have been
tightening expectations on banks and PSPs to detect and contain mule-account activity
rather than just report it after the fact. A platform that cannot demonstrate this
capability carries both direct fraud-loss exposure and regulatory/reputational
exposure.

## 2. Why this is solvable without hurting the customer experience

UPI's customer-facing speed ("instant" credit/debit in-app) is not the same thing as
final inter-bank settlement. The payment appears instant to both parties, but the
underlying movement of real money between banks is still reconciled through NPCI's
settlement process, which is not instantaneous at the inter-bank ledger level. That
gap — between "the customer sees the money move" and "the money is fully, finally
settled between banks" — is where this proposal operates. We are not proposing to
reverse a completed payment; we are proposing to catch and hold suspect money
*inside a window that already exists*, before it becomes freely withdrawable, using
network-level detection that is cheap enough to run on every transaction.

*(This is the single biggest assumption in this PRD and needs sign-off from
whoever owns our NPCI/settlement integration before this goes further — see §8.)*

## 3. Goals

- Reduce realized fraud losses from multi-hop mule-network laundering by detecting
  the network pattern during the settlement window and freezing implicated funds
  before cash-out, without adding customer-visible latency or declining legitimate
  payments outright.
- Give Risk & Compliance a defensible, evidence-backed detection trail (which
  accounts, which edges, why flagged) usable for SARs/regulatory reporting, not just
  a black-box score.
- Do this as an add-on layer to the existing fraud stack, not a replacement — lowest
  possible integration cost and risk to the current (working) per-transaction model.

## 4. Non-goals

- Not a general AML/KYC case-management system, and not a replacement for the
  existing per-transaction fraud model.
- Not solving single-account, non-network fraud (stolen credentials, one-off
  unauthorized payment) — that's the existing model's job.
- Not, in this phase, a claim that we can reliably intercept funds once they have
  left NPCI's settlement window entirely — that failure mode is accepted and costed,
  not solved (see §7).

## 5. Stakeholders

| Stakeholder | Interest |
|---|---|
| Head of Security / Fraud team | Owns the ask; needs this to actually reduce loss, not just add alerts |
| Payments Engineering | Owns the hot path; needs a hard guarantee this adds no customer-visible latency |
| Risk & Compliance | Needs evidence trails and needs to sign off that "holding funds" is operationally and legally sound |
| Customers (indirect) | Must never notice this exists unless they are the (rare) legitimate false-positive case |
| Receiving-bank-side settlement ops | The counterparty on the other end of any hold/freeze request — a real dependency, not fully in our control |

## 6. Business success metrics (primary)

| Metric | How it's measured |
|---|---|
| ₹ fraud losses prevented / year | Value of funds successfully held before cash-out, validated as fraudulent on review, vs. a trailing baseline of losses from this fraud pattern pre-launch |
| Cost of false positives | Number of legitimate transactions held × estimated customer-trust/support cost per incident, kept below an agreed threshold |
| Net financial case | Fraud losses prevented minus (build/run cost + false-positive cost) — must be clearly positive to justify continued investment past pilot |
| Regulatory posture | Whether this materially improves our standing on mule-account detection expectations from RBI/NPCI (qualitative, tracked via compliance sign-off, not a number we control directly) |

## 7. Supporting technical metrics (secondary — these justify the business numbers, they aren't the goal themselves)

- Latency added to the legitimate payment path (target: customer-imperceptible).
- % of injected/known fraud-ring patterns detected before the settlement window closes.
- False-positive rate on benign bursty traffic (payroll fan-out, merchant settlement batches).

## 8. Key open risk — needs an answer before scoping engineering work

**Does a settlement window of sufficient, reliable length actually exist for us to
act within, and do we have (or can we get) the ability to place a hold on funds
within it?** This is a real operational/legal question about our NPCI/settlement
integration and the receiving bank's cooperation — not something this PRD can settle
on its own. If the answer is "no, or not reliably," the whole proposal needs to shift
from *prevention* to *fast post-hoc flagging for recovery/reporting*, which is a much
smaller claim with a different cost-benefit case.

## 9. Scope / phasing (business framing)

- **Phase 0 (this doc + a prototype/simulation):** prove the detection mechanism
  works in principle and produce real numbers for the cost-benefit case above, before
  asking for production engineering budget.
- **Phase 1 (pilot, if Phase 0's numbers justify it):** shadow mode in production —
  detect and log, do not yet hold funds — to measure real detection/false-positive
  rates against real traffic without taking any customer-facing risk.
- **Phase 2 (if pilot numbers hold):** enable the hold/escrow action for a limited,
  monitored rollout.

## 10. Open questions for leadership / compliance sign-off

1. Who owns confirming the real settlement-window assumption in §2/§8 — Payments
   Engineering, or a direct conversation with NPCI/partner banks?
2. What is an acceptable false-positive cost threshold (in ₹ and in customer-trust
   terms) before this is considered net-negative?
3. Does Risk & Compliance need to approve the "hold funds in escrow" action as a
   distinct operational/legal capability before even a shadow-mode pilot?
