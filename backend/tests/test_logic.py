from datetime import datetime, timedelta, timezone

import pytest

from app.config import TimingConfig
from app.logic import (
    NEXT_PHASE,
    Phase,
    TieBreak,
    Vote,
    decide,
    everyone_voted,
    is_chat,
    is_vote,
    latest_votes,
    next_phase,
    phase_duration,
    round_of,
    tally_round,
)

P = ["A", "B", "C"]
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def v(round: int, voter: str, target: str, t: float = 0) -> Vote:
    return Vote(round=round, voter=voter, target=target, cast_at=T0 + timedelta(seconds=t))


# --- phase transitions -------------------------------------------------------


def test_full_phase_sequence():
    sequence = [Phase.WAITING]
    while sequence[-1] in NEXT_PHASE:
        sequence.append(next_phase(sequence[-1]))
    assert sequence == [
        Phase.WAITING,
        Phase.ROUND_1_CHAT,
        Phase.ROUND_1_VOTE,
        Phase.ROUND_2_CHAT,
        Phase.ROUND_2_VOTE,
        Phase.RESULTS,
    ]


def test_results_is_terminal():
    with pytest.raises(ValueError, match="terminal"):
        next_phase(Phase.RESULTS)


@pytest.mark.parametrize(
    "phase, rnd, chat, vote",
    [
        (Phase.WAITING, None, False, False),
        (Phase.ROUND_1_CHAT, 1, True, False),
        (Phase.ROUND_1_VOTE, 1, False, True),
        (Phase.ROUND_2_CHAT, 2, True, False),
        (Phase.ROUND_2_VOTE, 2, False, True),
        (Phase.RESULTS, None, False, False),
    ],
)
def test_phase_properties(phase, rnd, chat, vote):
    assert round_of(phase) == rnd
    assert is_chat(phase) is chat
    assert is_vote(phase) is vote


def test_phase_durations_come_from_config():
    timing = TimingConfig(chat_seconds=60, vote_seconds=15)
    assert phase_duration(Phase.ROUND_1_CHAT, timing) == 60
    assert phase_duration(Phase.ROUND_2_CHAT, timing) == 60
    assert phase_duration(Phase.ROUND_1_VOTE, timing) == 15
    assert phase_duration(Phase.ROUND_2_VOTE, timing) == 15
    assert phase_duration(Phase.WAITING, timing) is None
    assert phase_duration(Phase.RESULTS, timing) is None


# --- tally -------------------------------------------------------------------


def test_tally_lists_every_participant_in_order():
    tally = tally_round([v(1, "A", "B"), v(1, "B", "B"), v(1, "C", "A")], 1, P)
    assert list(tally.counts) == P
    assert tally.counts == {"A": 1, "B": 2, "C": 0}
    assert tally.abstentions == 0


def test_tally_only_counts_requested_round():
    votes = [v(1, "A", "B"), v(2, "A", "C")]
    assert tally_round(votes, 1, P).counts == {"A": 0, "B": 1, "C": 0}
    assert tally_round(votes, 2, P).counts == {"A": 0, "B": 0, "C": 1}


def test_self_vote_counts():
    assert tally_round([v(1, "A", "A")], 1, P).counts["A"] == 1


def test_abstentions_counted():
    tally = tally_round([v(1, "A", "C")], 1, P)
    assert tally.counts == {"A": 0, "B": 0, "C": 1}
    assert tally.abstentions == 2


def test_no_votes_all_abstain():
    tally = tally_round([], 1, P)
    assert tally.counts == {"A": 0, "B": 0, "C": 0}
    assert tally.abstentions == 3


def test_changed_vote_only_latest_counts():
    votes = [v(1, "A", "B", t=1), v(1, "A", "C", t=5), v(1, "B", "B", t=2)]
    tally = tally_round(votes, 1, P)
    assert tally.counts == {"A": 0, "B": 1, "C": 1}
    assert tally.abstentions == 1  # C never voted; A's two rows count once


def test_latest_is_by_timestamp_not_input_order():
    votes = [v(1, "A", "C", t=5), v(1, "A", "B", t=1)]
    assert latest_votes(votes, 1, P) == {"A": "C"}


def test_equal_timestamps_last_in_input_wins():
    votes = [v(1, "A", "B", t=3), v(1, "A", "C", t=3)]
    assert latest_votes(votes, 1, P) == {"A": "C"}


@pytest.mark.parametrize("vote", [v(1, "X", "A"), v(1, "A", "X")])
def test_non_participant_vote_rejected(vote):
    with pytest.raises(ValueError, match="non-participant"):
        tally_round([vote], 1, P)


def test_everyone_voted():
    votes = [v(1, "A", "B"), v(1, "B", "B")]
    assert not everyone_voted(votes, 1, P)
    assert everyone_voted(votes + [v(1, "C", "A")], 1, P)
    assert not everyone_voted(votes + [v(2, "C", "A")], 1, P)  # other round doesn't count


def test_everyone_voted_ignores_repeat_votes():
    votes = [v(1, "A", "B", t=1), v(1, "A", "C", t=2), v(1, "B", "A", t=3)]
    assert not everyone_voted(votes, 1, P)


# --- decide / tie-break ------------------------------------------------------


def test_clear_round_2_winner():
    votes = [
        v(1, "A", "A"), v(1, "B", "A"), v(1, "C", "A"),  # round 1 favours A...
        v(2, "A", "B"), v(2, "B", "B"), v(2, "C", "C"),  # ...but round 2 decides: B
    ]
    decision = decide(votes, P, seed=0)
    assert decision.winner == "B"
    assert decision.method == TieBreak.ROUND_2
    assert decision.tallies[1].counts == {"A": 3, "B": 0, "C": 0}
    assert decision.tallies[2].counts == {"A": 0, "B": 2, "C": 1}


def test_round_2_tie_resolved_by_combined_votes():
    votes = [
        v(1, "A", "C"), v(1, "B", "C"), v(1, "C", "A"),  # round 1: C=2, A=1
        v(2, "A", "A"), v(2, "B", "C"),                  # round 2: A=1, C=1, C abstains
    ]
    decision = decide(votes, P, seed=0)
    assert decision.winner == "C"  # combined: C=3, A=2
    assert decision.method == TieBreak.COMBINED
    assert decision.tallies[2].abstentions == 1


def test_combined_only_considers_round_2_leaders():
    votes = [
        v(1, "A", "C"), v(1, "B", "C"), v(1, "C", "C"),  # round 1: C=3
        v(2, "A", "A"), v(2, "B", "B"),                  # round 2: A=1, B=1, C=0
    ]
    decision = decide(votes, P, seed=0)
    # C has the most combined votes (3) but lost round 2, so it is not eligible.
    assert decision.winner in {"A", "B"}
    assert decision.method == TieBreak.RANDOM


def test_full_tie_resolved_by_seed():
    votes = [
        v(1, "A", "B"), v(1, "B", "C"), v(1, "C", "A"),
        v(2, "A", "B"), v(2, "B", "C"), v(2, "C", "A"),
    ]
    decision = decide(votes, P, seed=42)
    assert decision.method == TieBreak.RANDOM
    # Golden value: random.Random(42).choice(["A", "B", "C"]). Same on Python 3.10 and 3.12.
    # If this changes, stored session seeds no longer reproduce their results.
    assert decision.winner == "C"


def test_random_draw_is_reproducible_and_uses_seed():
    votes = [v(2, "A", "B"), v(2, "B", "C"), v(2, "C", "A")]
    for seed in range(20):
        assert decide(votes, P, seed).winner == decide(votes, P, seed).winner
    assert len({decide(votes, P, seed).winner for seed in range(50)}) > 1


def test_random_draw_independent_of_participant_order():
    votes = [v(2, "A", "B"), v(2, "B", "C"), v(2, "C", "A")]
    assert decide(votes, ["A", "B", "C"], 7).winner == decide(votes, ["C", "A", "B"], 7).winner


def test_round_2_all_abstain_falls_back_to_round_1():
    decision = decide([v(1, "A", "B"), v(1, "C", "B"), v(1, "B", "A")], P, seed=0)
    assert decision.winner == "B"
    assert decision.method == TieBreak.COMBINED
    assert decision.tallies[2].abstentions == 3


def test_nobody_votes_at_all_is_random():
    decision = decide([], P, seed=1)
    assert decision.method == TieBreak.RANDOM
    assert decision.winner == "A"  # random.Random(1).choice(["A", "B", "C"])


def test_changed_vote_affects_decision():
    votes = [
        v(2, "A", "B", t=1), v(2, "B", "B", t=2), v(2, "C", "C", t=3),
        v(2, "B", "C", t=10),  # B switches to C before the deadline
    ]
    decision = decide(votes, P, seed=0)
    assert decision.winner == "C"
    assert decision.method == TieBreak.ROUND_2
    assert decision.tallies[2].counts == {"A": 0, "B": 1, "C": 2}
