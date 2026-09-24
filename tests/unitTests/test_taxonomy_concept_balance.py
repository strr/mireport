"""Unit tests for Concept.balance: a monetary concept's xbrli:balance, loaded
from the optional "balance" key of its taxonomy JSON entry (see
mireport.arelle.taxonomy_extraction.addConceptMetadata(), which writes it
only when the concept declares one).

Built over hand-written taxonomy JSON via Taxonomy.fromJSON(), so nothing is
registered (see test_taxonomy_fromJSON.py) and no Arelle DTS is needed.
"""

from __future__ import annotations

from typing import Any

import pytest

from mireport.data import taxonomies
from mireport.json import getJsonFiles, getObject
from mireport.taxonomy import Balance, Taxonomy

_NS = "https://example.com/vsme"
_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"


def _concept(
    *,
    monetary: bool = False,
    balance: str | None = None,
) -> dict[str, Any]:
    itemType = "xbrli:monetaryItemType" if monetary else "xbrli:stringItemType"
    jconcept: dict[str, Any] = {
        "labels": {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": itemType,
        "baseDataType": itemType,
        "periodType": "duration",
    }
    if monetary:
        jconcept["numeric"] = True
    if balance is not None:
        jconcept["balance"] = balance
    return jconcept


def _taxonomy(concepts: dict[str, dict[str, Any]]) -> Taxonomy:
    return Taxonomy.fromJSON(
        {
            "entryPoint": "test://concept-balance",
            "namespaces": {"vsme": _NS},
            "concepts": concepts,
            "presentation": {},
            "dimensions": {},
        }
    )


class TestConceptBalance:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("debit", Balance.Debit), ("credit", Balance.Credit)],
    )
    def test_declared_balance_is_loaded(self, value: str, expected: Balance) -> None:
        taxonomy = _taxonomy({"vsme:Revenue": _concept(monetary=True, balance=value)})
        concept = taxonomy.getConcept("vsme:Revenue")
        assert concept.balance is expected
        # StrEnum: still compares equal to (and serialises as) the JSON value.
        assert concept.balance == value

    def test_monetary_concept_without_balance_is_none(self) -> None:
        taxonomy = _taxonomy({"vsme:Amount": _concept(monetary=True)})
        assert taxonomy.getConcept("vsme:Amount").balance is None

    def test_non_monetary_concept_is_none(self) -> None:
        taxonomy = _taxonomy({"vsme:Name": _concept()})
        assert taxonomy.getConcept("vsme:Name").balance is None

    @pytest.mark.parametrize("value", ["Debit", "sideways"])
    def test_unknown_balance_is_rejected(self, value: str) -> None:
        with pytest.raises(ValueError):
            _taxonomy({"vsme:Revenue": _concept(monetary=True, balance=value)})

    def test_built_in_taxonomies_load(self) -> None:
        """Every built-in taxonomy JSON still loads, and any balance it does
        carry is one of the two Balance values (none of them may carry any
        yet, if baked before balance was extracted -- that is fine too)."""
        for path in getJsonFiles(taxonomies):
            taxonomy = Taxonomy.fromJSON(getObject(path))
            for concept in taxonomy.concepts:
                assert concept.balance is None or isinstance(concept.balance, Balance)
