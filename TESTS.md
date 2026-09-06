# Lifecycle Tests

These are manual verification walkthroughs you can run in GenLayer
Studio (or via `genlayer-js` scripts) against a deployed
`PredictionAdjudicator` contract. Each one demonstrates a safety
guarantee added after steward review: staking is restricted to
`pending` claims, and `disputed` claims are terminal.

## Test 1 — A `resolved` claim rejects new stakes

Staking must only be possible while a claim is genuinely open — once
the first `resolve()` call has come in, later stakers would have an
unfair information advantage.

1. `submit_claim("some claim", "https://example.com", "some criteria")`
   → claim `0`, status `pending`.
2. `stake_true(0)` with some GEN attached → succeeds. Status is still
   `pending`.
3. `resolve(0)` → status becomes `resolved` (first resolution is always
   provisional).
4. `stake_false(0)` with some GEN attached → **must revert** with
   "Staking is only open while a claim is pending...".
5. `stake_true(0)` again → **must also revert**, for the same reason —
   the restriction applies to both sides, not just the side that hasn't
   been staked on yet.

Expected result: steps 4 and 5 both revert. Step 2 (staking while
`pending`) is the only staking window.

## Test 2 — A `disputed` claim cannot finalize after a refund

Once a claim is `disputed`, `claim_winnings()` treats it as a
full-refund state. If a disputed claim could later be resolved again,
it might reach `finalized` with a real winner *after* everyone had
already been refunded — double-paying the pool. `disputed` must
therefore be terminal.

1. `submit_claim(...)` → claim `1`, status `pending`.
2. `stake_true(1)` and `stake_false(1)` with GEN from two different
   accounts, while still `pending`.
3. `resolve(1)` → status `resolved`, some verdict `V1`.
4. `resolve(1)` again, this time with a genuinely different source or
   at a time where the evidence supports the opposite verdict → the two
   resolutions disagree, so status becomes `disputed`.
5. `claim_winnings(1)` from the `stake_true` account → refunds their
   full stake. `claimed` is now `true` for that account.
6. `resolve(1)` again → **must revert** with "Claim already settled —
   finalized and disputed claims are both terminal...", regardless of
   how strongly the new evidence supports a verdict.
7. `claim_winnings(1)` from the `stake_false` account → still succeeds
   (refund), since each staker's claim is tracked independently and step
   6 never changed the claim's status.

Expected result: step 6 reverts every time, so a `disputed` claim can
never reach `finalized` — whether or not a refund has already been
paid out. (Step 5 happening before step 6 in this walkthrough is
intentional: it shows the terminal check holds even after money has
already moved, which is the actual risk being closed.)

## Test 3 — A `finalized` claim still rejects `resolve()` (regression check)

This is the original guarantee from the first review round — confirming
it still holds after the changes above.

1. Get a claim to `resolved`, then `resolve()` it again with a matching
   verdict twice more, so `confirmations` reaches `2` and status becomes
   `finalized`.
2. `resolve(claim_id)` again → **must revert**, same error as Test 2
   step 6.
