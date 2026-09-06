# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import typing

# Used to send GEN back to a staker's EOA. Sending to an address on the
# GenLayer Chain layer (EOA or EVM contract) is an *external* message and
# goes through this IC's ghost contract — see "Value Transfers" in the
# GenLayer docs. The empty View/Write bodies are intentional: we only ever
# call emit_transfer() on this interface, never a named method.
@gl.evm.contract_interface
class _EOA:
    class View:
        pass

    class Write:
        pass


@allow_storage
@dataclass
class Claim:
    text: str
    source_url: str
    criteria: str
    status: str  # "pending" -> "resolved" -> "finalized" | "disputed"
    verdict: str  # "" | "true" | "false" | "undetermined"
    reasoning: str
    resolutions: u32  # how many times resolve() has run for this claim
    confirmations: u32  # consecutive matching resolutions in a row
    pool_true: u256  # total GEN staked on verdict == "true"
    pool_false: u256  # total GEN staked on verdict == "false"


@allow_storage
@dataclass
class Position:
    true_amount: u256
    false_amount: u256
    claimed: bool


class PredictionAdjudicator(gl.Contract):
    """
    A standalone Intelligent Contract: an AI-adjudicated prediction market.

    Anyone can file a factual claim with a source URL and a standard of
    proof. GenLayer validators read the source and reach consensus on a
    verdict (true / false / undetermined) via a custom Equivalence
    Principle check. While a claim is still open, anyone can stake GEN on
    which way it will resolve; once a claim is finalized (two consecutive
    matching resolutions), winners split the pool and claim their payout.
    """

    claims: TreeMap[u32, Claim]
    next_id: u32
    # claim_id -> staker address -> that staker's position on this claim
    positions: TreeMap[u32, TreeMap[Address, Position]]

    def __init__(self):
        self.next_id = u32(0)

    # ------------------------------------------------------------------
    # Filing and resolving claims
    # ------------------------------------------------------------------

    @gl.public.write
    def submit_claim(self, text: str, source_url: str, criteria: str) -> u32:
        """Registers a new claim in the "pending" state. Returns its id."""
        claim_id = self.next_id
        self.claims[claim_id] = Claim(
            text=text,
            source_url=source_url,
            criteria=criteria,
            status="pending",
            verdict="",
            reasoning="",
            resolutions=u32(0),
            confirmations=u32(0),
            pool_true=u256(0),
            pool_false=u256(0),
        )
        self.next_id = u32(self.next_id + 1)
        return claim_id

    @gl.public.write
    def resolve(self, claim_id: u32) -> typing.Any:
        if claim_id not in self.claims:
            raise gl.vm.UserError("Unknown claim id")
        claim = self.claims[claim_id]

        # Once a claim is finalized, its verdict is what any stakers are
        # paid out against. It must never move again — otherwise a verdict
        # could flip after payouts have already started.
        #
        # A disputed claim is likewise terminal: claim_winnings() treats
        # "disputed" as a full-refund state, so once any staker has been
        # refunded, the claim's outcome must never change again either —
        # otherwise it could later reach "finalized" with a real winner
        # after refunds already went out to everyone.
        if claim.status in ("finalized", "disputed"):
            raise gl.vm.UserError(
                "Claim already settled — finalized and disputed claims "
                "are both terminal and can never be resolved again"
            )

        source_url = claim.source_url
        criteria = claim.criteria
        text = claim.text

        prompt = f"""You are adjudicating a factual claim using the evidence at a given URL.

CLAIM: {text}
STANDARD OF PROOF: {criteria}

Read the evidence below and decide whether the claim is true, false, or
cannot be determined from this evidence.

Respond using ONLY the following JSON format, nothing else:
{{
  "verdict": str,   // exactly one of "true", "false", "undetermined"
  "reasoning": str  // one or two sentences citing what in the evidence supports this
}}
This result must be perfectly parsable by a JSON parser without errors.
"""

        def leader_fn():
            response = gl.nondet.web.get(source_url)
            web_data = response.body.decode("utf-8")
            full_prompt = prompt + f"\n\nEVIDENCE:\n{web_data}"
            return gl.nondet.exec_prompt(full_prompt, response_format="json")

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            leader_out = leaders_res.calldata
            if "verdict" not in leader_out or "reasoning" not in leader_out:
                return False
            if not leader_out["reasoning"]:
                return False
            if leader_out["verdict"] not in ("true", "false", "undetermined"):
                return False
            my_result = leader_fn()
            if my_result.get("verdict") not in ("true", "false", "undetermined"):
                return False
            return my_result["verdict"] == leader_out["verdict"]

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        new_verdict = result["verdict"]
        previous_verdict = claim.verdict

        claim.resolutions = u32(claim.resolutions + 1)

        if claim.status == "pending":
            claim.status = "resolved"
            claim.confirmations = u32(1)
        elif new_verdict == previous_verdict:
            claim.confirmations = u32(claim.confirmations + 1)
            claim.status = "finalized" if claim.confirmations >= u32(2) else "resolved"
        else:
            claim.status = "disputed"
            claim.confirmations = u32(1)

        claim.verdict = new_verdict
        claim.reasoning = result["reasoning"]
        self.claims[claim_id] = claim

        return {
            "claim_id": claim_id,
            "status": claim.status,
            "verdict": claim.verdict,
            "reasoning": claim.reasoning,
            "confirmations": claim.confirmations,
        }

    # ------------------------------------------------------------------
    # Prediction market: staking on a claim's eventual verdict
    # ------------------------------------------------------------------

    @gl.public.write.payable
    def stake_true(self, claim_id: u32) -> None:
        """Stakes the attached GEN on this claim resolving to verdict == "true"."""
        self._stake(claim_id, on_true=True)

    @gl.public.write.payable
    def stake_false(self, claim_id: u32) -> None:
        """Stakes the attached GEN on this claim resolving to verdict == "false"."""
        self._stake(claim_id, on_true=False)

    def _stake(self, claim_id: u32, on_true: bool) -> None:
        if claim_id not in self.claims:
            raise gl.vm.UserError("Unknown claim id")
        v = gl.message.value
        if v == u256(0):
            raise gl.vm.UserError("Send some GEN to stake")

        claim = self.claims[claim_id]
        if claim.status != "pending":
            raise gl.vm.UserError(
                "Staking is only open while a claim is pending — "
                "once the first resolution comes in, the outcome is no "
                "longer an open question and further stakes would be "
                "placed with an unfair information advantage."
            )

        sender = gl.message.sender_address
        if claim_id not in self.positions:
            self.positions[claim_id] = TreeMap()
        claim_positions = self.positions[claim_id]

        if sender in claim_positions:
            pos = claim_positions[sender]
        else:
            pos = Position(true_amount=u256(0), false_amount=u256(0), claimed=False)

        if on_true:
            pos.true_amount = u256(pos.true_amount + v)
            claim.pool_true = u256(claim.pool_true + v)
        else:
            pos.false_amount = u256(pos.false_amount + v)
            claim.pool_false = u256(claim.pool_false + v)

        claim_positions[sender] = pos
        self.positions[claim_id] = claim_positions
        self.claims[claim_id] = claim

    @gl.public.write
    def claim_winnings(self, claim_id: u32) -> u256:
        """
        Pays out (or refunds) the caller's stake on this claim, in GEN.

        - status == "finalized" and verdict in ("true", "false"): winners
          split the full pool (pool_true + pool_false) proportionally to
          their stake on the winning side. Losers get nothing.
        - status == "finalized" and verdict == "undetermined", or
          status == "disputed": no reliable winner, so every staker gets
          their own stake back.
        - Otherwise (still pending/resolved): reverts — payouts only
          happen once a claim has stopped moving.
        """
        if claim_id not in self.claims:
            raise gl.vm.UserError("Unknown claim id")
        claim = self.claims[claim_id]

        sender = gl.message.sender_address
        if claim_id not in self.positions or sender not in self.positions[claim_id]:
            raise gl.vm.UserError("No stake found for this claim")
        claim_positions = self.positions[claim_id]
        pos = claim_positions[sender]

        if pos.claimed:
            raise gl.vm.UserError("Already claimed")

        if claim.status == "finalized":
            total_pool = u256(claim.pool_true + claim.pool_false)
            if claim.verdict == "true":
                if claim.pool_true == u256(0):
                    payout = u256(0)
                else:
                    payout = u256((pos.true_amount * total_pool) // claim.pool_true)
            elif claim.verdict == "false":
                if claim.pool_false == u256(0):
                    payout = u256(0)
                else:
                    payout = u256((pos.false_amount * total_pool) // claim.pool_false)
            else:  # "undetermined" — no side wins, refund everyone
                payout = u256(pos.true_amount + pos.false_amount)
        elif claim.status == "disputed":
            payout = u256(pos.true_amount + pos.false_amount)
        else:
            raise gl.vm.UserError("Claim is not finalized or disputed yet")

        pos.claimed = True
        claim_positions[sender] = pos
        self.positions[claim_id] = claim_positions

        if payout > u256(0):
            _EOA(sender).emit_transfer(value=payout)

        return payout

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    @gl.public.view
    def get_claim(self, claim_id: u32) -> typing.Any:
        if claim_id not in self.claims:
            raise gl.vm.UserError("Unknown claim id")
        c = self.claims[claim_id]
        return {
            "text": c.text,
            "source_url": c.source_url,
            "criteria": c.criteria,
            "status": c.status,
            "verdict": c.verdict,
            "reasoning": c.reasoning,
            "resolutions": c.resolutions,
            "confirmations": c.confirmations,
            "pool_true": c.pool_true,
            "pool_false": c.pool_false,
        }

    @gl.public.view
    def get_position(self, claim_id: u32, staker: Address) -> typing.Any:
        if claim_id not in self.positions or staker not in self.positions[claim_id]:
            return {"true_amount": u256(0), "false_amount": u256(0), "claimed": False}
        pos = self.positions[claim_id][staker]
        return {
            "true_amount": pos.true_amount,
            "false_amount": pos.false_amount,
            "claimed": pos.claimed,
        }

    @gl.public.view
    def total_claims(self) -> u32:
        return self.next_id
