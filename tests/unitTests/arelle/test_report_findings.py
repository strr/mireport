"""Unit tests for mireport.arelle.report_findings: what Arelle found wrong with a report's facts.

A fact whose value fails its datatype is written to xBRL-JSON with a null value, the same as
a nil fact. The plug-in sees the model, so it says which facts those are.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from arelle import XmlValidateConst

from mireport.arelle.diagnostics import ArelleDiagnostic
from mireport.arelle.report_findings import (
    InvalidFact,
    invalid_fact_diagnostics,
)
from mireport.arelle.support import ArelleProcessingResult


def fact(
    *,
    id: str | None = None,
    index: int = 1,
    valid: int = XmlValidateConst.VALID,
    nil: bool = False,
    item: bool = True,
    value: str = "x",
    concept: str = "Thing",
    dataType: str = "xbrli:stringItemType",
) -> Any:
    return SimpleNamespace(
        id=id,
        objectIndex=index,
        xValid=valid,
        isNil=nil,
        isItem=item,
        value=value,
        qname=f"ex:{concept}",
        concept=SimpleNamespace(typeQname=dataType),
    )


def model(*facts: Any) -> Any:
    return SimpleNamespace(factsInInstance=list(facts))


class TestInvalidFactDiagnostics:
    def test_a_fact_that_failed_its_datatype_is_reported(self) -> None:
        bad = fact(id="f9", valid=XmlValidateConst.INVALID, value="AC1830153")
        (diagnostic,) = invalid_fact_diagnostics(model(bad))
        assert isinstance(diagnostic, ArelleDiagnostic)
        assert diagnostic.details["kind"] == "invalid-fact-value"
        assert diagnostic.details["factId"] == "f9"
        assert diagnostic.details["value"] == "AC1830153"
        assert diagnostic.details["dataType"] == "xbrli:stringItemType"
        assert list(map(str, diagnostic.concepts)) == ["ex:Thing"]

    def test_a_fact_without_an_id_is_named_as_the_xbrl_json_names_it(self) -> None:
        bad = fact(id=None, index=17, valid=XmlValidateConst.INVALID)
        (diagnostic,) = invalid_fact_diagnostics(model(bad))
        assert diagnostic.details["factId"] == "f17"

    def test_valid_nil_and_non_item_facts_are_not_reported(self) -> None:
        facts = [
            fact(index=1),
            fact(index=2, valid=XmlValidateConst.INVALID, nil=True),
            fact(index=3, valid=XmlValidateConst.INVALID, item=False),
        ]
        assert list(invalid_fact_diagnostics(model(*facts))) == []

    def test_reports_come_in_document_order(self) -> None:
        facts = [
            fact(index=n, valid=XmlValidateConst.INVALID, concept=f"C{n}")
            for n in (30, 5, 12)
        ]
        found = [d.details["factId"] for d in invalid_fact_diagnostics(model(*facts))]
        assert found == ["f5", "f12", "f30"]

    def test_it_is_a_warning(self) -> None:
        bad = fact(valid=XmlValidateConst.INVALID)
        (diagnostic,) = invalid_fact_diagnostics(model(bad))
        assert diagnostic.level >= 30  # logging.WARNING


class TestProcessingResultKeepsThemApart:
    def test_invalid_facts_are_typed_and_not_among_the_diagnostics(self) -> None:
        bad = fact(id="f9", valid=XmlValidateConst.INVALID, value="AC1")
        other = ArelleDiagnostic.warning("Something else")
        result = ArelleProcessingResult()
        result.addDiagnostics([*invalid_fact_diagnostics(model(bad)), other])
        assert result.invalid_facts == [
            InvalidFact(
                id="f9",
                concept="ex:Thing",
                value="AC1",
                dataType="xbrli:stringItemType",
            )
        ]
        assert result.diagnostics == [other]
