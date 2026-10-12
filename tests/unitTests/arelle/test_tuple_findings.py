"""Unit tests for mireport.arelle.tuple_findings: what an XBRL 2.1 report's tuples are made of.

Arelle's xBRL-JSON has no tuple children, so the plug-in, which sees the model, reads them:
for each tuple kind, its child concepts and, from the values across its instances, which
children could identify a row (they differ from one instance to the next within a context)
and which are measures. The fixtures are small hand-built models.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from mireport.arelle.diagnostics import ArelleDiagnostic
from mireport.arelle.support import ArelleProcessingResult
from mireport.arelle.tuple_findings import (
    TupleChild,
    TupleKind,
    as_tuple_kind,
    tuple_diagnostics,
    tuple_kinds,
)


def item(
    concept: str,
    value: str | None,
    *,
    context: str = "c1",
    unit: str | None = None,
    numeric: bool = False,
) -> Any:
    return SimpleNamespace(
        qname=f"ex:{concept}",
        isTuple=False,
        isItem=True,
        isNil=value is None,
        value=value,
        contextID=context,
        unitID=unit,
        concept=SimpleNamespace(isNumeric=numeric, isTuple=False),
        modelTupleFacts=[],
    )


def row(concept: str, *children: Any) -> Any:
    return SimpleNamespace(
        qname=f"ex:{concept}",
        isTuple=True,
        isItem=False,
        isNil=False,
        value=None,
        contextID=None,
        unitID=None,
        concept=SimpleNamespace(isNumeric=False, isTuple=True),
        modelTupleFacts=list(children),
    )


def model(*facts: Any) -> Any:
    return SimpleNamespace(facts=list(facts))


def investee(name: str, percent: str, country: str = "TW", context: str = "c1") -> Any:
    return row(
        "Investee",
        item("Name", name, context=context),
        item("Percent", percent, context=context, unit="pure", numeric=True),
        item("Country", country, context=context),
    )


def child(kind: TupleKind, concept: str) -> TupleChild:
    return next(c for c in kind.children if c.concept == f"ex:{concept}")


class TestTupleKinds:
    def test_no_tuples_gives_nothing(self) -> None:
        assert tuple_kinds(model(item("A", "1"))) == []
        assert list(tuple_diagnostics(model(item("A", "1")))) == []

    def test_a_kind_names_its_children_and_what_each_could_be(self) -> None:
        facts = [investee("Acme", "10"), investee("Bolt", "20"), investee("Cog", "30")]
        (kind,) = tuple_kinds(model(*facts))
        assert kind.concept == "ex:Investee"
        assert kind.instances == 3
        assert kind.parents == ()
        assert [c.concept for c in kind.children] == [
            "ex:Name",
            "ex:Percent",
            "ex:Country",
        ]
        assert child(kind, "Name").role == "identity"
        assert child(kind, "Percent").role == "measure"
        assert child(kind, "Country").role == "pinned"
        assert kind.identity == ("ex:Name",)
        assert kind.identityPairs == ()
        assert not kind.identityUnclear

    def test_a_child_counts_its_values_units_and_nils(self) -> None:
        facts = [
            investee("Acme", "10"),
            row(
                "Investee",
                item("Name", None),
                item("Percent", "20", unit="pure", numeric=True),
                item("Country", "JP"),
            ),
        ]
        (kind,) = tuple_kinds(model(*facts))
        name, percent = child(kind, "Name"), child(kind, "Percent")
        assert (name.occurrences, name.instances, name.distinct, name.nil) == (
            2,
            2,
            1,
            1,
        )
        assert percent.units == ("pure",)
        assert percent.sample == ("10", "20")
        assert child(kind, "Country").distinct == 2

    def test_a_varying_child_that_is_not_unique_is_an_attribute(self) -> None:
        facts = [investee("Acme", "10", "TW"), investee("Acme", "20", "JP")]
        (kind,) = tuple_kinds(model(*facts))
        assert child(kind, "Name").role == "pinned"  # one value in every row
        assert child(kind, "Country").role == "identity"

    def test_two_children_together_can_identify_a_row(self) -> None:
        facts = [
            investee("Acme", "10", "TW"),
            investee("Acme", "20", "JP"),
            investee("Bolt", "30", "TW"),
            investee("Bolt", "40", "JP"),
        ]
        (kind,) = tuple_kinds(model(*facts))
        assert kind.identity == ()
        assert kind.identityPairs == (("ex:Name", "ex:Country"),)
        assert child(kind, "Name").role == "attribute"
        assert not kind.identityUnclear

    def test_a_kind_nothing_identifies_is_unclear(self) -> None:
        facts = [investee("Acme", "10"), investee("Acme", "20"), investee("Acme", "30")]
        (kind,) = tuple_kinds(model(*facts))
        assert kind.identity == () and kind.identityPairs == ()
        assert kind.identityUnclear

    def test_identity_is_judged_within_a_context(self) -> None:
        """The same investee in two years is two rows of two contexts, not a repeated
        identity."""
        facts = [
            investee("Acme", "10", context="y1"),
            investee("Bolt", "20", context="y1"),
            investee("Acme", "15", context="y2"),
            investee("Bolt", "25", context="y2"),
        ]
        (kind,) = tuple_kinds(model(*facts))
        assert kind.contextGroups == 2
        assert kind.identity == ("ex:Name",)
        assert child(kind, "Name").distinct == 2

    def test_a_nested_tuple_is_its_own_kind_under_its_parent(self) -> None:
        facts = [
            row(
                "Group",
                item("GroupName", "G1"),
                row(
                    "Member", item("MemberName", "m1"), item("Share", "1", numeric=True)
                ),
                row(
                    "Member", item("MemberName", "m2"), item("Share", "2", numeric=True)
                ),
            ),
            row(
                "Group",
                item("GroupName", "G2"),
                row(
                    "Member", item("MemberName", "m3"), item("Share", "3", numeric=True)
                ),
            ),
        ]
        group, member = tuple_kinds(model(*facts))
        assert (group.concept, group.instances, group.parents) == ("ex:Group", 2, ())
        assert (member.concept, member.instances) == ("ex:Member", 3)
        assert member.parents == ("ex:Group",)
        tuple_child = child(group, "Member")
        assert tuple_child.role == "tuple"
        assert (tuple_child.occurrences, tuple_child.instances) == (3, 2)
        assert tuple_child.maxPerInstance == 2
        assert member.identity == ("ex:MemberName",)

    def test_a_sample_is_short_and_a_few(self) -> None:
        facts = [investee(f"{n}" * 100, "1") for n in range(6)]
        (kind,) = tuple_kinds(model(*facts))
        sample = child(kind, "Name").sample
        assert len(sample) == 3 and all(len(s) <= 40 for s in sample)

    def test_kinds_are_in_document_order(self) -> None:
        other = row("Guarantee", item("Amount", "5", numeric=True))
        kinds = tuple_kinds(model(other, investee("Acme", "1")))
        assert [k.concept for k in kinds] == ["ex:Guarantee", "ex:Investee"]


class TestDiagnostics:
    def test_one_diagnostic_per_kind_round_trips(self) -> None:
        facts = [investee("Acme", "10"), investee("Bolt", "20")]
        diagnostics = list(tuple_diagnostics(model(*facts)))
        assert len(diagnostics) == 1
        assert as_tuple_kind(diagnostics[0]) == tuple_kinds(model(*facts))[0]

    def test_another_diagnostic_is_not_a_tuple_kind(self) -> None:
        assert as_tuple_kind(ArelleDiagnostic.warning("something else")) is None

    def test_the_result_keeps_tuple_kinds_apart_from_other_diagnostics(self) -> None:
        other = ArelleDiagnostic.warning("something else")
        result = ArelleProcessingResult()
        result.addDiagnostics([*tuple_diagnostics(model(investee("A", "1"))), other])
        assert [k.concept for k in result.tuple_kinds] == ["ex:Investee"]
        assert result.diagnostics == [other]
