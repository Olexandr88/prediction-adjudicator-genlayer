"""
Executable lifecycle tests for PredictionAdjudicator, using GenLayer's
official testing suite (`genlayer-test` / `gltest`) in Direct Mode —
fast, in-memory contract execution with `mock_web`/`mock_llm` cheatcodes
standing in for the real nondeterministic web+LLM calls inside resolve().

Setup:
    pip install genlayer-test

Run:
    gltest tests/test_lifecycle.py -v

These replace the manual walkthroughs previously described in TESTS.md
with assertions that actually execute against the contract and fail the
test run if a regression breaks either guarantee.
"""

import pytest
from gltest import create_account
from gltest.assertions import tx_execution_succeeded


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("contracts/prediction_adjudicator.py")


def _resolve_with_verdict(contract, direct_vm, claim_id, verdict):
    """
    Resolves a claim once. Both the mocked web page and the mocked LLM
    response are held fixed for the duration of the call, so the
    leader's result and the validator's independent re-computation of
    leader_fn agree deterministically — reproducing what happens on the
    real network when validators genuinely agree.
    """
    direct_vm.mock_web(r".*", {"status": 200, "body": "mock flight evidence page"})
    direct_vm.mock_llm(
        r".*",
        f'{{"verdict": "{verdict}", "reasoning": "mocked evidence supports this verdict"}}',
    )
    result = contract.resolve(claim_id)
    direct_vm.clear_mocks()
    return result


class TestResolvedClaimRejectsNewStakes:
    """Steward guarantee #1: staking is pending-only."""

    def test_stake_succeeds_while_pending(self, contract, direct_vm):
        alice = create_account()
        bob = create_account()

        direct_vm.sender = alice
        claim_id = contract.submit_claim(
            "GenLayer testnet processed 1M+ transactions in August 2026",
            "https://example.com/evidence",
            "The page must state the transaction count explicitly",
        )

        direct_vm.sender = bob
        tx = contract.stake_true(claim_id, value=10)
        assert tx_execution_succeeded(tx)

    def test_stake_reverts_once_resolved(self, contract, direct_vm):
        alice = create_account()
        bob = create_account()

        direct_vm.sender = alice
        claim_id = contract.submit_claim(
            "GenLayer testnet processed 1M+ transactions in August 2026",
            "https://example.com/evidence",
            "The page must state the transaction count explicitly",
        )

        # First resolution moves the claim out of "pending" into "resolved".
        _resolve_with_verdict(contract, direct_vm, claim_id, "true")
        claim = contract.get_claim(claim_id)
        assert claim["status"] == "resolved"

        # Staking on either side must now revert, for both a fresh
        # staker and one who already staked while it was pending.
        direct_vm.sender = bob
        with direct_vm.expect_revert("Staking is only open while a claim is pending"):
            contract.stake_true(claim_id, value=10)
        with direct_vm.expect_revert("Staking is only open while a claim is pending"):
            contract.stake_false(claim_id, value=10)


class TestDisputedClaimCannotFinalizeAfterRefund:
    """Steward guarantee #2: disputed is terminal, even after a refund."""

    def test_dispute_then_refund_then_resolve_reverts(self, contract, direct_vm):
        alice = create_account()
        bob = create_account()

        direct_vm.sender = alice
        claim_id = contract.submit_claim(
            "A contested claim",
            "https://example.com/evidence",
            "Some criteria",
        )

        direct_vm.sender = bob
        contract.stake_true(claim_id, value=10)

        # First resolution: "true" -> status "resolved".
        _resolve_with_verdict(contract, direct_vm, claim_id, "true")

        # Second resolution disagrees: "false" -> status "disputed".
        _resolve_with_verdict(contract, direct_vm, claim_id, "false")
        claim = contract.get_claim(claim_id)
        assert claim["status"] == "disputed"

        # The staker claims a full refund while disputed.
        direct_vm.sender = bob
        payout = contract.claim_winnings(claim_id)
        assert payout == 10  # full stake refunded, not a partial payout

        # Even with evidence that would otherwise resolve cleanly,
        # resolve() must still revert — the refund already happened, so
        # the claim can never move to "finalized" after this point.
        with direct_vm.expect_revert("Claim already settled"):
            _resolve_with_verdict(contract, direct_vm, claim_id, "true")


class TestFinalizedClaimRejectsResolveRegression:
    """Original guarantee from the first review round, still enforced."""

    def test_finalized_claim_rejects_resolve(self, contract, direct_vm):
        alice = create_account()
        direct_vm.sender = alice
        claim_id = contract.submit_claim(
            "A claim that will be finalized",
            "https://example.com/evidence",
            "Some criteria",
        )

        _resolve_with_verdict(contract, direct_vm, claim_id, "true")  # -> resolved
        _resolve_with_verdict(contract, direct_vm, claim_id, "true")  # -> finalized

        claim = contract.get_claim(claim_id)
        assert claim["status"] == "finalized"
        assert claim["confirmations"] == 2

        with direct_vm.expect_revert("Claim already settled"):
            _resolve_with_verdict(contract, direct_vm, claim_id, "true")
