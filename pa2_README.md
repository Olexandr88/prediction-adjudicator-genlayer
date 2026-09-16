# Prediction Adjudicator

A standalone Intelligent Contract on GenLayer: an AI-adjudicated
prediction market. Anyone can file a factual claim with a source URL and
a standard of proof, stake GEN on how it will resolve, and get paid out
once independent AI validators reach consensus on a verdict.

## Deployed contract

`0x9C54F704c3F8687124EE75E48f17A92E3d3481b4` (GenLayer Studionet)

## Live demo

https://frabjous-centaur-60094f.netlify.app

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
never move again.

### Staking

Staking is open **only while a claim is `pending`** — the moment the
first resolution comes in, `stake_true`/`stake_false` revert, since a
staker who waits for a resolution before staking would have an unfair
information advantage over everyone who staked earlier.

### Recovery paths for staked GEN

Two safety valves make sure pooled GEN can always be recovered, not just
in the happy path:

- **A claim nobody ever resolves.** If a claim is still `pending` or
  `resolved` seven days (`RECLAIM_AFTER`) after it was submitted, any
  staker can call `reclaim_stake(claim_id)` and withdraw their own stake
  in full. Without this, GEN staked on a claim that simply never gets
  enough `resolve()` calls would be locked forever. `reclaim_stake()`
  reverts on claims that already reached `finalized`/`disputed` — those
  have their own payout path via `claim_winnings()`.
- **A claim that finalizes on a side nobody staked.** If the winning
  verdict's pool is empty (e.g. the claim finalizes `true` but every
  staker backed `false`), there is no ratio to split the pot by.
  `claim_winnings()` detects this and refunds every staker their own
  stake instead of performing a division by zero.

Otherwise, once a claim is `finalized`, stakers call
`claim_winnings(claim_id)`:

- If the claim finalized with a `true`/`false` verdict that people
  actually staked on, everyone on the winning side splits the **entire
  pool** (both sides) proportionally to their stake. Losers get nothing.
- If the claim finalized with verdict `undetermined`, or moved to
  `disputed` instead, everyone gets their own stake back in full.

`get_claim` returns `pool_true`, `pool_false` and `submitted_at` along
with the lifecycle fields, and `get_position(claim_id, staker)` returns
any given address's stake and whether they've already claimed.

## Files

- `contracts/prediction_adjudicator.py` — the contract (Python, GenLayer SDK).
- `tests/direct/test_lifecycle.py` — executable pytest tests (GenLayer's
  official `genlayer-test` / `gltest` Direct Mode) covering the
  pending-only staking restriction, the terminal-disputed guarantee, the
  zero-pool refund path, and `reclaim_stake()`'s guards. Run with
  `pip install genlayer-test && gltest tests/ -v`.
- `pyproject.toml` — points `gltest` at the `contracts/` directory so the
  test suite can find and deploy the contract.
- `TESTS.md` — narrative walkthrough companion to the executable tests.
- `index.html` — a dependency-free frontend (`genlayer-js` only, no
  build step) for filing claims, resolving them, staking, and claiming
  winnings.
