"""Unit tests for calculation relationships: CalculationGroup and
CalculationRelationship, loaded from the optional top-level "calculation"
section (see mireport.arelle.taxonomy_extraction.extractCalculation(), which
writes it) and exposed through Taxonomy.calculation.

Built over hand-written taxonomy JSON via Taxonomy.fromJSON(), so nothing is
registered (see test_taxonomy_fromJSON.py) and no Arelle DTS is needed.
"""

from __future__ import annotations

from typing import Any

import pytest

from mireport.data import taxonomies
from mireport.json import getJsonFiles, getObject
from mireport.taxonomy import (
    CalculationArcrole,
    CalculationGroup,
    CalculationRelationship,
    Taxonomy,
)

_NS = "https://example.com/vsme"
_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"
_ROLE = "https://example.com/role/income"
_OTHER_ROLE = "https://example.com/role/revenue"
_XBRL21_ARCROLE = "http://www.xbrl.org/2003/arcrole/summation-item"
_CALC11_ARCROLE = "https://xbrl.org/2023/arcrole/summation-item"


def _concept() -> dict[str, Any]:
    return {
        "labels": {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": "xbrli:monetaryItemType",
        "baseDataType": "xbrli:monetaryItemType",
        "periodType": "duration",
        "numeric": True,
    }


_CONCEPTS = {
    f"vsme:{name}": _concept()
    for name in ("Profit", "Revenue", "Costs", "ProductSales", "ServiceSales")
}


def _rel(source: str, target: str, weight: float, order: float) -> dict[str, Any]:
    return {
        "source": f"vsme:{source}",
        "target": f"vsme:{target}",
        "weight": weight,
        "order": order,
    }


# _ROLE:        Profit = Revenue - Costs
# _OTHER_ROLE:  Revenue = ProductSales + 0.5 * ServiceSales
# Revenue is a contributing item in one ELR and a total in the other.
_CALCULATION = {
    _ROLE: {
        "relationships": [
            _rel("Profit", "Revenue", 1.0, 1.0),
            _rel("Profit", "Costs", -1.0, 2.0),
        ]
    },
    _OTHER_ROLE: {
        "relationships": [
            _rel("Revenue", "ProductSales", 1.0, 1.0),
            _rel("Revenue", "ServiceSales", 0.5, 2.0),
        ]
    },
}


def _bits(
    calculation: dict[str, Any] | None, arcrole: str | None = _XBRL21_ARCROLE
) -> dict[str, Any]:
    bits: dict[str, Any] = {
        "entryPoint": "test://calculation",
        "namespaces": {"vsme": _NS},
        "concepts": _CONCEPTS,
        "presentation": {},
        "dimensions": {},
    }
    if calculation is not None:
        bits["calculation"] = calculation
        if arcrole is not None:
            bits["calculationArcrole"] = arcrole
    return bits


class TestCalculation:
    def setup_method(self) -> None:
        self.taxonomy = Taxonomy.fromJSON(_bits(_CALCULATION))
        self.c = self.taxonomy.getConcept

    def group(self, role: str) -> CalculationGroup:
        [group] = [g for g in self.taxonomy.calculation if g.roleUri == role]
        return group

    def test_one_group_per_elr(self) -> None:
        assert [g.roleUri for g in self.taxonomy.calculation] == [_ROLE, _OTHER_ROLE]

    def test_relationships_keep_weight_sign_and_order(self) -> None:
        assert self.group(_ROLE) == CalculationGroup(
            roleUri=_ROLE,
            relationships=(
                CalculationRelationship(
                    _ROLE, self.c("vsme:Profit"), self.c("vsme:Revenue"), 1.0, 1.0
                ),
                CalculationRelationship(
                    _ROLE, self.c("vsme:Profit"), self.c("vsme:Costs"), -1.0, 2.0
                ),
            ),
        )

    def test_weight_other_than_plus_or_minus_one_survives(self) -> None:
        [serviceSales] = [
            rel
            for rel in self.group(_OTHER_ROLE).relationships
            if rel.target == self.c("vsme:ServiceSales")
        ]
        assert serviceSales.weight == 0.5

    def test_items_are_per_elr(self) -> None:
        revenue = self.c("vsme:Revenue")
        # Revenue is only a contributing item in _ROLE...
        assert self.group(_ROLE).getItems(revenue) == ()
        assert self.group(_ROLE).totals == {self.c("vsme:Profit")}
        # ...and a total in _OTHER_ROLE, with its items in arc order.
        assert [
            (r.target, r.weight, r.order)
            for r in self.group(_OTHER_ROLE).getItems(revenue)
        ] == [
            (self.c("vsme:ProductSales"), 1.0, 1.0),
            (self.c("vsme:ServiceSales"), 0.5, 2.0),
        ]

    def test_relationships_carry_their_elr(self) -> None:
        for group in self.taxonomy.calculation:
            assert {rel.roleUri for rel in group.relationships} == {group.roleUri}

    def test_json_is_not_modified(self) -> None:
        bits = _bits(_CALCULATION)
        Taxonomy.fromJSON(bits)
        assert bits["calculation"] == _CALCULATION


class TestCalculationArcrole:
    @pytest.mark.parametrize(
        ("arcrole", "expected"),
        [
            (_XBRL21_ARCROLE, CalculationArcrole.Xbrl21),
            (_CALC11_ARCROLE, CalculationArcrole.Calculations11),
        ],
    )
    def test_one_arcrole_for_the_whole_model(
        self, arcrole: str, expected: CalculationArcrole
    ) -> None:
        taxonomy = Taxonomy.fromJSON(_bits(_CALCULATION, arcrole))
        assert taxonomy.calculationArcrole is expected
        assert taxonomy.calculationArcrole == arcrole

    def test_either_arcrole_loads_the_same_groups(self) -> None:
        def shape(taxonomy: Taxonomy) -> list[Any]:
            return [
                (g.roleUri, [(r.source.qname, r.target.qname) for r in g.relationships])
                for g in taxonomy.calculation
            ]

        assert shape(Taxonomy.fromJSON(_bits(_CALCULATION, _XBRL21_ARCROLE))) == shape(
            Taxonomy.fromJSON(_bits(_CALCULATION, _CALC11_ARCROLE))
        )

    def test_json_baked_before_the_arcrole_was_recorded_is_xbrl_2_1(self) -> None:
        # Such JSON only ever held 2003 summation-item arcs: the extractor
        # ignored Calculations 1.1 ones until the arcrole was recorded.
        taxonomy = Taxonomy.fromJSON(_bits(_CALCULATION, arcrole=None))
        assert taxonomy.calculationArcrole is CalculationArcrole.Xbrl21

    def test_no_calculation_has_no_arcrole(self) -> None:
        assert Taxonomy.fromJSON(_bits(None)).calculationArcrole is None
        assert Taxonomy.fromJSON(_bits({})).calculationArcrole is None

    def test_unknown_arcrole_raises(self) -> None:
        with pytest.raises(ValueError, match="calculationArcrole"):
            Taxonomy.fromJSON(
                _bits(_CALCULATION, "https://example.com/arcrole/summation-item")
            )


class TestNoCalculation:
    def test_absent_section_gives_no_groups(self) -> None:
        assert Taxonomy.fromJSON(_bits(None)).calculation == ()

    def test_empty_section_gives_no_groups(self) -> None:
        assert Taxonomy.fromJSON(_bits({})).calculation == ()

    def test_unknown_concept_raises(self) -> None:
        with pytest.raises(KeyError):
            Taxonomy.fromJSON(
                _bits({_ROLE: {"relationships": [_rel("Profit", "Nope", 1.0, 1.0)]}})
            )

    def test_built_in_taxonomies_load(self) -> None:
        """Every built-in taxonomy JSON still loads (none of them may carry a
        "calculation" section, if baked before calculations were extracted,
        or if the taxonomy simply has no calculation linkbase)."""
        for path in getJsonFiles(taxonomies):
            taxonomy = Taxonomy.fromJSON(getObject(path))
            for group in taxonomy.calculation:
                assert all(rel.weight != 0 for rel in group.relationships)
