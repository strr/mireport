"""Facts whose taxonomy gives no hypercube in presentation still reach the report.

A taxonomy may define its hypercubes in the definition linkbase only, leaving its presentation
groups as plain lists. A list group used to drop every dimensionally qualified fact; they now
get a table per set of dimensions, built from the facts themselves.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from tests.unitTests.synthetic_taxonomy import (
    NAMESPACE,
    concept,
    cube,
    group_taxonomy_json,
    qname,
    taxonomy_json,
)

from mireport.exceptions import InlineReportException
from mireport.report import InlineReport
from mireport.report.factbuilder import FactBuilder
from mireport.taxonomy import Taxonomy
from mireport.xml import getBootstrapQNameMaker

_Q = getBootstrapQNameMaker()
_Q.addNamespacePrefix("utr", "http://www.xbrl.org/2009/utr")


@pytest.fixture
def taxonomy(register_taxonomy: Callable[[dict[str, Any]], Taxonomy]) -> Taxonomy:
    return register_taxonomy(group_taxonomy_json())


def _report(taxonomy: Taxonomy, *, orphan: bool = False) -> InlineReport:
    report = InlineReport(taxonomy)
    report.setEntity("http://standards.iso.org/iso/17442", "529900T8BM49AURSDO55")
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")

    def add(name: str, value: Any, **dims: str) -> None:
        concept = taxonomy.getConcept(name)
        fb = FactBuilder(report).setConcept(concept).setValue(value)
        if concept.isNumeric:
            fb.setSimpleUnit(_Q.fromString("utr:kg")).setDecimals(0)
        for axis, member in dims.items():
            dimension = taxonomy.getConcept(qname(axis))
            if dimension.isTypedDimension:
                fb.setTypedDimension(dimension, member)
            else:
                fb.setExplicitDimension(dimension, taxonomy.getConcept(member))
        report.addFact(fb.buildFact())

    add(qname("Emissions"), 10, ScopeAxis=qname("ScopeA"))
    add(qname("Emissions"), 20, ScopeAxis=qname("ScopeB"))
    add(qname("Total"), 30)
    add(qname("Intensity"), 5, TargetAxis="T1")
    add(qname("Intensity"), 7, TargetAxis="T2")
    add(qname("Note"), "note one", TargetAxis="T1")
    if orphan:
        add(qname("Orphan"), "lost")  # in no presentation group
    return report


def _render(report: InlineReport) -> str:
    return report.getInlineReport().fileContent.decode("utf-8")


def _tagged(html: str) -> list[str]:
    return re.findall(r'<ix:non(?:Fraction|Numeric)[^>]*name="([^"]+)"', html)


def test_every_fact_is_shown_though_presentation_has_no_hypercube(
    taxonomy: Taxonomy,
) -> None:
    report = _report(taxonomy)
    report.requireAllFactsRendered = True
    html = _render(report)
    assert sorted(_tagged(html)) == sorted(
        [qname("Emissions")] * 2
        + [qname("Total")]
        + [qname("Intensity")] * 2
        + [qname("Note")]
    )


def test_each_dimension_set_gets_its_own_titled_table(taxonomy: Taxonomy) -> None:
    html = _render(_report(taxonomy))
    titles = [t.strip() for t in re.findall(r"<h2[^>]*>\s*([^<]*)", html)]
    subheadings = re.findall(r'<h3 class="block-heading">\s*([^<]*)', html)
    # The group's own section, then one table per dimension set under the same title, shown
    # once: the tables have sub-headings instead, and no table-of-contents entries of their own.
    assert titles.count("Group") == 1
    assert subheadings == ["By Scope", "By Target"]
    assert html.count('<table class="thematic-table">') == 2


def test_the_smaller_axis_becomes_the_columns(taxonomy: Taxonomy) -> None:
    html = _render(_report(taxonomy))
    byScope, byTarget = re.findall(r"<table.*?</table>", html, re.DOTALL)
    # One concept across two scopes: the scopes are rows.
    assert re.findall(r'<th class="[^"]*">\s*(Scope [AB])', byScope) == [
        "Scope A",
        "Scope B",
    ]
    # Two concepts across two targets: concepts are rows, targets are columns.
    headings = re.findall(r"<th[^>]*>\s*([^<]*?)\s*</th>", byTarget)
    assert "T1" in headings and "T2" in headings
    assert "Intensity" in headings and "Note" in headings


def test_a_gap_in_the_grid_is_left_blank(taxonomy: Taxonomy) -> None:
    byTarget = re.findall(r"<table.*?</table>", _render(_report(taxonomy)), re.DOTALL)[
        1
    ]
    # Note has no fact for T2.
    assert byTarget.count("<td></td>") == 1


def test_a_fact_that_appears_nowhere_is_an_error_when_required(
    taxonomy: Taxonomy,
) -> None:
    report = _report(taxonomy, orphan=True)
    report.requireAllFactsRendered = True
    with pytest.raises(InlineReportException, match="appear nowhere"):
        _render(report)


def test_a_fact_that_appears_nowhere_is_left_out_by_default(
    taxonomy: Taxonomy,
) -> None:
    assert qname("Orphan") not in _tagged(_render(_report(taxonomy, orphan=True)))


def test_the_title_is_one_table_of_contents_entry(taxonomy: Taxonomy) -> None:
    html = _render(_report(taxonomy))
    toc = re.search(r'<ol class="toc-list">.*?</ol>', html, re.DOTALL)
    assert toc is not None
    assert re.findall(r"<a [^>]*>\s*([^<]*?)\s*</a>", toc.group(0)) == ["Group"]


LIST_ROLE = f"{NAMESPACE}/role/List"
CUBE_ROLE = f"{NAMESPACE}/role/Cube"


def _list_and_cube_taxonomy(*, presented: bool) -> dict[str, Any]:
    """A list group presenting Note, with no hypercube in presentation, and a group whose
    hypercube takes Note and Intensity by a typed target axis: a target's note belongs to the
    second. With ``presented``, the second group presents its hypercube; otherwise its hypercube
    is in the definition linkbase only and both groups are lists."""
    target = qname("TargetAxis")
    cubeRows = [[1, qname("Cube")], [2, target]] if presented else []
    return taxonomy_json(
        "test://synthetic/list-and-cube",
        concepts={
            qname("ListGroup"): concept("List group", abstract=True),
            qname("CubeGroup"): concept("Cube group", abstract=True),
            qname("Lines"): concept("Line items", abstract=True),
            qname("Intensity"): concept("Intensity", numeric=True),
            qname("Note"): concept("Note"),
            target: concept(
                "Target [Axis]",
                dimension=True,
                other={"typedElement": qname("TargetType")},
            ),
            qname("Cube"): concept("Target cube [Table]", hypercube=True),
            qname("PlainCube"): concept("Plain cube [Table]", hypercube=True),
        },
        xsElements={
            qname("TargetType"): {"dataType": "xs:string", "baseDataType": "xs:string"}
        },
        presentation={
            LIST_ROLE: {
                "definition": "1 List group",
                "rows": [[0, qname("ListGroup")], [1, qname("Note")]],
            },
            CUBE_ROLE: {
                "definition": "2 Cube group",
                "rows": [
                    [0, qname("CubeGroup")],
                    *cubeRows,
                    [1, qname("Lines")],
                    [2, qname("Intensity")],
                    [2, qname("Note")],
                ],
            },
        },
        # The list group's note has a cube of no dimensions, in the definition linkbase only.
        dimensions={
            LIST_ROLE: {qname("PlainCube"): cube([qname("Note")], {})},
            CUBE_ROLE: {
                qname("Cube"): cube([qname("Intensity"), qname("Note")], {}, [target])
            },
        },
    )


@pytest.mark.parametrize("presented", [True, False])
def test_a_target_fact_is_shown_once_in_the_group_whose_hypercube_takes_it(
    register_taxonomy: Callable[[dict[str, Any]], Taxonomy], presented: bool
) -> None:
    taxonomy = register_taxonomy(_list_and_cube_taxonomy(presented=presented))
    report = InlineReport(taxonomy)
    report.setEntity("http://standards.iso.org/iso/17442", "529900T8BM49AURSDO55")
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")
    for name, value, target in (
        ("Note", "note on the plan", None),
        ("Note", "note on target T1", "T1"),
        ("Intensity", 5, "T1"),
    ):
        concept_ = taxonomy.getConcept(qname(name))
        fb = FactBuilder(report).setConcept(concept_).setValue(value)
        if concept_.isNumeric:
            fb.setSimpleUnit(_Q.fromString("utr:kg")).setDecimals(0)
        if target:
            fb.setTypedDimension(taxonomy.getConcept(qname("TargetAxis")), target)
        report.addFact(fb.buildFact())
    report.requireAllFactsRendered = True

    html = _render(report)
    assert html.count("note on target T1") == 1
    assert html.count("note on the plan") == 1
    titles = [
        t.strip() for t in re.findall(r'<h2 id="section-class\d+">\s*([^<]*)', html)
    ]
    assert titles == ["1 List group", "2 Cube group"]
