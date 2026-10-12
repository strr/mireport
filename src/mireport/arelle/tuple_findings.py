"""What an XBRL 2.1 report's tuples are made of, which its xBRL-JSON does not say.

Arelle's xBRL-JSON has no tuple children: each tuple instance is an empty stub. The plug-in
sees the model, so it reads them here. For each tuple kind (a tuple concept) it reports its
child concepts and, from their values across the kind's instances, what each child could be:

- ``identity``: not numeric, once in every instance, no nil, and its values differ from one
  instance to the next within a context (the investee's name in a table of investees). The
  row identity a dimension could carry;
- ``pinned``: the same value in every instance (the currency of the table);
- ``measure``: numeric;
- ``tuple``: a nested tuple;
- ``attribute``: anything else (it varies, but does not by itself tell the rows apart).

When no child alone identifies the rows, pairs of ``attribute`` children that do together
are listed, and a kind with neither is ``identityUnclear``.

The instances of one kind that have the same contexts (as their items' ``contextRef``s) are
one *context group*; "differ within a context" means within each group. The values are the
lexical ones. `ArelleProcessingResult` hands the kinds back as ``TupleKind``s.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING, Any

from mireport.arelle.diagnostics import ArelleDiagnostic

if TYPE_CHECKING:
    from arelle.ModelXbrl import ModelXbrl

TUPLE_KIND = "tuple-kind"
"""The `kind` detail of the diagnostic that carries a tuple kind."""

_SAMPLE = 3
_SAMPLE_WIDTH = 40


@dataclass(frozen=True)
class TupleChild:
    """One child concept of a tuple kind, over all its instances."""

    concept: str
    role: str
    """``identity``, ``pinned``, ``measure``, ``tuple`` or ``attribute``."""
    occurrences: int
    instances: int
    """How many instances have at least one of it."""
    maxPerInstance: int
    distinct: int
    """Distinct values, nil ones left out."""
    nil: int
    units: tuple[str, ...]
    sample: tuple[str, ...]
    """The first few distinct values, cut short."""


@dataclass(frozen=True)
class TupleKind:
    """A tuple concept as the report uses it."""

    concept: str
    parents: tuple[str, ...]
    """The tuple kinds it is nested in, in the order first met."""
    instances: int
    contextGroups: int
    children: tuple[TupleChild, ...]
    """In the order first met."""
    identity: tuple[str, ...]
    """The children that each tell every instance apart within a context group."""
    identityPairs: tuple[tuple[str, str], ...]
    """Pairs of ``attribute`` children that do together, when none does alone."""
    identityUnclear: bool
    """Nothing, alone or in a pair, tells the instances apart."""


@dataclass
class _Draft:
    qname: Any
    parents: dict[str, None]
    instances: list[Sequence[Any]]


def _is_tuple(fact: Any) -> bool:
    return bool(fact.isTuple)


def _collect(facts: Sequence[Any]) -> dict[str, _Draft]:
    drafts: dict[str, _Draft] = {}

    def walk(fact: Any, parent: str | None) -> None:
        name = str(fact.qname)
        draft = drafts.setdefault(name, _Draft(fact.qname, {}, []))
        if parent is not None:
            draft.parents.setdefault(parent, None)
        children = list(fact.modelTupleFacts)
        draft.instances.append(children)
        for child in children:
            if _is_tuple(child):
                walk(child, name)

    for fact in facts:
        if _is_tuple(fact):
            walk(fact, None)
    return drafts


def _value(fact: Any) -> str | None:
    if fact.isNil or fact.value is None:
        return None
    return str(fact.value).strip()


def _context_group(children: Sequence[Any]) -> tuple[str, ...]:
    return tuple(
        sorted({str(c.contextID) for c in children if not _is_tuple(c) and c.contextID})
    )


def _unique_within_groups(
    values: Sequence[Sequence[str]], groups: Sequence[tuple[str, ...]]
) -> bool:
    """Whether the tuples of ``values`` (one per instance) are all different within
    each context group."""
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    for group, value in zip(groups, values, strict=True):
        key = (group, tuple(value))
        if key in seen:
            return False
        seen.add(key)
    return True


def _kind(name: str, draft: _Draft) -> TupleKind:
    instances = draft.instances
    groups = [_context_group(children) for children in instances]
    order: list[str] = []
    by_child: dict[str, list[list[Any]]] = {}
    for children in instances:
        here: dict[str, list[Any]] = {}
        for child in children:
            concept = str(child.qname)
            if concept not in by_child:
                order.append(concept)
                by_child[concept] = []
            here.setdefault(concept, []).append(child)
        for concept, found in here.items():
            by_child[concept].append(found)

    count = len(instances)
    # Per instance, the one value of each child that is once in every instance.
    single: dict[str, list[str]] = {}
    children: list[TupleChild] = []
    roles: dict[str, str] = {}
    for concept in order:
        per_instance = by_child[concept]
        facts = [f for found in per_instance for f in found]
        first = facts[0]
        values = [v for f in facts if (v := _value(f)) is not None]
        distinct = list(dict.fromkeys(values))
        nil = len(facts) - len(values)
        units = tuple(dict.fromkeys(str(f.unitID) for f in facts if f.unitID))
        once = len(per_instance) == count and all(len(f) == 1 for f in per_instance)
        if _is_tuple(first):
            role = "tuple"
        elif first.concept.isNumeric:
            role = "measure"
        elif not once or nil:
            role = "attribute"
        elif count > 1 and len(distinct) == 1:
            role = "pinned"
        else:
            column = [[_value(found[0]) or ""] for found in per_instance]
            unique = count > 1 and _unique_within_groups(column, groups)
            role = "identity" if unique else "attribute"
        if once and not nil and role in ("identity", "attribute"):
            single[concept] = [_value(found[0]) or "" for found in per_instance]
        roles[concept] = role
        children.append(
            TupleChild(
                concept=concept,
                role=role,
                occurrences=len(facts),
                instances=len(per_instance),
                maxPerInstance=max(len(found) for found in per_instance),
                distinct=len(distinct),
                nil=nil,
                units=units,
                sample=tuple(v[:_SAMPLE_WIDTH] for v in distinct[:_SAMPLE]),
            )
        )

    identity = tuple(c for c in order if roles[c] == "identity")
    pairs: tuple[tuple[str, str], ...] = ()
    if not identity and count > 1:
        candidates = [c for c in order if roles[c] == "attribute" and c in single]
        pairs = tuple(
            (a, b)
            for a, b in combinations(candidates, 2)
            if _unique_within_groups(
                [[x, y] for x, y in zip(single[a], single[b], strict=True)], groups
            )
        )
    return TupleKind(
        concept=name,
        parents=tuple(draft.parents),
        instances=count,
        contextGroups=len(set(groups)),
        children=tuple(children),
        identity=identity,
        identityPairs=pairs,
        identityUnclear=not identity and not pairs,
    )


def _kinds(modelXbrl: ModelXbrl) -> list[tuple[Any, TupleKind]]:
    drafts = _collect(list(modelXbrl.facts))
    return [(draft.qname, _kind(name, draft)) for name, draft in drafts.items()]


def tuple_kinds(modelXbrl: ModelXbrl) -> list[TupleKind]:
    """The tuple kinds of the report, in the order of the document."""
    return [kind for _, kind in _kinds(modelXbrl)]


def tuple_diagnostics(modelXbrl: ModelXbrl) -> Iterator[ArelleDiagnostic]:
    """One information diagnostic for each tuple kind of the report."""
    for qname, kind in _kinds(modelXbrl):
        yield ArelleDiagnostic.info(
            "A tuple kind of the report",
            concepts=(qname,),
            kind=TUPLE_KIND,
            tuple=kind,
        )


def as_tuple_kind(diagnostic: ArelleDiagnostic) -> TupleKind | None:
    """The kind a `tuple_diagnostics` diagnostic carries; None for any other."""
    if diagnostic.details.get("kind") != TUPLE_KIND:
        return None
    found = diagnostic.details["tuple"]
    return found if isinstance(found, TupleKind) else None
