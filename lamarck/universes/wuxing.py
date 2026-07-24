"""Universe #1 — Wuxing Alchemy: a procedurally generated hidden chemistry.

Implements ``lamarck.contracts.UniverseP`` (Phase 1). The world is a fixed
rule table generated deterministically from one hex-u64 seed; ``attempt`` is
a pure function of ``(task_id, submission)`` — no RNG anywhere at attempt
time, and no exceptions for anything a submitter can cause (a malformed
submission is a *result*, never a crash; exceptions are reserved for engine
bugs via ``LMK_ASSERT``).

Structure (all generation randomness from ``RngStreams(int(seed_hex, 16))``,
streams ``"wuxing-structure"`` and ``"wuxing-names"``):

- Bases: wood, fire, earth, metal, water (tier 0; always available; never
  tasks).
- Compounds per tier: {1: 8, 2: 8, 3: 7, 4: 6, 5: 5} — 34 total. Each
  tier-``t`` compound has exactly ONE recipe: an unordered pair of distinct
  ingredients of tier ``< t``, at least one of tier exactly ``t - 1`` (forces
  depth). Recipe pairs are unique across the whole universe: an unordered
  pair maps to at most one product (bounded re-draw on collision).
- Producibility cap: a candidate recipe is also re-drawn when the compound's
  minimal derivation (its ancestor closure — each compound has one recipe,
  so the derivation is unique up to ordering) would exceed
  ``MAX_STEPS - (tiers - t)`` steps. The per-tier headroom guarantees a
  valid draw always exists at every higher tier (any frontier compound plus
  any base stays under the cap, by induction), and that EVERY commission is
  completable inside one ``MAX_STEPS``-step experiment — attempts always
  restart from bases, so a compound over the cap would be unwinnable.
- Every unordered pair that is not a recipe yields ``"slag"`` — including
  every unused base+base pair, any pair involving slag itself, and any
  same-ingredient pair.
- Names are two-part ``prefix-suffix`` draws from two seeded word lists,
  collision-free, never a base name, never ``"slag"``, and (asserted) never
  a substring of another compound name nor containing a base name — the
  no-substring guarantees keep leak auditing sound.

SECRECY: the recipe table is the hidden Dao. It must never leak through any
public surface — manifest, tasks, titles, and Outcome messages may name only
(a) the task's own target, (b) ingredients the submitter themselves
submitted, and (c) products their own steps yielded. ``_debug_rules`` is a
TEST-ONLY escape hatch (see its docstring) and must never be imported or
called outside tests.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from lamarck.asserts import LMK_ASSERT
from lamarck.contracts import (
    AuditReport,
    Outcome,
    Submission,
    TaskStub,
    UniverseManifest,
)
from lamarck.engine.rng import RngStreams

UNIVERSE_NAME = "wuxing"
BASES: tuple[str, ...] = ("wood", "fire", "earth", "metal", "water")
SLAG = "slag"
TIER_COUNTS: dict[int, int] = {1: 8, 2: 8, 3: 7, 4: 6, 5: 5}
MAX_STEPS = 12  # a Submission may hold 1..MAX_STEPS combination steps

_STRUCTURE_STREAM = "wuxing-structure"
_NAMES_STREAM = "wuxing-names"
_RETRY_BOUND = 256  # bounded re-draws per recipe / per name before LMK_ASSERT
_U64_BOUND = 1 << 64

# Seeded word lists for compound names. Curated so that no word contains a
# base name and no word is a substring of another word — with the hyphen
# join this makes any compound-name-in-compound-name (or base-in-name)
# substring collision impossible, which the generation asserts re-check.
_PREFIXES: tuple[str, ...] = (
    "cinnabar",
    "azure",
    "pale",
    "thunder",
    "hollow",
    "ember",
    "jade",
    "silt",
    "vermilion",
    "umbral",
    "drifting",
    "burnished",
    "gilded",
    "molten",
    "verdant",
    "sable",
    "radiant",
    "murmuring",
    "whispering",
    "autumn",
    "winter",
    "carmine",
    "lacquer",
    "porcelain",
)
_SUFFIXES: tuple[str, ...] = (
    "ash",
    "dew",
    "marrow",
    "salt",
    "bloom",
    "iron",
    "breath",
    "glass",
    "root",
    "spark",
    "tide",
    "bone",
    "lantern",
    "thread",
    "mirror",
    "crown",
    "smoke",
    "pearl",
    "coil",
    "veil",
    "cinder",
)


@dataclass(frozen=True)
class _Compound:
    """One generated compound. ``recipe`` is the sorted unordered pair."""

    name: str
    tier: int
    index: int  # generation order within its tier (0-based)
    task_id: str
    recipe: tuple[str, str]


def _pair_key(a: str, b: str) -> tuple[str, str]:
    """Canonical key for an unordered ingredient pair."""
    return (a, b) if a <= b else (b, a)


class WuxingUniverse:
    """The wuxing alchemy universe (implements ``UniverseP``)."""

    def __init__(self, seed_hex: str, tiers: int) -> None:
        try:
            seed = int(seed_hex, 16)
        except ValueError:
            seed = -1
        LMK_ASSERT(
            0 <= seed < _U64_BOUND,
            "wuxing seed must parse as a hex u64",
            seed_hex=seed_hex,
        )
        LMK_ASSERT(tiers == 5, "wuxing v1 supports exactly 5 tiers", tiers=tiers)
        self._seed_hex = seed_hex
        self._tiers = tiers
        self._compounds: list[_Compound] = []
        self._by_task_id: dict[str, _Compound] = {}
        self._by_name: dict[str, _Compound] = {}
        self._rules: dict[tuple[str, str], str] = {}
        self._closures: dict[str, frozenset[str]] = {}
        self._generate(seed)
        self._assert_invariants()

    # ------------------------------------------------------------ generation

    def _generate(self, seed: int) -> None:
        """Build compounds tier by tier, in locked draw order.

        For each compound (global generation order: tier 1 first, index 0
        first): one recipe from the structure stream, then one name from the
        names stream. Pools are ordered deterministically (bases in fixed
        order, compounds in generation order), so regeneration from the same
        seed reproduces the identical universe.
        """
        rngs = RngStreams(seed)
        structure = rngs.stream(_STRUCTURE_STREAM)
        names = rngs.stream(_NAMES_STREAM)
        items: list[tuple[str, int]] = [(base, 0) for base in BASES]
        used_names: set[str] = set()
        closures: dict[str, frozenset[str]] = {base: frozenset() for base in BASES}
        for tier in range(1, self._tiers + 1):
            frontier = [name for (name, t) in items if t == tier - 1]
            lower = [name for (name, t) in items if t < tier]
            cap = MAX_STEPS - (self._tiers - tier)
            for index in range(TIER_COUNTS[tier]):
                recipe = self._draw_recipe(structure, tier, frontier, lower, closures, cap)
                name = self._draw_name(names, tier, used_names)
                used_names.add(name)
                task_id = f"wx-t{tier}-{index}"
                compound = _Compound(
                    name=name, tier=tier, index=index, task_id=task_id, recipe=recipe
                )
                self._compounds.append(compound)
                self._by_task_id[task_id] = compound
                self._by_name[name] = compound
                self._rules[recipe] = name
                closures[name] = closures[recipe[0]] | closures[recipe[1]] | {name}
                items.append((name, tier))
        self._closures = closures

    def _draw_recipe(
        self,
        rng: random.Random,
        tier: int,
        frontier: list[str],
        lower: list[str],
        closures: dict[str, frozenset[str]],
        cap: int,
    ) -> tuple[str, str]:
        """Draw an unused unordered pair: one ingredient of tier exactly
        ``tier - 1`` (``frontier``), one of any tier ``< tier`` (``lower``),
        distinct, whose product's minimal derivation fits ``cap`` steps.
        Bounded retries; exhaustion is an engine bug (a valid draw always
        exists — any frontier ingredient plus any base is under the cap)."""
        for _ in range(_RETRY_BOUND):
            a = rng.choice(frontier)
            rest = [name for name in lower if name != a]
            b = rng.choice(rest)
            pair = _pair_key(a, b)
            if pair in self._rules:
                continue
            if len(closures[a] | closures[b]) + 1 > cap:
                continue
            return pair
        LMK_ASSERT(False, "recipe draw exhausted retries", tier=tier, bound=_RETRY_BOUND)
        raise AssertionError("unreachable")  # LMK_ASSERT(False) always raises

    def _draw_name(self, rng: random.Random, tier: int, used: set[str]) -> str:
        """Draw an unused ``prefix-suffix`` name; never a base, never slag."""
        for _ in range(_RETRY_BOUND):
            name = f"{rng.choice(_PREFIXES)}-{rng.choice(_SUFFIXES)}"
            if name not in used and name not in BASES and name != SLAG:
                return name
        LMK_ASSERT(False, "name draw exhausted retries", tier=tier, bound=_RETRY_BOUND)
        raise AssertionError("unreachable")  # LMK_ASSERT(False) always raises

    def _tier_of(self, ingredient: str) -> int:
        """Tier of a base (0) or generated compound; unknown is a bug."""
        if ingredient in BASES:
            return 0
        compound = self._by_name.get(ingredient)
        LMK_ASSERT(compound is not None, "unknown ingredient in rule table", name=ingredient)
        assert compound is not None  # for mypy; LMK_ASSERT raised otherwise
        return compound.tier

    def _assert_invariants(self) -> None:
        """Post-generation engine invariants (always-on)."""
        total = sum(TIER_COUNTS.values())
        LMK_ASSERT(
            len(self._compounds) == total,
            "compound count mismatch",
            got=len(self._compounds),
            want=total,
        )
        LMK_ASSERT(
            len(self._rules) == total,
            "recipe pairs must be unique across the universe",
            rules=len(self._rules),
        )
        for tier, count in TIER_COUNTS.items():
            got = sum(1 for c in self._compounds if c.tier == tier)
            LMK_ASSERT(got == count, "tier count mismatch", tier=tier, got=got, want=count)
        names = [c.name for c in self._compounds]
        LMK_ASSERT(len(set(names)) == len(names), "compound name collision", names=len(names))
        for compound in self._compounds:
            a, b = compound.recipe
            LMK_ASSERT(a != b, "recipe ingredients must be distinct", task=compound.task_id)
            tier_a, tier_b = self._tier_of(a), self._tier_of(b)
            LMK_ASSERT(
                tier_a < compound.tier and tier_b < compound.tier,
                "recipe ingredient tier must be below product tier",
                task=compound.task_id,
            )
            LMK_ASSERT(
                max(tier_a, tier_b) == compound.tier - 1,
                "recipe must use at least one ingredient of tier t-1",
                task=compound.task_id,
            )
            LMK_ASSERT(
                compound.name not in BASES and compound.name != SLAG,
                "compound name shadows a base or slag",
                name=compound.name,
            )
            for base in BASES:
                LMK_ASSERT(
                    base not in compound.name,
                    "compound name contains a base name",
                    name=compound.name,
                    base=base,
                )
            LMK_ASSERT(
                len(self._closures[compound.name]) <= MAX_STEPS - (self._tiers - compound.tier),
                "minimal derivation exceeds the producibility cap",
                name=compound.name,
                steps=len(self._closures[compound.name]),
            )
        for one in names:
            for other in names:
                LMK_ASSERT(
                    one == other or one not in other,
                    "compound name is a substring of another",
                    name=one,
                    other=other,
                )

    # ------------------------------------------------------------ public API

    def manifest(self) -> UniverseManifest:
        return UniverseManifest(
            name=UNIVERSE_NAME,
            tiers=self._tiers,
            compounds=len(self._compounds),
            universe_seed=self._seed_hex,
        )

    def tasks(self, tier: int) -> list[TaskStub]:
        """The tier's task stubs in generation order. Tier out of range is a
        caller bug (the runner assembles the board from validated config)."""
        LMK_ASSERT(1 <= tier <= self._tiers, "tier outside universe range", tier=tier)
        return [
            TaskStub(task_id=c.task_id, tier=c.tier, title=f'produce "{c.name}"')
            for c in self._compounds
            if c.tier == tier
        ]

    def attempt(self, task_id: str, submission: Submission) -> Outcome:
        """Pure, deterministic verification of one procedure.

        Malformed input yields a non-verified Outcome with a precise
        in-fiction message — never an exception. Messages name only what the
        submitter already knows: their own submitted ingredients, their own
        step products, and the task's public target.
        """
        compound = self._by_task_id.get(task_id)
        if compound is None:
            return Outcome(
                verified=False,
                product="",
                tier=0,
                step_products=[],
                message="no such commission",
            )
        steps = submission.steps
        if len(steps) == 0:
            return self._rejected(compound, "the procedure names no steps")
        if len(steps) > MAX_STEPS:
            return self._rejected(
                compound,
                f"the crucible admits at most {MAX_STEPS} combinations; {len(steps)} were offered",
            )
        for position, step in enumerate(steps, start=1):
            if len(step) != 2:
                return self._rejected(
                    compound, f"step {position} must combine exactly two ingredients"
                )
        available: set[str] = set(BASES)
        step_products: list[str] = []
        halted_on: str | None = None
        for step in steps:
            first, second = step[0], step[1]
            if first not in available:
                halted_on = first
                break
            if second not in available:
                halted_on = second
                break
            product = self._combine(first, second)
            step_products.append(product)
            available.add(product)
        verified = compound.name in step_products
        final = step_products[-1] if step_products else ""
        if halted_on is not None:
            message = f"the {halted_on} is not at hand"
        else:
            message = f"the crucible yields {final}"
        if verified:
            message += f"; the essence of {compound.name} condenses"
        return Outcome(
            verified=verified,
            product=final,
            tier=compound.tier,
            step_products=step_products,
            message=message,
        )

    def oracle_audit(self) -> AuditReport:
        """Structure-only audit: closure reachability, minimal derivation
        sizes, name integrity. ``ok`` is False iff any compound is
        unreachable, any name collides (incl. with a base or slag), or any
        tier is empty. Budget cross-checks against an economy live in
        ``lamarck.universes.audit.audit_universe``.

        ``min_steps`` per compound = combination steps on its cheapest
        derivation counting each distinct intermediate once — since every
        compound has exactly one recipe this is the size of its ancestor
        closure (bases are free). Reported for every compound; independently
        of it, reachability is re-derived from the rule table alone.
        """
        reached: set[str] = set(BASES)
        changed = True
        while changed:
            changed = False
            for (a, b), product in sorted(self._rules.items()):
                if product not in reached and a in reached and b in reached:
                    reached.add(product)
                    changed = True
        unreachable = sorted(c.name for c in self._compounds if c.name not in reached)

        memo: dict[str, frozenset[str]] = {}

        def closure(name: str) -> frozenset[str]:
            if name in BASES:
                return frozenset()
            cached = memo.get(name)
            if cached is not None:
                return cached
            compound = self._by_name[name]
            a, b = compound.recipe
            result = closure(a) | closure(b) | {name}
            memo[name] = result
            return result

        min_steps = {c.name: len(closure(c.name)) for c in self._compounds}

        names = [c.name for c in self._compounds]
        collisions = sorted({n for n in names if names.count(n) > 1 or n in BASES or n == SLAG})
        counts = {
            str(tier): sum(1 for c in self._compounds if c.tier == tier)
            for tier in range(1, self._tiers + 1)
        }
        empty_tiers = sorted(int(t) for t, n in counts.items() if n == 0)
        reachable_tiers = sorted({c.tier for c in self._compounds if c.name in reached})

        ok = not unreachable and not collisions and not empty_tiers
        deepest = max(min_steps.values()) if min_steps else 0
        notes = [
            f"{len(self._compounds) - len(unreachable)}/{len(self._compounds)} "
            f"compounds reachable from the {len(BASES)} bases",
            f"deepest minimal derivation: {deepest} steps (procedure cap {MAX_STEPS})",
        ]
        over_cap = sorted(name for name, n in min_steps.items() if n > MAX_STEPS)
        if over_cap:
            notes.append(
                f"WARNING: compounds needing more than {MAX_STEPS} steps "
                f"(unproducible in one experiment): {over_cap}"
            )
        if unreachable:
            notes.append(f"unreachable compounds: {unreachable}")
        if collisions:
            notes.append(f"name collisions: {collisions}")
        if empty_tiers:
            notes.append(f"empty tiers: {empty_tiers}")
        return AuditReport(
            ok=ok,
            reachable_tiers=reachable_tiers,
            compounds_by_tier=counts,
            min_steps=min_steps,
            notes=notes,
        )

    # -------------------------------------------------------------- internal

    def _combine(self, a: str, b: str) -> str:
        """Rule-table lookup for an unordered pair; anything else is slag."""
        return self._rules.get(_pair_key(a, b), SLAG)

    def _rejected(self, compound: _Compound, message: str) -> Outcome:
        """Non-verified Outcome for a malformed (but known-task) submission."""
        return Outcome(
            verified=False,
            product="",
            tier=compound.tier,
            step_products=[],
            message=message,
        )

    def _debug_rules(self) -> dict[str, str]:
        """TEST-ONLY canonical serialization of the hidden rule table.

        Maps ``"a+b"`` (pair sorted) to its product, sorted by key. Exists
        solely so tests can check regeneration determinism and recipe-pair
        uniqueness, and search for known-good derivations. NEVER import or
        call this outside tests — it is the entire hidden Dao in one dict.
        """
        return {f"{a}+{b}": product for (a, b), product in sorted(self._rules.items())}


__all__ = [
    "BASES",
    "MAX_STEPS",
    "SLAG",
    "TIER_COUNTS",
    "UNIVERSE_NAME",
    "WuxingUniverse",
]
