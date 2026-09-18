"""Tests for lamarck.analysis.exposure — recipe exposure / retro-provenance.

Three layers, per the Stage-0 brief:

(a) hand-built synthetic EventRecord lists (dummy hashes; the fold never
    needs a real chain) with hand-computed expected numbers for every rule
    R1–R7, plus focused unit tests for the matcher (word boundaries, whole
    hyphenated tokens), the sentence split, the R6 clause parser, the pure
    ``classify_acquisitions``, the ``KnowledgeFold`` and R3's two pair rules
    ("connected", the default, vs "sentence");
(b) a determinism test over a small scripted live run: two writes are
    byte-identical, the documented top-level keys are present, values are
    ints/strings/bools only, and the module's own journal/satchel/co-presence
    fold agrees with ``WorldStateFold``;
(c) structural facts over the accepted Phase-1 run when it is present
    locally (numbers are NOT pinned here — the coordinator pins them).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from lamarck.analysis.exposure import (
    DEFAULT_PAIR_RULE,
    Acquisition,
    ExposureRecord,
    KnowledgeFold,
    NameMatcher,
    PairRule,
    Production,
    RecipeNames,
    classify_acquisitions,
    exposure_records,
    find_assertions,
    fold_exposure,
    match_names,
    names_from_config,
    names_from_events,
    split_sentences,
    write_exposure,
)
from lamarck.contracts import EventKind, EventRecord
from lamarck.engine import WorldStateFold, load_live_config
from lamarck.eventstore import EventStore
from lamarck.live import run_live
from lamarck.serving import ScriptedBackend
from lamarck.universes.wuxing import BASES
from tests.scripted_llm import make as make_scripted

REPO_ROOT = Path(__file__).resolve().parents[1]
VALLEY_TOML = REPO_ROOT / "configs" / "valley.toml"
ACCEPTED_RUN = REPO_ROOT / "runs" / "phase1-accept2"

_DUMMY_HASH = "0" * 64

TOP_LEVEL_KEYS = {
    "run_id",
    "window_days",
    "pair_rule",
    "utterances",
    "exposures",
    "control",
    "acquisitions",
    "assertions",
    "per_agent",
    "examples",
}

# Hand-made name lists. MATCH_NAMES deliberately includes 'firebrand' (a base
# name inside a compound) and 'ember-ash' (a hyphenated token) for boundary
# tests; FOLD_NAMES is the small universe of the synthetic scenario.
MATCH_NAMES = RecipeNames(
    bases=tuple(BASES),
    compounds=("murmuring-smoke", "firebrand", "ember-ash", "silt-ash"),
    tiers={"murmuring-smoke": 1, "firebrand": 1, "ember-ash": 3, "silt-ash": 2},
)
FOLD_NAMES = RecipeNames(
    bases=tuple(BASES),
    compounds=("jade-salt", "murmuring-smoke", "silt-ash"),
    tiers={"murmuring-smoke": 1, "silt-ash": 2, "jade-salt": 3},
)
PARSE_NAMES = RecipeNames(
    bases=tuple(BASES),
    compounds=(
        "murmuring-smoke",
        "silt-ash",
        "azure-coil",
        "burnished-iron",
        "autumn-mirror",
        "whispering-bone",
        "verdant-root",
        "jade-salt",
    ),
)

# ------------------------------------------------------------ event helpers


def _ev(
    seq: int,
    kind: EventKind,
    *,
    actor: str = "",
    payload: dict[str, Any] | None = None,
    day: int = 0,
    tick: int = 0,
) -> EventRecord:
    return EventRecord(
        seq=seq,
        day=day,
        tick=tick,
        kind=kind,
        actor=actor,
        payload=payload or {},
        qi_delta=0,
        stones_delta=0,
        hash=_DUMMY_HASH,
    )


def _spawn(seq: int, agent: str, name: str) -> EventRecord:
    payload = {"agent_id": agent, "qi_max": 1000, "starting_stones": 20, "name": name}
    return _ev(seq, EventKind.AGENT_SPAWNED, payload=payload)


def _attempt(
    seq: int,
    agent: str,
    day: int,
    steps: list[list[str]],
    products: list[str],
    *,
    message: str = "the crucible yields something",
    claims: list[dict[str, Any]] | None = None,
) -> EventRecord:
    payload = {
        "steps": steps,
        "step_products": products,
        "message": message,
        "claims": claims or [],
    }
    return _ev(seq, EventKind.TASK_ATTEMPT, actor=agent, payload=payload, day=day)


def _converse(seq: int, agent: str, day: int, text: str, *, degraded: bool = False) -> EventRecord:
    payload: dict[str, Any] = {"type": "converse", "target": "someone", "text": text}
    if degraded:
        payload["degraded"] = True
        payload["reason"] = "malformed"
    return _ev(seq, EventKind.ACTION, actor=agent, payload=payload, day=day)


def _note(seq: int, agent: str, day: int, text: str) -> EventRecord:
    return _ev(seq, EventKind.ACTION, actor=agent, payload={"type": "note", "text": text}, day=day)


def _travel(seq: int, agent: str, day: int, to: str, *, degraded: bool = False) -> EventRecord:
    payload: dict[str, Any] = {"type": "travel", "to": to}
    if degraded:
        payload["degraded"] = True
    return _ev(seq, EventKind.ACTION, actor=agent, payload=payload, day=day)


def _llm(seq: int, agent: str, day: int, purpose: str) -> EventRecord:
    payload = {"purpose": purpose, "agent": agent, "usage_in": 10, "usage_out": 5}
    return _ev(seq, EventKind.LLM_CALL, actor=agent, payload=payload, day=day)


def _day(seq: int, day: int) -> EventRecord:
    return _ev(seq, EventKind.DAY_STARTED, day=day)


def _envelope(user: str) -> str:
    return json.dumps({"system": "You are someone.", "template": "p2.3", "user": user})


# ------------------------------------------------------ the synthetic log

T7 = (
    "I have made murmuring-smoke from fire and wood. Earth and fire gave only slag. "
    "Water with murmuring-smoke births silt-ash!"
)
T8 = (
    "Does fire and wood make murmuring-smoke? Perhaps wood and water make silt-ash. "
    "Fire and metal yield jade-salt."
)
T9 = "learned: fire+wood -> murmuring-smoke\nwater+metal -> silt-ash"
T17 = "Fire and wood make murmuring-smoke."
FALSE_CLAIM = "Fire and metal yield jade-salt."

PROMPTS: dict[int, str] = {
    11: _envelope(
        "Day 0, round 2, tick 5.\n\nHEARD (oldest first)\nYan Hua: " + T7 + "\n\nTASK BOARD"
    ),
    12: _envelope("Dusk of day 0.\n\nHEARD (oldest first)\nYan Hua: " + T7),
    19: "raw prompt, not JSON\nYan Hua: " + T17,  # exercises the non-envelope fallback
    24: _envelope("Day 3, round 1, tick 2.\n\nHEARD (oldest first)\n(nothing)"),
    25: _envelope("Dusk of day 3.\n\nHEARD (oldest first)\nYan Hua: " + T17),
}


def synthetic_events() -> list[EventRecord]:
    """Three agents, five days; see the expectations in the tests below."""
    return [
        _ev(0, EventKind.RUN_STARTED, payload={"run_id": "synthetic", "mode": "live"}),
        _spawn(1, "a1", "Yan Hua"),
        _spawn(2, "a2", "Bo Shan"),
        _spawn(3, "a3", "Mei Lin"),
        _day(4, 0),
        _attempt(
            5,
            "a1",
            0,
            [["wood", "fire"], ["earth", "fire"], ["murmuring-smoke", "water"]],
            ["murmuring-smoke", "slag", "silt-ash"],
            message=(
                "the crucible yields silt-ash The board pays 30 stones for "
                "murmuring-smoke (wx-t1-6)."
            ),
            claims=[{"task_id": "wx-t1-6", "tier": 1, "first": True}],
        ),
        _travel(6, "a3", 0, "spring"),
        _converse(7, "a1", 0, T7),
        _converse(8, "a2", 0, T8),
        _note(9, "a2", 0, T9),
        _converse(10, "a1", 0, T17, degraded=True),
        _llm(11, "a2", 0, "tick"),
        _llm(12, "a2", 0, "reflection"),
        _day(13, 1),
        _attempt(14, "a2", 1, [["fire", "wood"]], ["murmuring-smoke"]),
        _converse(15, "a2", 1, FALSE_CLAIM),
        _travel(16, "a3", 1, "meadow"),
        _converse(17, "a1", 1, T17),
        _day(18, 2),
        _llm(19, "a3", 2, "tick"),
        _attempt(20, "a3", 2, [["metal", "fire"], ["wood", "fire"]], ["slag", "murmuring-smoke"]),
        _converse(21, "a3", 2, FALSE_CLAIM),
        _day(22, 3),
        _converse(23, "a1", 3, T17),
        _llm(24, "a3", 3, "tick"),
        _llm(25, "a3", 3, "reflection"),
        _attempt(26, "a1", 3, [["fire", "metal"]], ["slag"]),
        _attempt(27, "a2", 3, [["murmuring-smoke", "water"]], ["silt-ash"]),
        _ev(28, EventKind.AGENT_DIED, actor="a2", payload={"cause": "qi"}, day=3),
        _day(29, 4),
        _converse(30, "a1", 4, FALSE_CLAIM),
        _attempt(31, "a3", 4, [["murmuring-smoke", "water"]], ["silt-ash"]),
        _ev(32, EventKind.RUN_FINISHED, payload={"days_elapsed": 5}, day=4),
    ]


@pytest.fixture(scope="module")
def synthetic() -> dict[str, Any]:
    return fold_exposure(synthetic_events(), FOLD_NAMES, spawn_location="meadow", prompts=PROMPTS)


@pytest.fixture(scope="module")
def synthetic_records() -> list[ExposureRecord]:
    return exposure_records(
        synthetic_events(), FOLD_NAMES, spawn_location="meadow", prompts=PROMPTS
    )


# ------------------------------------------------------------ R2: matcher


class TestMatcher:
    def test_base_inside_compound_does_not_match_the_base(self) -> None:
        assert match_names("The firebrand glows.", MATCH_NAMES) == ["firebrand"]
        assert match_names("A firebrand and a fire.", MATCH_NAMES) == ["firebrand", "fire"]

    def test_base_is_word_bounded_and_case_insensitive(self) -> None:
        assert match_names("Fire and WOOD make smoke.", MATCH_NAMES) == ["fire", "wood"]
        assert match_names("The fires of Woodland.", MATCH_NAMES) == []
        assert match_names("fire+wood, fire-wood, (water)", MATCH_NAMES) == [
            "fire",
            "wood",
            "water",
        ]

    def test_hyphenated_compound_matches_only_as_a_whole_token(self) -> None:
        for text in ("ember-ash.", "(ember-ash)", "ember-ash, then", "EMBER-ASH!"):
            assert match_names(text, MATCH_NAMES) == ["ember-ash"], text
        for text in ("ember-ashen", "pale-ember-ash", "ember-ash-x", "ember ash", "embers-ash"):
            assert match_names(text, MATCH_NAMES) == [], text

    def test_base_mention_inside_a_compound_span_is_dropped(self) -> None:
        names = RecipeNames(bases=tuple(BASES), compounds=("fire-ash",))
        assert match_names("fire-ash and fire", names) == ["fire-ash", "fire"]
        mentions = NameMatcher(names).mentions("fire-ash")
        assert [(m.name, m.kind) for m in mentions] == [("fire-ash", "compound")]

    def test_names_are_distinct_in_first_occurrence_order(self) -> None:
        assert match_names("wood and fire and wood", MATCH_NAMES) == ["wood", "fire"]

    def test_slag_is_a_mention_but_never_a_name(self) -> None:
        matcher = NameMatcher(MATCH_NAMES)
        assert [m.kind for m in matcher.mentions("fire and slag")] == ["base", "slag"]
        assert matcher.names("fire and slag") == ["fire"]
        assert matcher.bases == frozenset(BASES)


class TestSentenceSplit:
    def test_splits_on_every_delimiter_and_keeps_the_terminator(self) -> None:
        assert split_sentences("A. B! C? D; E: F\nG") == [
            "A.",
            "B!",
            "C?",
            "D;",
            "E:",
            "F",
            "G",
        ]

    def test_strips_and_drops_empties(self) -> None:
        assert split_sentences("  first.\n\n  second!  ") == ["first.", "second!"]
        assert split_sentences("") == []


# ------------------------------------------------------- R6: clause parser


class TestFindAssertions:
    matcher = NameMatcher(PARSE_NAMES)

    def parse(self, sentence: str) -> tuple[list[tuple[str, str, str]], bool]:
        return find_assertions(sentence, self.matcher)

    def test_pair_before_and_after_the_yield_verb(self) -> None:
        expected = ([("fire", "wood", "murmuring-smoke")], False)
        assert self.parse("fire and wood make murmuring-smoke.") == expected
        assert self.parse("I have made murmuring-smoke from fire and wood.") == expected
        assert self.parse("Made murmuring-smoke from fire+wood, earned 10 stones.") == expected
        assert self.parse("fire+wood=murmuring-smoke") == expected
        assert self.parse("fire+wood -> murmuring-smoke") == expected
        assert self.parse("fire-wood → murmuring-smoke") == expected

    def test_all_compound_recipe_orients_by_the_connected_pair(self) -> None:
        assert self.parse("verdant-root made from autumn-mirror and whispering-bone.") == (
            [("autumn-mirror", "whispering-bone", "verdant-root")],
            False,
        )

    def test_enumerations_zip_pairs_with_products_in_order(self) -> None:
        two = [("fire", "wood", "murmuring-smoke"), ("earth", "water", "burnished-iron")]
        assert self.parse(
            "fire+wood→murmuring-smoke (no residue), earth+water→burnished-iron."
        ) == (
            two,
            False,
        )
        assert self.parse(
            "Fire and wood make murmuring-smoke, and earth and water make burnished-iron."
        ) == (two, False)
        assert self.parse(
            "fire with earth yields only slag, wood with metal yields azure-coil."
        ) == (
            [("earth", "fire", "slag"), ("metal", "wood", "azure-coil")],
            False,
        )

    def test_a_single_slag_mention_covers_every_pair(self) -> None:
        assert self.parse("fire-earth and fire-water both yielded slag.") == (
            [("earth", "fire", "slag"), ("fire", "water", "slag")],
            False,
        )

    def test_questions_and_hedged_openers_are_excluded(self) -> None:
        assert self.parse("Does fire and wood make murmuring-smoke?") == ([], False)
        assert self.parse("Perhaps fire and wood make murmuring-smoke.") == ([], False)
        assert self.parse("Let's say fire and wood make murmuring-smoke.") == ([], False)
        assert self.parse("If fire and wood make murmuring-smoke, good.") == ([], False)

    def test_needs_a_yield_token_and_two_ingredients(self) -> None:
        assert self.parse("Fire and wood are friends with murmuring-smoke.") == ([], False)
        assert self.parse("wood and wood make nothing but slag.") == ([], False)
        assert self.parse("fire makes fire.") == ([], False)

    def test_ambiguous_sentences_assert_nothing(self) -> None:
        assert self.parse("I made murmuring-smoke, burnished-iron, silt-ash.") == ([], True)
        assert self.parse("fire with wood yields smoke.") == ([], True)
        assert self.parse("murmuring-smoke and water make silt-ash and azure-coil.") == ([], True)


# ----------------------------------------------------- R5: pure classifier


def _exp(
    seq: int, day: int, speaker: str, listener: str, target: str, *, naive: bool = True
) -> ExposureRecord:
    return ExposureRecord(
        utterance_seq=seq,
        day=day,
        speaker=speaker,
        listener=listener,
        a="fire",
        b="wood",
        target=target,
        tier=1,
        product_named=True,
        listener_naive=naive,
        listener_can_make=True,
    )


class TestClassifyAcquisitions:
    def test_priority_and_earliest_teacher(self) -> None:
        productions = [
            Production("a1", "x", 5, 0),
            Production("a2", "x", 20, 2),
            Production("a3", "x", 30, 3),
            Production("a4", "x", 40, 9),
        ]
        exposures = [
            _exp(12, 1, "a9", "a2", "x"),  # later than seq 10 below -> not the earliest
            _exp(10, 1, "a1", "a2", "x"),
            _exp(25, 2, "a1", "a3", "x", naive=False),  # not naive -> ignored
            _exp(35, 8, "a1", "a4", "x"),  # day 9 - 8 = 1 <= window: transmitted
        ]
        out = classify_acquisitions(productions, exposures, window_days=2, tiers={"x": 4})
        kinds = [(a.student, a.kind, a.teacher, a.utterance_seq, a.tier) for a in out]
        assert kinds == [
            ("a1", "first_in_world", None, None, 4),
            ("a2", "transmitted", "a1", 10, 4),
            ("a3", "independent", None, None, 4),
            ("a4", "transmitted", "a1", 35, 4),
        ]

    def test_window_boundary_is_inclusive(self) -> None:
        productions = [Production("a1", "x", 1, 0), Production("a2", "x", 50, 2)]
        inside = [_exp(3, 0, "a1", "a2", "x")]
        outside = [_exp(3, 0, "a1", "a2", "x")]
        assert classify_acquisitions(productions, inside, 2)[1].kind == "transmitted"
        assert classify_acquisitions(productions, outside, 1)[1].kind == "independent"
        assert classify_acquisitions(productions, [], 2)[1] == Acquisition(
            "a2", "x", 0, 50, 2, "independent", None, None, None
        )

    def test_exposure_must_precede_the_production(self) -> None:
        productions = [Production("a1", "x", 1, 0), Production("a2", "x", 5, 0)]
        later = [_exp(7, 0, "a1", "a2", "x")]
        assert classify_acquisitions(productions, later, 2)[1].kind == "independent"


# ------------------------------------------------------ R1: knowledge fold


class TestKnowledgeFold:
    def test_journal_satchel_and_halted_attempts(self) -> None:
        fold = KnowledgeFold("meadow")
        fold.apply(_spawn(1, "a1", "A"))
        steps = fold.apply(
            _attempt(2, "a1", 0, [["fire", "wood"], ["wood", "fire"], ["x", "y"]], ["ms", "ms"])
        )
        assert [(s.pair, s.product, s.pair_new, s.product_new) for s in steps] == [
            (("fire", "wood"), "ms", True, True),
            (("fire", "wood"), "ms", False, False),
        ]
        assert fold.journal("a1") == [("fire", "wood", "ms")]
        assert fold.satchel("a1") == ["ms"]
        fold.apply(_attempt(3, "a1", 0, [["earth", "fire"]], ["slag"]))
        assert fold.journal("a1") == [("fire", "wood", "ms"), ("earth", "fire", "slag")]
        assert fold.satchel("a1") == ["ms"]  # slag never joins the satchel
        assert fold.journal_product("a1", ("earth", "fire")) == "slag"
        assert fold.journal_product("a1", ("metal", "water")) is None

    def test_malformed_step_stops_the_attempt(self) -> None:
        fold = KnowledgeFold()
        fold.apply(_spawn(1, "a1", "A"))
        steps = fold.apply(_attempt(2, "a1", 0, [["fire"], ["fire", "wood"]], ["slag", "ms"]))
        assert steps == []
        assert fold.apply(_ev(3, EventKind.TASK_ATTEMPT, actor="a1", payload={})) == []

    def test_listeners_follow_location_liveness_and_degraded_travel(self) -> None:
        fold = KnowledgeFold("meadow")
        for seq, agent in enumerate(("a1", "a2", "a3"), start=1):
            fold.apply(_spawn(seq, agent, agent.upper()))
        assert fold.listeners("a1") == ["a2", "a3"]
        fold.apply(_travel(4, "a3", 0, "spring"))
        assert fold.listeners("a1") == ["a2"]
        fold.apply(_travel(5, "a2", 0, "spring", degraded=True))  # no world effect
        assert fold.listeners("a1") == ["a2"]
        fold.apply(_ev(6, EventKind.AGENT_DIED, actor="a2", day=0))
        assert fold.listeners("a1") == []
        fold.apply(_travel(7, "a3", 0, "meadow"))
        assert fold.listeners("a1") == ["a3"]
        assert fold.location("a3") == "meadow"
        assert fold.alive("a2") is False and fold.alive("a1") is True
        assert fold.spawn_order == ["a1", "a2", "a3"]


# ------------------------------------------------------------- names


class TestNames:
    def test_names_from_config_rebuilds_the_universe(self) -> None:
        names = names_from_config(load_live_config(VALLEY_TOML))
        assert names.bases == BASES
        assert len(names.compounds) == 34
        assert names.compounds == tuple(sorted(names.compounds))
        assert all("-" in c for c in names.compounds)
        assert sorted(names.tiers.values()).count(1) == 8
        assert {names.tiers[c] for c in names.compounds} == {1, 2, 3, 4, 5}
        assert names.tier_of("nothing-here") == 0

    def test_names_from_config_rejects_unknown_universe(self) -> None:
        cfg = load_live_config(VALLEY_TOML)
        cfg = cfg.model_copy(update={"universe": cfg.universe.model_copy(update={"name": "other"})})
        with pytest.raises(ValueError, match="other"):
            names_from_config(cfg)

    def test_names_from_events_uses_products_and_board_lines(self) -> None:
        names = names_from_events(synthetic_events())
        assert names.bases == BASES
        assert names.compounds == ("murmuring-smoke", "silt-ash")
        assert dict(names.tiers) == {"murmuring-smoke": 1}  # silt-ash was never claimed

    def test_log_tiers_fill_in_when_the_name_list_has_none(self) -> None:
        bare = RecipeNames(bases=FOLD_NAMES.bases, compounds=FOLD_NAMES.compounds)
        records = exposure_records(synthetic_events(), bare, spawn_location="meadow")
        assert {(r.target, r.tier) for r in records} == {("murmuring-smoke", 1), ("silt-ash", 0)}


# ------------------------------------------------ the synthetic scenario


class TestSyntheticScenario:
    """Expected numbers are computed by hand from ``synthetic_events``."""

    def test_header_and_utterances(self, synthetic: dict[str, Any]) -> None:
        assert synthetic["run_id"] == "synthetic"
        assert synthetic["window_days"] == 2
        assert synthetic["pair_rule"] == "connected"
        assert synthetic["utterances"] == {
            "total": 7,  # seqs 7, 8, 15, 17, 21, 23, 30 (seq 10 is degraded)
            "naming_any_compound": 7,
            "naming_known_pair": 3,  # 7, 17, 23
            "naming_known_pair_and_product": 3,
            "naming_connected_pair": 3,  # every pair here is connector-joined
            "naming_connected_pair_and_product": 3,
            "distinct_recipes_spoken": 2,
            "distinct_known_pairs_spoken": 2,
        }

    def test_both_pair_rules_agree_on_connector_phrased_speech(
        self, synthetic: dict[str, Any]
    ) -> None:
        # Every pair in the synthetic log is spoken as "a and b" / "a with
        # b", so the sentence rule adds nothing: the whole document is
        # identical apart from the rule label.
        sentence = fold_exposure(
            synthetic_events(),
            FOLD_NAMES,
            spawn_location="meadow",
            pair_rule="sentence",
            prompts=PROMPTS,
        )
        assert sentence["pair_rule"] == "sentence"
        assert {k: v for k, v in sentence.items() if k != "pair_rule"} == {
            k: v for k, v in synthetic.items() if k != "pair_rule"
        }

    def test_exposure_records(self, synthetic_records: list[ExposureRecord]) -> None:
        rows = [
            (
                r.utterance_seq,
                r.listener,
                r.a,
                r.b,
                r.target,
                r.tier,
                r.product_named,
                r.listener_naive,
                r.listener_can_make,
                r.uptake_seq,
                r.uptake_delay_days,
                r.produced_target,
                r.rendered,
            )
            for r in synthetic_records
        ]
        assert rows == [
            (7, "a2", "fire", "wood", "murmuring-smoke", 1, True, True, True, 14, 1, True, True),
            (
                7,
                "a2",
                "murmuring-smoke",
                "water",
                "silt-ash",
                2,
                True,
                True,
                False,
                None,
                None,
                None,
                True,
            ),
            (
                17,
                "a2",
                "fire",
                "wood",
                "murmuring-smoke",
                1,
                True,
                False,
                True,
                None,
                None,
                None,
                False,
            ),
            (17, "a3", "fire", "wood", "murmuring-smoke", 1, True, True, True, 20, 1, True, True),
            (
                23,
                "a2",
                "fire",
                "wood",
                "murmuring-smoke",
                1,
                True,
                False,
                True,
                None,
                None,
                None,
                False,
            ),
            (
                23,
                "a3",
                "fire",
                "wood",
                "murmuring-smoke",
                1,
                True,
                False,
                True,
                None,
                None,
                None,
                False,
            ),
        ]
        assert all(r.speaker == "a1" and r.day in (0, 1, 3) for r in synthetic_records)

    def test_exposure_summary(self, synthetic: dict[str, Any]) -> None:
        assert synthetic["exposures"] == {
            "total": 6,
            "naive": 3,
            "rendered_permille": 500,  # 3 of 6
            "by_tier": {
                "1": {"naive": 2, "uptake": 2, "uptake_permille": 1000},
                "2": {"naive": 1, "uptake": 0, "uptake_permille": 0},
            },
        }

    def test_control_rate(self, synthetic: dict[str, Any]) -> None:
        # candidates: d0 30, d1 38, d2 42, d3 45, d4 30 = 185; minus 6 exposed
        # agent-day pairs = 179; first-tried within 2 days: a1 5, a2 1, a3 5.
        assert synthetic["control"] == {"pairs": 179, "uptake": 11, "uptake_permille": 61}

    def test_acquisitions(self, synthetic: dict[str, Any]) -> None:
        assert synthetic["acquisitions"]["by_tier"] == {
            "1": {"first_in_world": 1, "transmitted": 2, "independent": 0},
            "2": {"first_in_world": 1, "transmitted": 0, "independent": 2},
        }
        assert synthetic["acquisitions"]["transmitted"] == [
            {
                "student": "a2",
                "teacher": "a1",
                "target": "murmuring-smoke",
                "tier": 1,
                "utterance_seq": 7,
                "utterance_day": 0,
                "production_seq": 14,
                "production_day": 1,
            },
            {
                "student": "a3",
                "teacher": "a1",
                "target": "murmuring-smoke",
                "tier": 1,
                "utterance_seq": 17,
                "utterance_day": 1,
                "production_seq": 20,
                "production_day": 2,
            },
        ]

    def test_assertions(self, synthetic: dict[str, Any]) -> None:
        assertions = synthetic["assertions"]
        assert assertions["converse"] == {
            "total": 9,
            "correct": 5,
            "false": 4,
            "unknown": 0,
            "ambiguous": 0,
        }
        assert assertions["note"] == {
            "total": 2,
            "correct": 1,
            "false": 0,
            "unknown": 1,
            "ambiguous": 0,
        }
        # original false claim: a2 at seq 8 (day 0); a2 repeats it itself (no),
        # a3 repeats on day 2 (yes), a1 repeats on day 4 (> 3 days: no).
        assert assertions["false_replicated"] == 1
        assert assertions["per_agent"] == {
            "a1": {"asserted": 6, "false": 1},
            "a2": {"asserted": 4, "false": 2},
            "a3": {"asserted": 1, "false": 1},
        }
        assert [(e["seq"], e["agent"], e["channel"]) for e in assertions["examples_false"]] == [
            (8, "a2", "converse"),
            (15, "a2", "converse"),
            (21, "a3", "converse"),
            (30, "a1", "converse"),
        ]
        assert assertions["examples_false"][0] == {
            "seq": 8,
            "agent": "a2",
            "channel": "converse",
            "a": "fire",
            "b": "metal",
            "asserted": "jade-salt",
            "truth": "slag",
        }

    def test_per_agent_and_examples(self, synthetic: dict[str, Any]) -> None:
        assert synthetic["per_agent"] == {
            "a1": {
                "utterances": 4,
                "exposures_given": 6,
                "exposures_received": 0,
                "uptakes_caused": 2,
                "uptakes_taken": 0,
            },
            "a2": {
                "utterances": 2,
                "exposures_given": 0,
                "exposures_received": 4,
                "uptakes_caused": 0,
                "uptakes_taken": 1,
            },
            "a3": {
                "utterances": 1,
                "exposures_given": 0,
                "exposures_received": 2,
                "uptakes_caused": 0,
                "uptakes_taken": 1,
            },
        }
        assert synthetic["examples"]["top_recipe_utterances"] == [
            {"seq": 7, "day": 0, "speaker": "a1", "recipes": 2},
            {"seq": 17, "day": 1, "speaker": "a1", "recipes": 1},
            {"seq": 23, "day": 3, "speaker": "a1", "recipes": 1},
            {"seq": 8, "day": 0, "speaker": "a2", "recipes": 0},
            {"seq": 15, "day": 1, "speaker": "a2", "recipes": 0},
        ]

    def test_rendered_is_null_without_prompts(self) -> None:
        data = fold_exposure(synthetic_events(), FOLD_NAMES, spawn_location="meadow")
        assert data["exposures"]["rendered_permille"] is None
        assert data["exposures"]["total"] == 6
        records = exposure_records(synthetic_events(), FOLD_NAMES, spawn_location="meadow")
        assert all(r.rendered is None for r in records)

    def test_window_days_changes_uptake_and_classes(self) -> None:
        wide = fold_exposure(synthetic_events(), FOLD_NAMES, spawn_location="meadow", window_days=3)
        # a2's silt-ash (day 3) now falls inside the window of the day-0 exposure.
        assert wide["exposures"]["by_tier"]["2"] == {
            "naive": 1,
            "uptake": 1,
            "uptake_permille": 1000,
        }
        assert wide["acquisitions"]["by_tier"]["2"] == {
            "first_in_world": 1,
            "transmitted": 1,
            "independent": 1,
        }
        assert wide["window_days"] == 3

    def test_spawn_location_matters_for_return_trips(self) -> None:
        # With the default "" spawn location, a3's travel back to "meadow"
        # never rejoins a1/a2: utterances 17 and 23 lose their a3 listeners.
        records = exposure_records(synthetic_events(), FOLD_NAMES)
        assert [r.listener for r in records] == ["a2", "a2", "a2", "a2"]


class TestRendering:
    """R7 in isolation: seq, purpose and the same-or-next-day window."""

    @staticmethod
    def _events() -> list[EventRecord]:
        return [
            _spawn(1, "a1", "A"),
            _spawn(2, "a2", "B"),
            _day(3, 0),
            _attempt(4, "a1", 0, [["fire", "wood"]], ["murmuring-smoke"]),
            _converse(5, "a1", 0, T17),
            _llm(6, "a2", 0, "tick"),
        ]

    def _rendered(self, prompt_day: int, purpose: str, seq: int = 6) -> bool | None:
        events = self._events()
        events[-1] = _llm(seq, "a2", prompt_day, purpose)
        prompts = {seq: _envelope("HEARD\nA: " + T17)}
        records = exposure_records(events, FOLD_NAMES, prompts=prompts)
        assert len(records) == 1
        return records[0].rendered

    def test_same_or_next_day_tick_prompt_counts(self) -> None:
        assert self._rendered(0, "tick") is True
        assert self._rendered(1, "tick") is True

    def test_later_days_reflections_and_earlier_seqs_do_not(self) -> None:
        assert self._rendered(2, "tick") is False
        assert self._rendered(0, "reflection") is False
        assert self._rendered(0, "tick", seq=4) is False  # before the utterance

    def test_prefix_is_eighty_chars(self) -> None:
        long_text = "Fire and wood make murmuring-smoke, " * 5  # 180 chars
        events = self._events()
        events[4] = _converse(5, "a1", 0, long_text)
        prompts = {6: _envelope("HEARD\nA: " + long_text[:80] + "...truncated")}
        assert exposure_records(events, FOLD_NAMES, prompts=prompts)[0].rendered is True
        prompts = {6: _envelope("HEARD\nA: " + long_text[:79])}
        assert exposure_records(events, FOLD_NAMES, prompts=prompts)[0].rendered is False


# ------------------------------------------------ adversarial edge cases


class TestAdversarial:
    """Reviewer probes: the speaker-side journal gate, delivery at emission,
    notes vs converse, halted/empty attempts and sentence-level pairing."""

    def test_pair_outside_the_speakers_journal_is_never_an_exposure(self) -> None:
        # a2 holds the recipe; a1 names it before ever trying it (seq 6),
        # then names a pair it tried and got slag from (seq 8), and only
        # after producing it (seq 9) does a1's utterance expose anyone.
        events = [
            _ev(0, EventKind.RUN_STARTED, payload={"run_id": "adv-a"}),
            _spawn(1, "a1", "A"),
            _spawn(2, "a2", "B"),
            _spawn(3, "a3", "C"),
            _day(4, 0),
            _attempt(5, "a2", 0, [["fire", "wood"]], ["murmuring-smoke"]),
            _converse(6, "a1", 0, "Fire and wood make murmuring-smoke."),
            _attempt(7, "a1", 0, [["earth", "fire"]], ["slag"]),
            _converse(8, "a1", 0, "Earth and fire make murmuring-smoke."),
            _attempt(9, "a1", 0, [["fire", "wood"]], ["murmuring-smoke"]),
            _converse(10, "a1", 0, "Fire and wood. Fire and wood make murmuring-smoke."),
        ]
        data = fold_exposure(events, FOLD_NAMES, spawn_location="meadow")
        records = exposure_records(events, FOLD_NAMES, spawn_location="meadow")
        assert data["utterances"] == {
            "total": 3,
            "naming_any_compound": 3,
            "naming_known_pair": 1,  # seq 10 only
            "naming_known_pair_and_product": 1,
            "naming_connected_pair": 1,
            "naming_connected_pair_and_product": 1,
            "distinct_recipes_spoken": 1,
            "distinct_known_pairs_spoken": 1,
        }
        # one recipe (deduped across the two sentences, product named in the
        # second) x two listeners; a2 already holds the pair, a3 is naive.
        assert [
            (r.utterance_seq, r.listener, r.product_named, r.listener_naive) for r in records
        ] == [
            (10, "a2", True, False),
            (10, "a3", True, True),
        ]
        assert data["exposures"] == {
            "total": 2,
            "naive": 1,
            "rendered_permille": None,
            "by_tier": {"1": {"naive": 1, "uptake": 0, "uptake_permille": 0}},
        }
        # a2 was first in the world; a1 heard nothing before producing.
        assert data["acquisitions"] == {
            "by_tier": {"1": {"first_in_world": 1, "transmitted": 0, "independent": 1}},
            "transmitted": [],
        }
        # R6 does not need the speaker's journal: seq 6 is correct (the map
        # holds fire+wood -> murmuring-smoke via a2), seq 8 is false (slag),
        # seq 10's first sentence has no yield verb.
        assert data["assertions"]["converse"] == {
            "total": 3,
            "correct": 2,
            "false": 1,
            "unknown": 0,
            "ambiguous": 0,
        }
        assert data["assertions"]["examples_false"] == [
            {
                "seq": 8,
                "agent": "a1",
                "channel": "converse",
                "a": "earth",
                "b": "fire",
                "asserted": "murmuring-smoke",
                "truth": "slag",
            }
        ]
        assert data["per_agent"]["a1"] == {
            "utterances": 3,
            "exposures_given": 2,
            "exposures_received": 0,
            "uptakes_caused": 0,
            "uptakes_taken": 0,
        }
        assert [u["seq"] for u in data["examples"]["top_recipe_utterances"]] == [10, 6, 8]

    def test_delivery_is_at_emission_and_notes_never_expose(self) -> None:
        # a3 is away and a4 is dead when seq 10 is spoken; a3 returning at
        # seq 11 never hears it. A degraded travel (seq 12) moves nobody.
        # The note at seq 9 names the recipe but is not an exposure.
        text = "Fire and wood make murmuring-smoke."
        events = [
            _ev(0, EventKind.RUN_STARTED, payload={"run_id": "adv-b"}),
            _spawn(1, "a1", "A"),
            _spawn(2, "a2", "B"),
            _spawn(3, "a3", "C"),
            _spawn(4, "a4", "D"),
            _day(5, 0),
            _attempt(6, "a1", 0, [["fire", "wood"]], ["murmuring-smoke"]),
            _travel(7, "a3", 0, "spring"),
            _ev(8, EventKind.AGENT_DIED, actor="a4", payload={"cause": "qi"}, day=0),
            _note(9, "a1", 0, "fire+wood -> murmuring-smoke"),
            _converse(10, "a1", 0, text),
            _travel(11, "a3", 0, "meadow"),
            _travel(12, "a2", 0, "spring", degraded=True),
            _converse(13, "a1", 0, text),
        ]
        data = fold_exposure(events, FOLD_NAMES, spawn_location="meadow")
        records = exposure_records(events, FOLD_NAMES, spawn_location="meadow")
        assert [(r.utterance_seq, r.listener) for r in records] == [
            (10, "a2"),
            (13, "a2"),
            (13, "a3"),
        ]
        assert all(r.listener_naive and r.uptake_seq is None for r in records)
        assert data["utterances"]["total"] == 2  # the note is not an utterance
        assert data["exposures"]["total"] == 3
        assert data["assertions"]["note"] == {
            "total": 1,
            "correct": 1,
            "false": 0,
            "unknown": 0,
            "ambiguous": 0,
        }
        assert data["assertions"]["converse"]["total"] == 2
        received = {agent: row["exposures_received"] for agent, row in data["per_agent"].items()}
        assert received == {"a1": 0, "a2": 2, "a3": 1, "a4": 0}
        assert data["per_agent"]["a1"]["exposures_given"] == 3

    def test_halted_and_empty_attempts_and_split_sentences_expose_nothing(self) -> None:
        # seq 4 halts before its first step (no products), seq 6 has no
        # steps at all: neither touches the journal, so seq 5 is not an
        # exposure. After the real production (seq 7), seq 8 names both
        # ingredients but never in one sentence -> still no exposure.
        events = [
            _ev(0, EventKind.RUN_STARTED, payload={"run_id": "adv-c"}),
            _spawn(1, "a1", "A"),
            _spawn(2, "a2", "B"),
            _day(3, 0),
            _attempt(4, "a1", 0, [["azure-coil", "fire"], ["fire", "wood"]], []),
            _converse(5, "a1", 0, "Fire and wood make murmuring-smoke."),
            _attempt(6, "a1", 0, [], []),
            _attempt(7, "a1", 0, [["fire", "wood"]], ["murmuring-smoke"]),
            _converse(8, "a1", 0, "Fire is hot. Wood burns bright. I hold murmuring-smoke."),
            _day(9, 1),
        ]
        data = fold_exposure(events, FOLD_NAMES, spawn_location="meadow")
        assert exposure_records(events, FOLD_NAMES, spawn_location="meadow") == []
        assert data["utterances"] == {
            "total": 2,
            "naming_any_compound": 2,
            "naming_known_pair": 0,
            "naming_known_pair_and_product": 0,
            "naming_connected_pair": 0,
            "naming_connected_pair_and_product": 0,
            "distinct_recipes_spoken": 0,
            "distinct_known_pairs_spoken": 0,
        }
        assert data["exposures"]["by_tier"] == {}
        assert data["acquisitions"]["by_tier"] == {
            "1": {"first_in_world": 1, "transmitted": 0, "independent": 0}
        }
        # control: day 0 = 10 + 10 base pairs (nothing tried yet); day 1 =
        # a1 (bases + murmuring-smoke = 15 pairs, minus the one tried) 14 +
        # a2 10; the day-0 (a1, fire+wood) candidate is first-tried on day 0.
        assert data["control"] == {"pairs": 44, "uptake": 1, "uptake_permille": 22}
        assert data["assertions"]["converse"]["total"] == 1
        assert data["assertions"]["converse"]["correct"] == 1


# ------------------------------------------------------- R3: the pair rule


def _pair_rule_events(text: str) -> list[EventRecord]:
    """a1 holds every pair over fire/wood/earth/water (six recipes) plus
    fire+metal, then says *text* to a2, who is naive for all of them."""
    steps = [
        ["fire", "wood"],
        ["earth", "water"],
        ["earth", "fire"],
        ["fire", "water"],
        ["earth", "wood"],
        ["water", "wood"],
        ["fire", "metal"],
    ]
    products = [
        "murmuring-smoke",
        "silt-ash",
        "jade-salt",
        "azure-coil",
        "burnished-iron",
        "autumn-mirror",
        "whispering-bone",
    ]
    return [
        _ev(0, EventKind.RUN_STARTED, payload={"run_id": "pair-rule"}),
        _spawn(1, "a1", "A"),
        _spawn(2, "a2", "B"),
        _day(3, 0),
        _attempt(4, "a1", 0, steps, products),
        _converse(5, "a1", 0, text),
    ]


def _spoken(text: str, pair_rule: PairRule) -> list[tuple[str, str, str, bool]]:
    """``(a, b, target, product_named)`` per exposure record, sorted."""
    records = exposure_records(_pair_rule_events(text), PARSE_NAMES, pair_rule=pair_rule)
    assert all(r.speaker == "a1" and r.listener == "a2" and r.listener_naive for r in records)
    return sorted((r.a, r.b, r.target, r.product_named) for r in records)


class TestPairRule:
    ENUMERATION = "Fire and wood made murmuring-smoke, and earth and water made silt-ash."

    def test_enumeration_names_only_the_joined_pairs_under_connected(self) -> None:
        assert _spoken(self.ENUMERATION, "connected") == [
            ("earth", "water", "silt-ash", True),
            ("fire", "wood", "murmuring-smoke", True),
        ]

    def test_enumeration_names_every_held_pair_under_sentence(self) -> None:
        # All six pairs over the four bases are in a1's journal; only the two
        # actually spoken have their product named in the sentence.
        assert _spoken(self.ENUMERATION, "sentence") == [
            ("earth", "fire", "jade-salt", False),
            ("earth", "water", "silt-ash", True),
            ("earth", "wood", "burnished-iron", False),
            ("fire", "water", "azure-coil", False),
            ("fire", "wood", "murmuring-smoke", True),
            ("water", "wood", "autumn-mirror", False),
        ]

    def test_summary_reports_both_rules_and_follows_the_selected_one(self) -> None:
        events = _pair_rule_events(self.ENUMERATION)
        connected = fold_exposure(events, PARSE_NAMES, pair_rule="connected")
        sentence = fold_exposure(events, PARSE_NAMES, pair_rule="sentence")
        assert (connected["pair_rule"], sentence["pair_rule"]) == ("connected", "sentence")
        # The four naming_* counts describe the text, not the selection.
        for data in (connected, sentence):
            u = data["utterances"]
            assert (u["total"], u["naming_any_compound"]) == (1, 1)
            assert (u["naming_known_pair"], u["naming_known_pair_and_product"]) == (1, 1)
            assert (u["naming_connected_pair"], u["naming_connected_pair_and_product"]) == (1, 1)
            assert data["assertions"]["converse"]["total"] == 2  # R6 is rule-independent
        assert connected["utterances"]["distinct_known_pairs_spoken"] == 2
        assert sentence["utterances"]["distinct_known_pairs_spoken"] == 6
        assert connected["utterances"]["distinct_recipes_spoken"] == 2
        assert sentence["utterances"]["distinct_recipes_spoken"] == 2
        assert (connected["exposures"]["total"], connected["exposures"]["naive"]) == (2, 2)
        assert (sentence["exposures"]["total"], sentence["exposures"]["naive"]) == (6, 6)
        assert connected["examples"]["top_recipe_utterances"][0]["recipes"] == 2
        assert sentence["examples"]["top_recipe_utterances"][0]["recipes"] == 6
        assert connected["per_agent"]["a2"]["exposures_received"] == 2
        assert sentence["per_agent"]["a2"]["exposures_received"] == 6

    def test_downstream_uptake_control_and_acquisitions_follow_the_rule(self) -> None:
        # a2 later tries earth+fire (named in the enumeration sentence but
        # not joined) and produces jade-salt: an uptake and a transmitted
        # acquisition under "sentence", neither under "connected".
        events = [
            *_pair_rule_events(self.ENUMERATION),
            _day(6, 1),
            _attempt(7, "a2", 1, [["earth", "fire"]], ["jade-salt"]),
        ]
        connected = fold_exposure(events, PARSE_NAMES, pair_rule="connected")
        sentence = fold_exposure(events, PARSE_NAMES, pair_rule="sentence")
        assert connected["exposures"]["by_tier"] == {
            "0": {"naive": 2, "uptake": 0, "uptake_permille": 0}
        }
        assert sentence["exposures"]["by_tier"] == {
            "0": {"naive": 6, "uptake": 1, "uptake_permille": 166}
        }
        assert connected["acquisitions"]["by_tier"]["0"]["transmitted"] == 0
        assert connected["acquisitions"]["by_tier"]["0"]["independent"] == 1
        assert sentence["acquisitions"]["by_tier"]["0"]["transmitted"] == 1
        assert sentence["acquisitions"]["by_tier"]["0"]["independent"] == 0
        assert [t["student"] for t in sentence["acquisitions"]["transmitted"]] == ["a2"]
        # Control candidates: day 0 = a1 10 + a2 10 (bases only); day 1 = a1
        # C(12,2) - 7 tried = 59 + a2 10 = 89. Exposed pairs are excluded on
        # both of a2's days: 2 x 2 under "connected", 6 x 2 under "sentence".
        # Uptakes: a1's seven day-0 base pairs first-tried that day, plus
        # (a2, earth+fire) on days 0 and 1 -- a control uptake only under
        # "connected", where it was never an exposure.
        assert connected["control"] == {"pairs": 85, "uptake": 9, "uptake_permille": 105}
        assert sentence["control"] == {"pairs": 77, "uptake": 7, "uptake_permille": 90}
        assert connected["per_agent"]["a1"]["uptakes_caused"] == 0
        assert sentence["per_agent"]["a1"]["uptakes_caused"] == 1

    def test_hyphen_plus_and_with_all_connect(self) -> None:
        expected = [("fire", "metal", "whispering-bone", True)]
        for text in (
            "fire-metal gave whispering-bone.",
            "FIRE-Metal gave whispering-bone!",
            "fire + metal gave whispering-bone.",
            "fire and metal gave whispering-bone.",
            "fire with metal gave whispering-bone.",
            "whispering-bone came from fire/metal.",
        ):
            assert _spoken(text, "connected") == expected, text
            assert _spoken(text, "sentence") == expected, text

    def test_mentions_split_by_a_non_connector_are_not_a_pair(self) -> None:
        text = "I tried fire, then later earth."
        assert _spoken(text, "connected") == []
        assert _spoken(text, "sentence") == [("earth", "fire", "jade-salt", False)]
        data = fold_exposure(_pair_rule_events(text), PARSE_NAMES)
        u = data["utterances"]
        assert (u["naming_known_pair"], u["naming_connected_pair"]) == (1, 0)
        assert (u["naming_known_pair_and_product"], u["naming_connected_pair_and_product"]) == (
            0,
            0,
        )
        assert data["exposures"]["total"] == 0

    def test_product_named_is_still_sentence_level_under_connected(self) -> None:
        # The product need not be adjacent to the pair: anywhere in the
        # sentence counts; a product in the NEXT sentence does not.
        assert _spoken("murmuring-smoke is what fire and wood gave me.", "connected") == [
            ("fire", "wood", "murmuring-smoke", True)
        ]
        assert _spoken("Try fire and wood. It gives murmuring-smoke.", "connected") == [
            ("fire", "wood", "murmuring-smoke", False)
        ]

    def test_default_is_connected(self) -> None:
        assert DEFAULT_PAIR_RULE == "connected"
        events = _pair_rule_events(self.ENUMERATION)
        default = fold_exposure(events, PARSE_NAMES)
        assert default["pair_rule"] == "connected"
        assert default == fold_exposure(events, PARSE_NAMES, pair_rule="connected")
        assert default != fold_exposure(events, PARSE_NAMES, pair_rule="sentence")
        assert len(exposure_records(events, PARSE_NAMES)) == 2

    def test_unknown_rule_is_rejected(self) -> None:
        events = _pair_rule_events(self.ENUMERATION)
        with pytest.raises(ValueError, match="pair_rule"):
            fold_exposure(events, PARSE_NAMES, pair_rule=cast(PairRule, "bogus"))
        with pytest.raises(ValueError, match="pair_rule"):
            exposure_records(events, PARSE_NAMES, pair_rule=cast(PairRule, ""))


def _assert_rule_invariants(connected: dict[str, Any], sentence: dict[str, Any]) -> None:
    """Facts that hold between the two rules on ANY log: a connected pair
    is two names in one sentence, so its records are a subset of the
    sentence rule's; the naming_* counts and R6 do not depend on the rule."""
    assert (connected["pair_rule"], sentence["pair_rule"]) == ("connected", "sentence")
    assert connected["run_id"] == sentence["run_id"]
    assert connected["window_days"] == sentence["window_days"]
    for key in (
        "total",
        "naming_any_compound",
        "naming_known_pair",
        "naming_known_pair_and_product",
        "naming_connected_pair",
        "naming_connected_pair_and_product",
    ):
        assert connected["utterances"][key] == sentence["utterances"][key], key
    u = connected["utterances"]
    assert u["naming_known_pair"] >= u["naming_connected_pair"] >= 0
    assert u["naming_known_pair_and_product"] >= u["naming_connected_pair_and_product"] >= 0
    assert u["naming_connected_pair"] >= u["naming_connected_pair_and_product"]
    for key in ("distinct_recipes_spoken", "distinct_known_pairs_spoken"):
        assert connected["utterances"][key] <= sentence["utterances"][key], key
    assert connected["exposures"]["total"] <= sentence["exposures"]["total"]
    assert connected["exposures"]["naive"] <= sentence["exposures"]["naive"]
    assert connected["control"]["pairs"] >= sentence["control"]["pairs"]
    assert connected["assertions"] == sentence["assertions"]
    for agent, row in connected["per_agent"].items():
        assert row["utterances"] == sentence["per_agent"][agent]["utterances"]
        assert row["exposures_given"] <= sentence["per_agent"][agent]["exposures_given"]
        assert row["exposures_received"] <= sentence["per_agent"][agent]["exposures_received"]


# --------------------------------------------------- (b) scripted live run


@pytest.fixture(scope="module")
def scripted_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[EventRecord]]:
    """A small scripted live run (same recipe as tests/test_report.py)."""
    cfg = load_live_config(VALLEY_TOML)
    cfg = cfg.model_copy(
        update={
            "world": cfg.world.model_copy(update={"days": 2, "rounds_per_day": 4}),
            "model": cfg.model.model_copy(update={"backend": "scripted"}),
            "population": cfg.population.model_copy(update={"founders": 3}),
        }
    )
    run_dir = tmp_path_factory.mktemp("exposure") / "run"
    run_live(
        cfg,
        run_dir,
        config_path=VALLEY_TOML,
        backend=ScriptedBackend(make_scripted()),
        difftest_interval=0,
    )
    with EventStore(run_dir / "events.sqlite3") as store:
        events = list(store.scan())
    return run_dir, events


def _check_json_values(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str)
            _check_json_values(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _check_json_values(item, f"{path}[{i}]")
    elif value is None:
        assert path.endswith("exposures.rendered_permille"), f"unexpected null at {path}"
    else:
        assert not isinstance(value, float), f"float at {path}: {value!r}"
        assert isinstance(value, (str, int, bool)), f"non-int/str at {path}: {value!r}"


class TestScriptedRun:
    def test_two_writes_are_byte_identical_and_well_formed(
        self, scripted_run: tuple[Path, list[EventRecord]], tmp_path: Path
    ) -> None:
        run_dir, _ = scripted_run
        first = write_exposure(run_dir, out_path=tmp_path / "one.json")
        second = write_exposure(run_dir, out_path=tmp_path / "two.json")
        assert first.read_bytes() == second.read_bytes()
        data = json.loads(first.read_text(encoding="utf-8"))
        assert set(data) == TOP_LEVEL_KEYS
        _check_json_values(data, "exposure")
        assert data["run_id"] != ""
        assert data["utterances"]["total"] > 0
        assert isinstance(data["exposures"]["rendered_permille"], int)  # llm_texts present
        assert data["assertions"]["note"]["total"] > 0  # 'learned: a+b -> x' notes
        assert set(data["per_agent"]) == {"a1", "a2", "a3"}
        # config.toml is present in the run dir, so the name list comes from
        # the rebuilt universe: the fold has the wuxing spawn location too.
        assert (run_dir / "config.toml").exists()

    def test_default_output_path_is_run_dir(
        self, scripted_run: tuple[Path, list[EventRecord]]
    ) -> None:
        run_dir, _ = scripted_run
        path = write_exposure(run_dir)
        assert path == run_dir / "exposure.json"
        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8"))["pair_rule"] == "connected"

    def test_pair_rule_selects_the_records_and_keeps_both_counts(
        self, scripted_run: tuple[Path, list[EventRecord]], tmp_path: Path
    ) -> None:
        run_dir, _ = scripted_run
        connected = json.loads(
            write_exposure(run_dir, out_path=tmp_path / "c.json").read_text(encoding="utf-8")
        )
        sentence = json.loads(
            write_exposure(run_dir, out_path=tmp_path / "s.json", pair_rule="sentence").read_text(
                encoding="utf-8"
            )
        )
        assert set(sentence) == TOP_LEVEL_KEYS
        _check_json_values(sentence, "exposure")
        _assert_rule_invariants(connected, sentence)

    def test_knowledge_fold_matches_world_state_fold(
        self, scripted_run: tuple[Path, list[EventRecord]]
    ) -> None:
        run_dir, events = scripted_run
        cfg = load_live_config(run_dir / "config.toml")
        world = WorldStateFold(cfg)
        mine = KnowledgeFold(cfg.live.locations[0])
        for ev in events:
            world.apply(ev)
            mine.apply(ev)
            for agent in mine.spawn_order:
                assert mine.journal(agent) == world.journal(agent), (ev.seq, agent)
                assert mine.satchel(agent) == world.satchel(agent), (ev.seq, agent)
                assert mine.location(agent) == world.location(agent), (ev.seq, agent)
        names = {
            ev.payload["agent_id"]: ev.payload["name"]
            for ev in events
            if ev.kind is EventKind.AGENT_SPAWNED
        }
        for agent in mine.spawn_order:
            assert [names[x] for x in mine.listeners(agent)] == world.co_present(agent)
        assert any(mine.journal(agent) for agent in mine.spawn_order)


# ------------------------------------------------ (c) the accepted real run


@pytest.mark.skipif(
    not (ACCEPTED_RUN / "events.sqlite3").exists(),
    reason="accepted Phase-1 run not present locally",
)
class TestAcceptedRun:
    def test_structural_facts(self, tmp_path: Path) -> None:
        path = write_exposure(ACCEPTED_RUN, out_path=tmp_path / "exposure.json")
        again = write_exposure(ACCEPTED_RUN, out_path=tmp_path / "again.json")
        assert path.read_bytes() == again.read_bytes()
        assert not (ACCEPTED_RUN / "exposure.json").exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) == TOP_LEVEL_KEYS
        _check_json_values(data, "exposure")
        assert len(data["per_agent"]) == 8
        u = data["utterances"]
        assert u["total"] >= u["naming_any_compound"] >= u["naming_known_pair"] > 0
        assert u["naming_known_pair"] >= u["naming_known_pair_and_product"] > 0
        e = data["exposures"]
        assert e["total"] >= e["naive"] > 0
        assert 0 <= e["rendered_permille"] <= 1000
        assert sum(row["naive"] for row in e["by_tier"].values()) == e["naive"]
        for row in e["by_tier"].values():
            assert 0 <= row["uptake"] <= row["naive"]
            assert 0 <= row["uptake_permille"] <= 1000
        assert set(e["by_tier"]) <= {"1", "2", "3", "4", "5"}
        c = data["control"]
        assert c["pairs"] > 0 and 0 <= c["uptake"] <= c["pairs"]
        assert 0 <= c["uptake_permille"] <= 1000
        acq = data["acquisitions"]
        assert sum(row["first_in_world"] for row in acq["by_tier"].values()) > 0
        assert len(acq["transmitted"]) == sum(row["transmitted"] for row in acq["by_tier"].values())
        for row in acq["transmitted"]:
            assert row["utterance_seq"] < row["production_seq"]
            assert 0 <= row["production_day"] - row["utterance_day"] <= data["window_days"]
        a = data["assertions"]
        for channel in ("converse", "note"):
            row = a[channel]
            assert row["total"] == row["correct"] + row["false"] + row["unknown"]
        assert a["false_replicated"] >= 0
        assert len(a["examples_false"]) <= 10
        assert [x["seq"] for x in a["examples_false"]] == sorted(
            x["seq"] for x in a["examples_false"]
        )

    def test_pair_rules_are_consistent(self, tmp_path: Path) -> None:
        connected = json.loads(
            write_exposure(ACCEPTED_RUN, out_path=tmp_path / "connected.json").read_text(
                encoding="utf-8"
            )
        )
        sentence = json.loads(
            write_exposure(
                ACCEPTED_RUN, out_path=tmp_path / "sentence.json", pair_rule="sentence"
            ).read_text(encoding="utf-8")
        )
        assert not (ACCEPTED_RUN / "exposure.json").exists()
        _assert_rule_invariants(connected, sentence)
        # Real speech enumerates: the strict rule must actually bite here.
        assert connected["exposures"]["total"] < sentence["exposures"]["total"]
        assert connected["utterances"]["naming_connected_pair"] > 0


@pytest.mark.skipif(
    not (ACCEPTED_RUN / "events.sqlite3").exists(), reason="accepted Phase-1 run not present"
)
def test_accepted_run_pins(tmp_path: Path) -> None:
    """Exact Phase-1 transmission baselines (pinned 2026-09-18; plan v2 §2.2, D1;
    devlog 002 errata). The frozen rule is ``connected`` at window 2; the
    ``sentence`` reading is measured alongside."""
    connected = json.loads(
        write_exposure(ACCEPTED_RUN, out_path=tmp_path / "c.json", window_days=2).read_text()
    )
    utt = connected["utterances"]
    assert (utt["total"], utt["naming_any_compound"]) == (567, 522)
    assert (utt["naming_connected_pair"], utt["naming_connected_pair_and_product"]) == (169, 84)
    assert (utt["naming_known_pair"], utt["naming_known_pair_and_product"]) == (257, 111)
    assert (utt["distinct_recipes_spoken"], utt["distinct_known_pairs_spoken"]) == (21, 21)
    ex = connected["exposures"]
    assert (ex["total"], ex["naive"], ex["rendered_permille"]) == (1729, 462, 991)
    by_tier = {t: (v["naive"], v["uptake"], v["uptake_permille"]) for t, v in ex["by_tier"].items()}
    assert by_tier == {
        "1": (179, 80, 446),
        "2": (118, 27, 228),
        "3": (144, 29, 201),
        "4": (7, 0, 0),
        "5": (14, 0, 0),
    }
    assert connected["control"]["uptake_permille"] == 63
    acq = connected["acquisitions"]["by_tier"]
    totals = {
        k: sum(acq[t][k] for t in acq) for k in ("first_in_world", "transmitted", "independent")
    }
    assert totals == {"first_in_world": 24, "transmitted": 51, "independent": 54}
    assert acq["1"] == {"first_in_world": 8, "transmitted": 25, "independent": 30}
    assertions = connected["assertions"]
    assert (assertions["converse"]["total"], assertions["converse"]["false"]) == (191, 15)
    assert (assertions["note"]["total"], assertions["note"]["false"]) == (64, 11)
    assert assertions["false_replicated"] == 6

    sentence = json.loads(
        write_exposure(
            ACCEPTED_RUN, out_path=tmp_path / "s.json", window_days=2, pair_rule="sentence"
        ).read_text()
    )
    assert (sentence["exposures"]["total"], sentence["exposures"]["naive"]) == (4200, 801)
    sacq = sentence["acquisitions"]["by_tier"]
    assert sum(sacq[t]["transmitted"] for t in sacq) == 57
    assert not (ACCEPTED_RUN / "c.json").exists() and not (ACCEPTED_RUN / "exposure.json").exists()
