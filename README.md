# Prediction Adjudicator

A standalone Intelligent Contract on GenLayer: an AI-adjudicated
prediction market. Anyone can file a factual claim with a source URL and
a standard of proof, stake GEN on how it will resolve, and get paid out
once independent AI validators reach consensus on a verdict.

## Deployed contract

`0x9a17174aEAbd4Fc600abCA8A34f11be0ae7125aC` (GenLayer Studionet)

## Live demo

[https://brilliant-sprinkles-e68ec3.netlify.app]

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
claim moves to `disputed`. **Both `finalized` and `disputed` are
terminal** — `resolve()` reverts for a claim in either state, so a
verdict people have already been paid out (or refunded) against can
never move again. See [TESTS.md](./TESTS.md) for the exact call
sequences that verify this, including the case where a refund has
already been paid out before a further `resolve()` is attempted.

### Staking

Staking is open **only while a claim is `pending`** — the moment the
first resolution comes in, `stake_true`/`stake_false` revert, since a
staker who waits for a resolution before staking would have an unfair
information advantage over everyone who staked earlier. Send GEN to
`stake_true(claim_id)` or `stake_false(claim_id)` to back your
prediction while the claim is still open.

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
