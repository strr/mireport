"""Summation-item arcs under either arcrole -- XBRL 2.1's 2003 one or
Calculations 1.1's 2023 one -- bake into the same "calculation" section, with
the arcrole the DTS uses recorded once for the whole taxonomy.

Runs real Arelle over a tiny hand-written DTS: what is under test is how
Arelle's own relationship sets combine the two arcroles (one set per ELR,
each total's arcs together and in arc order across both), which a stub
cannot faithfully stand in for.

    income:   Profit = Revenue - Costs
    revenue:  Revenue = ProductSales + 0.5 * ServiceSales
"""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from arelle import XbrlConst

from mireport.arelle.diagnostics import ArelleDiagnostic
from mireport.arelle.taxonomy_info import callArelleForTaxonomyInfo
from mireport.conversionresults import Severity
from mireport.taxonomy import CalculationArcrole, Taxonomy

NS = "https://example.com/2026/calc"
INCOME = f"{NS}/role/income"
REVENUE = f"{NS}/role/revenue"
XBRL21 = XbrlConst.summationItem
CALC11 = XbrlConst.summationItem11
NAMES = ("Profit", "Revenue", "Costs", "ProductSales", "ServiceSales")

SCHEMA = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:xbrli="http://www.xbrl.org/2003/instance"
           xmlns:link="http://www.xbrl.org/2003/linkbase"
           xmlns:xlink="http://www.w3.org/1999/xlink"
           xmlns:calc="{ns}"
           targetNamespace="{ns}" elementFormDefault="qualified">
  <xs:annotation>
    <xs:appinfo>
      <link:roleType roleURI="{income}" id="income">
        <link:usedOn>link:calculationLink</link:usedOn>
      </link:roleType>
      <link:roleType roleURI="{revenue}" id="revenue">
        <link:usedOn>link:calculationLink</link:usedOn>
      </link:roleType>
      <link:linkbaseRef xlink:type="simple" xlink:href="cal.xml"
          xlink:role="http://www.xbrl.org/2003/role/calculationLinkbaseRef"
          xlink:arcrole="http://www.w3.org/1999/xlink/properties/linkbase"/>
    </xs:appinfo>
  </xs:annotation>
  <xs:import namespace="http://www.xbrl.org/2003/instance"
             schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"/>
  {elements}
</xs:schema>
"""

ELEMENT = """<xs:element name="{name}" id="{name}" type="xbrli:monetaryItemType"
      substitutionGroup="xbrli:item" nillable="true" xbrli:periodType="duration"/>"""

LINKBASE = """<?xml version="1.0" encoding="UTF-8"?>
<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
               xmlns:xlink="http://www.w3.org/1999/xlink">
  <link:roleRef roleURI="{income}" xlink:type="simple" xlink:href="calc.xsd#income"/>
  <link:roleRef roleURI="{revenue}" xlink:type="simple" xlink:href="calc.xsd#revenue"/>
  {arcroleRef}
  {links}
</link:linkbase>
"""

CALC11_ARCROLE_REF = (
    f'<link:arcroleRef arcroleURI="{CALC11}" xlink:type="simple"'
    ' xlink:href="https://www.xbrl.org/2023/calculation-1.1.xsd#summation-item"/>'
)


def calculationLink(
    role: str, arcrole: str, total: str, items: list[tuple[str, float, float]]
) -> str:
    locs = "".join(
        f'<link:loc xlink:type="locator" xlink:href="calc.xsd#{name}" xlink:label="{name}"/>'
        for name in [total, *(item for item, _, _ in items)]
    )
    arcs = "".join(
        f'<link:calculationArc xlink:type="arc" xlink:arcrole="{arcrole}"'
        f' xlink:from="{total}" xlink:to="{item}" weight="{weight}" order="{order}"/>'
        for item, weight, order in items
    )
    return (
        f'<link:calculationLink xlink:type="extended" xlink:role="{role}">'
        f"{locs}{arcs}</link:calculationLink>"
    )


def writeDts(tmp_path: Path, links: list[str]) -> Path:
    (tmp_path / "cal.xml").write_text(
        LINKBASE.format(
            income=INCOME,
            revenue=REVENUE,
            arcroleRef=CALC11_ARCROLE_REF
            if any(CALC11 in link for link in links)
            else "",
            links="\n  ".join(links),
        ),
        "utf-8",
    )
    entry = tmp_path / "calc.xsd"
    entry.write_text(
        SCHEMA.format(
            ns=NS,
            income=INCOME,
            revenue=REVENUE,
            elements="\n  ".join(ELEMENT.format(name=name) for name in NAMES),
        ),
        "utf-8",
    )
    return entry


def singleArcroleLinks(arcrole: str) -> list[str]:
    return [
        calculationLink(
            INCOME, arcrole, "Profit", [("Revenue", 1, 1), ("Costs", -1, 2)]
        ),
        calculationLink(
            REVENUE,
            arcrole,
            "Revenue",
            [("ProductSales", 1, 1), ("ServiceSales", 0.5, 2)],
        ),
    ]


def bake(tmp_path: Path, entry: Path) -> tuple[dict[str, Any], list[ArelleDiagnostic]]:
    output = tmp_path / "taxonomy.json"
    result = callArelleForTaxonomyInfo(str(entry), [], output)
    assert output.exists(), "\n".join(result.log_lines)
    errors = [m.messageText for m in result.messages if m.severity is Severity.ERROR]
    assert errors == []
    baked: dict[str, Any] = json.loads(output.read_text("utf-8"))
    return baked, result.diagnostics


def arcs(baked: dict[str, Any]) -> dict[str, list[tuple[str, str, float, float]]]:
    return {
        elr: [
            (r["source"], r["target"], r["weight"], r["order"])
            for r in network["relationships"]
        ]
        for elr, network in baked["calculation"].items()
    }


EXPECTED = {
    INCOME: [
        ("calc:Profit", "calc:Revenue", 1.0, 1.0),
        ("calc:Profit", "calc:Costs", -1.0, 2.0),
    ],
    REVENUE: [
        ("calc:Revenue", "calc:ProductSales", 1.0, 1.0),
        ("calc:Revenue", "calc:ServiceSales", 0.5, 2.0),
    ],
}


def calculationDiagnostics(
    diagnostics: list[ArelleDiagnostic],
) -> list[ArelleDiagnostic]:
    return [d for d in diagnostics if "summation-item" in d.text]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("arcrole", "expected"),
    [(XBRL21, CalculationArcrole.Xbrl21), (CALC11, CalculationArcrole.Calculations11)],
)
def test_either_arcrole_bakes_the_same_networks(
    tmp_path: Path, arcrole: str, expected: CalculationArcrole
) -> None:
    baked, diagnostics = bake(tmp_path, writeDts(tmp_path, singleArcroleLinks(arcrole)))
    assert arcs(baked) == EXPECTED
    assert baked["calculationArcrole"] == arcrole
    assert calculationDiagnostics(diagnostics) == []

    # These DTSs have no presentation linkbase, which Taxonomy.fromJSON()
    # requires.
    taxonomy = Taxonomy.fromJSON({"presentation": {}, **baked})
    assert taxonomy.calculationArcrole is expected
    assert [g.roleUri for g in taxonomy.calculation] == [INCOME, REVENUE]


@pytest.mark.integration
def test_mixed_arcroles_bake_every_arc_and_warn(tmp_path: Path) -> None:
    # One total's arcs split across both arcroles within one ELR -- the 2003
    # link written first but holding the later-ordered arc -- and a second
    # ELR wholly under 1.1.
    links = [
        calculationLink(INCOME, XBRL21, "Profit", [("Costs", -1, 2)]),
        calculationLink(INCOME, CALC11, "Profit", [("Revenue", 1, 1)]),
        calculationLink(
            REVENUE,
            CALC11,
            "Revenue",
            [("ProductSales", 1, 1), ("ServiceSales", 0.5, 2)],
        ),
    ]
    baked, diagnostics = bake(tmp_path, writeDts(tmp_path, links))
    assert arcs(baked) == EXPECTED
    assert baked["calculationArcrole"] == CALC11
    [warning] = calculationDiagnostics(diagnostics)
    assert warning.level == logging.WARNING
    assert warning.details == {
        "xbrl21Arcs": 1,
        "calculations11Arcs": 3,
        "xbrl21Elrs": [INCOME],
        "calculations11Elrs": [INCOME, REVENUE],
    }
