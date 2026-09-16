"""
Executable lifecycle tests for PredictionAdjudicator, using GenLayer's
official testing suite (`genlayer-test` / `gltest`) in Direct Mode.

Setup:
    pip install genlayer-test

Run:
    gltest tests/ -v
"""

import pytest
from gltest import create_account
from gltest.assertions import tx_execution_succeeded


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("contracts/prediction_adjudicator.py")


def _resolve_with_verdict(contract, direct_vm, claim_id, verdict):
    direct_vm.mock_web(r".*", {"status": 200, "body": "mock evidence page"})
    direct_vm.mock_llm(
        r".*",
        f'{{"verdict": "{verdict}", "reasoning": "mocked evidence supports this verdict"}}',
    )
    result = contract.resolve(claim_id)
    direct_vm.clear_mocks()
    return result


class TestResolvedClaimRejectsNewStakes:
    """Steward guarantee: staking is pending-only."""

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

        _resolve_with_verdict(contract, direct_vm, claim_id, "true")
        claim = contract.get_claim(claim_id)
        assert claim["status"] == "resolved"

        direct_vm.sender = bob
        with direct_vm.expect_revert("Staking is only open while a claim is pending"):
            contract.stake_true(claim_id, value=10)
        with direct_vm.expect_revert("Staking is only open while a claim is pending"):
            contract.stake_false(claim_id, value=10)


class TestDisputedClaimCannotFinalizeAfterRefund:
    """Steward guarantee: disputed is terminal, even after a refund."""

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

        _resolve_with_verdict(contract, direct_vm, claim_id, "true")
        _resolve_with_verdict(contract, direct_vm, claim_id, "false")
        claim = contract.get_claim(claim_id)
        assert claim["status"] == "disputed"

        direct_vm.sender = bob
        payout = contract.claim_winnings(claim_id)
        assert payout == 10

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

        _resolve_with_verdict(contract, direct_vm, claim_id, "true")
        _resolve_with_verdict(contract, direct_vm, claim_id, "true")

        claim = contract.get_claim(claim_id)
        assert claim["status"] == "finalized"
        assert claim["confirmations"] == 2

        with direct_vm.expect_revert("Claim already settled"):
            _resolve_with_verdict(contract, direct_vm, claim_id, "true")


class TestZeroPoolSideRefundsInsteadOfDividingByZero:
    """
    New steward guarantee: if a claim finalizes on a verdict nobody
    staked on, claim_winnings() refunds stakers instead of dividing by
    zero (pos.true_amount * total_pool // claim.pool_true would crash
    if pool_true == 0).
    """

    def test_finalized_true_with_zero_true_pool_refunds(self, contract, direct_vm):
        alice = create_account()
        bob = create_account()

        direct_vm.sender = alice
        claim_id = contract.submit_claim(
            "A claim where everyone bet wrong",
            "https://example.com/evidence",
            "Some criteria",
        )

        # Bob stakes only on "false" — pool_true stays at 0.
        direct_vm.sender = bob
        contract.stake_false(claim_id, value=15)

        # Claim finalizes as "true" even though nobody staked on it.
        _resolve_with_verdict(contract, direct_vm, claim_id, "true")
        _resolve_with_verdict(contract, direct_vm, claim_id, "true")
        claim = contract.get_claim(claim_id)
        assert claim["status"] == "finalized"
        assert claim["verdict"] == "true"
        assert claim["pool_true"] == 0

        # Must not raise a division-by-zero error — must refund instead.
        direct_vm.sender = bob
        payout = contract.claim_winnings(claim_id)
        assert payout == 15  # full stake refunded, not zero and not a crash


class TestReclaimStakeForStuckClaims:
    """
    New steward guarantee: if a claim never reaches a terminal state,
    stakers can recover their own GEN after RECLAIM_AFTER has elapsed,
    instead of it being locked forever.
    """

    def test_reclaim_reverts_before_deadline(self, contract, direct_vm):
        alice = create_account()
        bob = create_account()

        direct_vm.sender = alice
        claim_id = contract.submit_claim(
            "A claim nobody will ever resolve",
            "https://example.com/evidence",
            "Some criteria",
        )

        direct_vm.sender = bob
        contract.stake_true(claim_id, value=20)

        with direct_vm.expect_revert("reclaim deadline hasn't passed yet"):
            contract.reclaim_stake(claim_id)

    def test_reclaim_reverts_on_finalized_claim(self, contract, direct_vm):
        alice = create_account()
        bob = create_account()

        direct_vm.sender = alice
        claim_id = contract.submit_claim(
            "A claim that gets finalized normally",
            "https://example.com/evidence",
            "Some criteria",
        )

        direct_vm.sender = bob
        contract.stake_true(claim_id, value=20)

        _resolve_with_verdict(contract, direct_vm, claim_id, "true")
        _resolve_with_verdict(contract, direct_vm, claim_id, "true")

        direct_vm.sender = bob
        with direct_vm.expect_revert("already reached a terminal state"):
            contract.reclaim_stake(claim_id)

    @pytest.mark.skip(
        reason=(
            "Requires RECLAIM_AFTER (7 real days) to actually elapse between "
            "submit_claim() and reclaim_stake(); no confirmed Direct Mode "
            "time-travel cheatcode is available in the current genlayer-test "
            "release. Run as an integration test against Studio/testnet with "
            "a claim that has genuinely aged past the deadline instead."
        )
    )
    def test_reclaim_succeeds_after_deadline_integration(self):
        """
        Integration-mode steps (documented, not executed here):
        1. submit_claim(...) on a real network.
        2. stake_true(claim_id) or stake_false(claim_id) from one or more
           accounts.
        3. Let RECLAIM_AFTER (7 days) genuinely elapse without ever
           calling resolve() to a terminal state.
        4. reclaim_stake(claim_id) from a staker -> succeeds, returns
           their full combined true_amount + false_amount, and marks
           their position claimed.
        5. reclaim_stake(claim_id) again from the same staker -> reverts
           with "Already claimed".
        """
