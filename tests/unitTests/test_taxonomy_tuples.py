"""Unit tests for the optional top-level "tuples" section (see
mireport.arelle.taxonomy_extraction.extractTuples(), which writes it), exposed
through Taxonomy.tupleConcepts.

Tuples are not modelled: the section names the tuple concepts so that a reader
can recognise them (a tuple is not a concept of the taxonomy), nothing more.
"""

from __future__ import annotations

from typing import Any

import pytest

from mireport.taxonomy import Taxonomy

_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"


def _concept() -> dict[str, Any]:
    return {
        "labels": {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": "xbrli:stringItemType",
        "baseDataType": "xbrli:stringItemType",
        "periodType": "duration",
        "numeric": False,
    }


def _bits(tuples: list[str] | None) -> dict[str, Any]:
    bits: dict[str, Any] = {
        "entryPoint": "test://tuples",
        "namespaces": {"ext": "https://example.com/ext"},
        "concepts": {"ext:Item": _concept()},
        "presentation": {},
        "dimensions": {},
    }
    if tuples is not None:
        bits["tuples"] = tuples
    return bits


class TestTupleConcepts:
    def test_names_the_tuple_concepts(self) -> None:
        taxonomy = Taxonomy.fromJSON(_bits(["ext:Row", "ext:Table"]))
        assert {q.localName for q in taxonomy.tupleConcepts} == {"Row", "Table"}

    def test_a_tuple_is_not_a_concept(self) -> None:
        taxonomy = Taxonomy.fromJSON(_bits(["ext:Row"]))
        (row,) = taxonomy.tupleConcepts
        assert row.namespace == "https://example.com/ext"
        with pytest.raises(KeyError):
            taxonomy.getConcept(row)

    def test_absent_means_no_tuples(self) -> None:
        assert Taxonomy.fromJSON(_bits(None)).tupleConcepts == frozenset()

    def test_empty_means_no_tuples(self) -> None:
        assert Taxonomy.fromJSON(_bits([])).tupleConcepts == frozenset()
