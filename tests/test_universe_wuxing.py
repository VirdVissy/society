"""Tests for the wuxing universe (lamarck.universes.wuxing + .audit).

Golden literals are pinned from the canonical valley seed 0x57A57A57A57A57A5
(computed first, then pinned — any drift in generation is a semantic change
and must show up here). The TESTS may search the hidden rule table via the
test-only ``_debug_rules`` escape hatch; the agents never can — a hygiene
test below enforces that nothing outside tests references it.
"""

from __future__ import annotations

import random
import re
import tomllib
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lamarck.asserts import LamarckAssertionError
from lamarck.contracts import LiveWorldConfig, Outcome, Submission, TaskStub
from lamarck.engine.rng import splitmix64
from lamarck.universes import WuxingUniverse, audit_universe, load_live_config
from lamarck.universes.audit import main
from lamarck.universes.wuxing import BASES, MAX_STEPS, SLAG, TIER_COUNTS

CANON_SEED = "0x57A57A57A57A57A5"  # configs/valley.toml [universe] seed
REPO = Path(__file__).resolve().parent.parent
VALLEY = REPO / "configs" / "valley.toml"

# One shared instance: construction is deterministic and attempt is pure.
UNI = WuxingUniverse(CANON_SEED, 5)

# ------------------------------------------------------------- test helpers


def _name_of(stub: TaskStub) -> str:
    """Extract the target name from the public title ``produce "X"``."""
    return stub.title.split('"')[1]


def _all_stubs(uni: WuxingUniverse) -> list[TaskStub]:
    return [stub for tier in range(1, 6) for stub in uni.tasks(tier)]


def _recipes(uni: WuxingUniverse) -> dict[str, tuple[str, str]]:
    """TEST-SIDE oracle: product -> its recipe pair (via _debug_rules)."""
    out: dict[str, tuple[str, str]] = {}
    for key, product in uni._debug_rules().items():
        a, b = key.split("+")
        out[product] = (a, b)
    return out


def _derivation(uni: WuxingUniverse, target: str) -> list[list[str]]:
    """A minimal derivation for ``target``, found by the TEST's own search
    over the hidden rules (each distinct intermediate made exactly once)."""
    recipes = _recipes(uni)
    steps: list[list[str]] = []
    made: set[str] = set(BASES)

    def make(name: str) -> None:
        if name in made:
            return
        a, b = recipes[name]
        make(a)
        make(b)
        steps.append([a, b])
        made.add(name)

    make(target)
    return steps


# ------------------------------------------------- golden pins (canon seed)


class TestGolden:
    def test_manifest(self) -> None:
        m = UNI.manifest()
        assert m.name == "wuxing"
        assert m.tiers == 5
        assert m.compounds == 34
        assert m.universe_seed == CANON_SEED

    def test_tier1_names_sorted(self) -> None:
        names = sorted(_name_of(s) for s in UNI.tasks(1))
        assert names == [
            "autumn-mirror",
            "azure-coil",
            "burnished-iron",
            "carmine-pearl",
            "lacquer-root",
            "murmuring-smoke",
            "silt-ash",
            "whispering-bone",
        ]

    def test_task_ids_and_titles(self) -> None:
        stubs = UNI.tasks(1)
        assert [s.task_id for s in stubs] == [f"wx-t1-{i}" for i in range(8)]
        for stub in _all_stubs(UNI):
            assert re.fullmatch(r'produce "[a-z]+-[a-z]+"', stub.title)
        assert [s.task_id for s in UNI.tasks(5)] == [f"wx-t5-{i}" for i in range(5)]
        assert all(s.tier == 3 for s in UNI.tasks(3))

    def test_min_steps_histogram(self) -> None:
        report = UNI.oracle_audit()
        hist = dict(sorted(Counter(report.min_steps.values()).items()))
        assert hist == {1: 8, 2: 4, 3: 4, 4: 3, 5: 3, 6: 1, 7: 2, 8: 1, 9: 2, 10: 2, 11: 3, 12: 1}

    def test_base_pair_products(self) -> None:
        task = UNI.tasks(1)[0].task_id

        def product(a: str, b: str) -> str:
            return UNI.attempt(task, Submission(steps=[[a, b]])).step_products[0]

        assert product("wood", "fire") == "murmuring-smoke"
        assert product("earth", "water") == "burnished-iron"
        assert product("metal", "wood") == "azure-coil"


# ---------------------------------------------- generation properties (24 seeds)

SEEDS = [f"0x{splitmix64(i):016x}" for i in range(24)]


@pytest.mark.parametrize("seed_hex", SEEDS)
def test_seeded_universe_is_sound(seed_hex: str) -> None:
    uni = WuxingUniverse(seed_hex, 5)
    report = uni.oracle_audit()
    assert report.ok  # all compounds reachable, no collisions, no empty tier
    assert report.reachable_tiers == [1, 2, 3, 4, 5]
    assert report.compounds_by_tier == {str(t): n for t, n in TIER_COUNTS.items()}
    assert max(report.min_steps.values()) <= MAX_STEPS  # every commission winnable

    rules = uni._debug_rules()
    assert len(rules) == 34  # unordered recipe pairs unique universe-wide

    stubs = _all_stubs(uni)
    names = {_name_of(s) for s in stubs}
    assert len(names) == 34  # no name collisions
    assert not names & (set(BASES) | {SLAG})

    # recipe structure: distinct ingredients, tiers < t, one of tier exactly t-1
    tier_of = dict.fromkeys(BASES, 0)
    for stub in stubs:
        tier_of[_name_of(stub)] = stub.tier
    recipes = _recipes(uni)
    for stub in stubs:
        a, b = recipes[_name_of(stub)]
        assert a != b
        assert tier_of[a] < stub.tier
        assert tier_of[b] < stub.tier
        assert max(tier_of[a], tier_of[b]) == stub.tier - 1

    # regeneration determinism: identical rules, manifest, and task board
    again = WuxingUniverse(seed_hex, 5)
    assert again._debug_rules() == rules
    assert again.manifest() == uni.manifest()
    for tier in range(1, 6):
        assert again.tasks(tier) == uni.tasks(tier)


# ------------------------------------------------------------ attempt semantics


class TestAttempt:
    def test_known_good_derivation_verifies(self) -> None:
        stub = UNI.tasks(3)[0]
        target = _name_of(stub)
        steps = _derivation(UNI, target)  # the TEST searches; the agent never does
        out = UNI.attempt(stub.task_id, Submission(steps=steps))
        assert out.verified is True
        assert out.product == target
        assert out.tier == 3
        assert out.step_products[-1] == target
        assert out.message == (f"the crucible yields {target}; the essence of {target} condenses")

    def test_every_commission_winnable_within_cap(self) -> None:
        for stub in _all_stubs(UNI):
            steps = _derivation(UNI, _name_of(stub))
            assert 1 <= len(steps) <= MAX_STEPS
            assert UNI.attempt(stub.task_id, Submission(steps=steps)).verified

    def test_mid_procedure_production_counts(self) -> None:
        stub = UNI.tasks(1)[0]
        target = _name_of(stub)
        a, b = _recipes(UNI)[target]
        out = UNI.attempt(
            stub.task_id, Submission(steps=[[a, b], ["wood", "wood"]])
        )  # slag chaser after the target: mid-procedure production still verifies
        assert out.verified is True
        assert out.step_products == [target, SLAG]
        assert out.product == SLAG  # final product is the last step's
        assert out.message == f"the crucible yields slag; the essence of {target} condenses"

    def test_unavailable_ingredient_stops_execution(self) -> None:
        stub = UNI.tasks(2)[0]  # tier-2 target cannot equal a 1-step product
        first = UNI.attempt(stub.task_id, Submission(steps=[["wood", "fire"]])).step_products[0]
        out = UNI.attempt(
            stub.task_id,
            Submission(steps=[["wood", "fire"], ["wood", "missing-thing"], ["fire", "water"]]),
        )
        assert out.verified is False
        assert out.step_products == [first]  # execution stopped at step 2
        assert out.product == first
        assert out.message == "the missing-thing is not at hand"

    def test_unavailable_first_ingredient_reported_first(self) -> None:
        stub = UNI.tasks(1)[0]
        out = UNI.attempt(stub.task_id, Submission(steps=[["ghost-a", "ghost-b"]]))
        assert out.verified is False
        assert out.step_products == []
        assert out.product == ""
        assert out.message == "the ghost-a is not at hand"

    def test_compound_unavailable_before_derived(self) -> None:
        stub = UNI.tasks(2)[0]
        a, b = _recipes(UNI)[_name_of(stub)]  # at least one is a tier-1 compound
        out = UNI.attempt(stub.task_id, Submission(steps=[[a, b]]))
        assert out.verified is False
        assert out.step_products == []
        assert out.message.endswith("is not at hand")

    def test_slag_propagates(self) -> None:
        rules = UNI._debug_rules()
        dead_pair = next(  # one of the 2 (of 10) base pairs that is not a recipe
            (a, b)
            for i, a in enumerate(BASES)
            for b in BASES[i + 1 :]
            if f"{min(a, b)}+{max(a, b)}" not in rules
        )
        stub = UNI.tasks(1)[0]
        out = UNI.attempt(
            stub.task_id,
            Submission(steps=[list(dead_pair), [SLAG, "wood"], [SLAG, SLAG]]),
        )
        assert out.verified is False
        assert out.step_products == [SLAG, SLAG, SLAG]
        assert out.product == SLAG
        assert out.message == "the crucible yields slag"

    def test_slag_not_available_until_produced(self) -> None:
        stub = UNI.tasks(1)[0]
        out = UNI.attempt(stub.task_id, Submission(steps=[[SLAG, "wood"]]))
        assert out.message == "the slag is not at hand"
        assert out.step_products == []

    def test_unknown_task(self) -> None:
        out = UNI.attempt("wx-t9-0", Submission(steps=[["wood", "fire"]]))
        assert out == Outcome(
            verified=False, product="", tier=0, step_products=[], message="no such commission"
        )

    def test_empty_steps_rejected(self) -> None:
        stub = UNI.tasks(4)[0]
        out = UNI.attempt(stub.task_id, Submission(steps=[]))
        assert out.verified is False
        assert out.step_products == []
        assert out.product == ""
        assert out.tier == 4
        assert out.message == "the procedure names no steps"

    def test_step_count_cap(self) -> None:
        stub = UNI.tasks(1)[0]
        out = UNI.attempt(stub.task_id, Submission(steps=[["wood", "fire"]] * 13))
        assert out.verified is False
        assert out.step_products == []
        assert out.message == "the crucible admits at most 12 combinations; 13 were offered"
        out12 = UNI.attempt(stub.task_id, Submission(steps=[["wood", "fire"]] * 12))
        assert len(out12.step_products) == 12  # exactly the cap executes

    def test_step_arity_rejected_upfront(self) -> None:
        stub = UNI.tasks(1)[0]
        out = UNI.attempt(stub.task_id, Submission(steps=[["wood", "fire"], ["wood"]]))
        assert out.verified is False
        assert out.step_products == []  # up-front validation: nothing executes
        assert out.message == "step 2 must combine exactly two ingredients"
        out3 = UNI.attempt(stub.task_id, Submission(steps=[["wood", "fire", "water"]]))
        assert out3.message == "step 1 must combine exactly two ingredients"

    def test_determinism_same_submission_twice(self) -> None:
        stub = UNI.tasks(3)[0]
        sub = Submission(steps=_derivation(UNI, _name_of(stub)))
        first = UNI.attempt(stub.task_id, sub)
        second = UNI.attempt(stub.task_id, sub)
        assert first == second
        assert first.model_dump_json() == second.model_dump_json()
        fresh = WuxingUniverse(CANON_SEED, 5).attempt(stub.task_id, sub)
        assert fresh == first  # and identical across a regenerated instance


@settings(max_examples=80, derandomize=True, deadline=None)
@given(
    steps=st.lists(st.lists(st.text(max_size=6), max_size=4), max_size=14),
    task_id=st.sampled_from(["wx-t1-0", "wx-t3-2", "wx-t5-4", "wx-t9-9", "bogus"]),
)
def test_attempt_is_total_and_pure(steps: list[list[str]], task_id: str) -> None:
    """attempt never raises for anything a submitter can send, and is pure."""
    sub = Submission(steps=steps)
    first = UNI.attempt(task_id, sub)
    assert isinstance(first, Outcome)
    assert UNI.attempt(task_id, sub) == first


# ------------------------------------------------------------------- secrecy


class TestSecrecy:
    def test_no_recipe_leak_on_public_surfaces(self) -> None:
        stubs = _all_stubs(UNI)
        names = {_name_of(s) for s in stubs}
        recipes = _recipes(UNI)
        probes = [
            Submission(steps=[["wood", "fire"]]),
            Submission(steps=[["earth", "water"], ["fire", "fire"]]),
            Submission(steps=[["metal", "wood"], ["water", "metal"], ["wood", "earth"]]),
            Submission(steps=[["ghost-thing", "wood"]]),
            Submission(steps=[]),
            Submission(steps=[["wood"]]),
            Submission(steps=[["wood", "fire"]] * 13),
        ]
        surfaces: list[str] = [UNI.manifest().model_dump_json()]
        surfaces += [s.model_dump_json() for s in stubs]
        derived: set[str] = set()
        rows: list[tuple[TaskStub, Submission, Outcome]] = []
        for stub in stubs:
            for probe in probes:
                out = UNI.attempt(stub.task_id, probe)
                rows.append((stub, probe, out))
                derived |= set(out.step_products)
        surfaces += [out.model_dump_json() for (_, _, out) in rows]

        # (i) no surface string co-reveals an underived compound with its pair
        # (sound because generation asserts no name is a substring of another)
        for name in names - derived:
            a, b = recipes[name]
            for surface in surfaces:
                assert not (a in surface and b in surface and name in surface), (
                    f"recipe of {name} leaked"
                )

        # (ii) messages name only submitted ingredients, own products, the target
        for stub, probe, out in rows:
            allowed = (
                {x for step in probe.steps for x in step}
                | set(out.step_products)
                | {_name_of(stub)}
            )
            for name in names:
                if name in out.message:
                    assert name in allowed, f"message leaked {name}: {out.message!r}"

        # (iii) a title contains its own target and no other compound name
        for stub in stubs:
            for name in names:
                if name in stub.title:
                    assert name == _name_of(stub)

        # (iv) the manifest carries only counts and the seed — no names at all
        manifest_json = UNI.manifest().model_dump_json()
        for name in names:
            assert name not in manifest_json

    def test_debug_rules_never_referenced_outside_tests(self) -> None:
        """The test-only escape hatch must not creep into engine code."""
        offenders = [
            str(path)
            for path in (REPO / "lamarck").rglob("*.py")
            if "_debug_rules" in path.read_text(encoding="utf-8") and path.name != "wuxing.py"
        ]
        assert offenders == []
        assert "_debug_rules" in (REPO / "lamarck" / "universes" / "wuxing.py").read_text(
            encoding="utf-8"
        )


# ------------------------------------------------------- search feasibility


def test_random_searcher_discovers_tier1_compounds() -> None:
    """A seeded random searcher doing <=120 one-step base-pair attempts must
    discover >=3 distinct tier-1 compounds — the world is kind enough for
    Phase-1 agents to get a foothold by dumb luck."""
    rng = random.Random(0xBEEF)
    tier1 = {_name_of(s) for s in UNI.tasks(1)}
    task = UNI.tasks(1)[0].task_id
    found: set[str] = set()
    for _ in range(120):
        a, b = rng.choice(BASES), rng.choice(BASES)
        out = UNI.attempt(task, Submission(steps=[[a, b]]))
        found |= set(out.step_products) & tier1
    assert len(found) >= 3


# ------------------------------------------------------------------ audit


class TestAudit:
    def test_structure_audit_ok(self) -> None:
        report = UNI.oracle_audit()
        assert report.ok is True
        assert report.reachable_tiers == [1, 2, 3, 4, 5]
        assert report.compounds_by_tier == {"1": 8, "2": 8, "3": 7, "4": 6, "5": 5}
        assert all(isinstance(k, str) for k in report.compounds_by_tier)
        names = {_name_of(s) for s in _all_stubs(UNI)}
        assert set(report.min_steps) == names
        assert all(1 <= v <= MAX_STEPS for v in report.min_steps.values())
        for stub in UNI.tasks(1):
            assert report.min_steps[_name_of(stub)] == 1

    def test_min_steps_matches_test_side_search(self) -> None:
        """Independent cross-check: audit's closure sizes equal the length of
        the test's own dedup'd BFS derivations."""
        report = UNI.oracle_audit()
        for stub in _all_stubs(UNI):
            target = _name_of(stub)
            assert report.min_steps[target] == len(_derivation(UNI, target))

    def test_valley_config_matches_canon(self) -> None:
        cfg = load_live_config(VALLEY)
        assert cfg.universe.name == "wuxing"
        assert cfg.universe.seed == CANON_SEED
        assert cfg.universe.tiers == 5

    def test_audit_universe_valley(self) -> None:
        report = audit_universe(load_live_config(VALLEY))
        assert report.ok is True
        assert report.compounds_by_tier == {"1": 8, "2": 8, "3": 7, "4": 6, "5": 5}
        assert any(note.startswith("budget:") for note in report.notes)
        assert any("self-funding" in note for note in report.notes)
        assert any("fits one experiment" in note for note in report.notes)

    def test_audit_universe_rejects_unknown_name(self) -> None:
        data = tomllib.loads(VALLEY.read_text())
        data["universe"]["name"] = "sudoku"
        cfg = LiveWorldConfig.model_validate(data)
        with pytest.raises(ValueError, match="unknown universe"):
            audit_universe(cfg)

    def test_load_live_config_rejects_bad_seed(self, tmp_path: Path) -> None:
        text = VALLEY.read_text().replace('seed = "0x57A57A57A57A57A5"', 'seed = "0xNOTHEX"')
        bad = tmp_path / "bad.toml"
        bad.write_text(text)
        with pytest.raises(ValueError, match="hex"):
            load_live_config(bad)

    def test_cli_main_valley(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main([str(VALLEY)])
        out = capsys.readouterr().out
        assert code == 0
        assert "wuxing universe audit" in out
        assert "ok: True" in out
        assert "compounds by tier: t1:8 t2:8 t3:7 t4:6 t5:5" in out
        assert "min-steps histogram" in out

    def test_cli_main_missing_file(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([str(REPO / "configs" / "no-such-config.toml")]) == 1
        assert "not found" in capsys.readouterr().err

    def test_cli_main_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == 2
        assert "usage" in capsys.readouterr().err


# ------------------------------------------------------------- construction


class TestConstruction:
    def test_bad_seed_rejected(self) -> None:
        with pytest.raises(LamarckAssertionError):
            WuxingUniverse("not-hex", 5)

    def test_seed_out_of_u64_rejected(self) -> None:
        with pytest.raises(LamarckAssertionError):
            WuxingUniverse("0x1" + "0" * 16, 5)  # 2**64, one past the top

    def test_wrong_tiers_rejected(self) -> None:
        with pytest.raises(LamarckAssertionError):
            WuxingUniverse(CANON_SEED, 3)

    def test_tasks_tier_out_of_range_is_engine_bug(self) -> None:
        with pytest.raises(LamarckAssertionError):
            UNI.tasks(0)
        with pytest.raises(LamarckAssertionError):
            UNI.tasks(6)
