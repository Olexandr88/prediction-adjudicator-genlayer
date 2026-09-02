# Prediction Adjudicator

A standalone Intelligent Contract on GenLayer: an AI-adjudicated
prediction market. Anyone can file a factual claim with a source URL and
a standard of proof, stake GEN on how it will resolve, and get paid out
once independent AI validators reach consensus on a verdict.

## Deployed contract

`0x5b2427afFaE5Ed2a05E0481b8ee6BE9472C88eFE` (GenLayer Studionet)

## What it does

Anyone can call `submit_claim(text, source_url, criteria)` to register a
factual claim (e.g. "GenLayer testnet processed over 1M transactions in
August 2026") along with a source and a standard of proof. Anyone can then
call `resolve(claim_id)` to have the network read the source and reach a
verdict — `true`, `false`, or `undetermined`.

Reaching that verdict is the interesting part: fetching a live web page
and asking an LLM to judge it are both nondeterministic operations. Every
validator independently re-fetches the page and re-runs the prompt, and a
custom Equivalence Principle check (`validator_fn`) requires only that
validators agree on the **verdict** — the `reasoning` text is allowed to
vary, since natural-language explanations legitimately differ between
LLM runs even when they agree on the answer. `strict_eq` would be too
strict here; this contract shows how to write your own comparison
instead.

### Lifecycle vs. verdict

Each claim tracks two things separately:

- **`status`** — where the claim is in its lifecycle:
  `pending → resolved → finalized | disputed`
- **`verdict`** — what the evidence says: `"" | "true" | "false" | "undetermined"`

A single resolution only ever produces a provisional `resolved` claim. It
takes **two consecutive resolutions agreeing on the same verdict** for a
claim to become `finalized`. If a later resolution disagrees instead, the
claim moves to `disputed`. Once `finalized`, a claim is frozen —
`resolve()` will revert rather than let a settled verdict move again.

### Staking

While a claim is `pending` or `resolved` (i.e. still open), anyone can
call the payable functions `stake_true(claim_id)` or
`stake_false(claim_id)`, sending GEN to back their prediction.

Once a claim is `finalized`, stakers call `claim_winnings(claim_id)`:

- If the verdict is `true` or `false`, everyone who staked on the winning
  side splits the entire pool (both sides combined), proportionally to
  their own stake. Losers get nothing.
- If the verdict is `undetermined`, or the claim ended up `disputed`
  instead of finalized, there's no reliable winner — every staker gets
  their own stake back in full.

`get_claim(claim_id)` returns the claim's text, source, criteria, status,
verdict, reasoning, resolution/confirmation counts, and both staking
pools. `get_position(claim_id, staker)` returns any address's stake and
whether they've already claimed.

## Why this is useful beyond a demo

The pattern here — a nondeterministic `leader_fn` (web read + LLM
judgment), a custom `validator_fn` that defines what "agreement" means
for the use case, and a lifecycle that only finalizes after repeated
agreement — is directly reusable for any GenLayer contract that needs to
turn real-world, ambiguous information into an on-chain, trustworthy
outcome: dispute resolution, insurance triggers, content moderation, or
other prediction markets.

## Files

- `prediction_adjudicator.py` — the contract (Python, GenLayer SDK).
- `index.html` — a dependency-free frontend (`genlayer-js` only, no
  build step) for filing claims, resolving them, staking, and claiming
  winnings.
