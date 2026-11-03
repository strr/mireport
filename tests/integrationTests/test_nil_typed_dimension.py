"""A nil typed dimension member, end to end: xBRL-JSON's null read, written to Inline XBRL as an
empty wrapper element with xsi:nil, and read back by Arelle as null again.

A closed hypercube's typed dimensions have no default, so a fact that does not use one of them can
only be valid with that dimension given a nil member (ESRS's GHG tables need it). The VSME coverage
report has typed dimensions with nillable wrappers, so one of its facts stands in."""

from __future__ import annotations

import json
from typing import Any

import pytest
from tests.offline_arelle import offlineArelle

from mireport.arelle.report_info import packageReportDocument
from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.coverage_report_generator._sampling import SampleEntityPeriod
from mireport.coverage_report_generator.report_writer import buildCoverageReportSet
from mireport.taxonomy import (
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
    loadBuiltInTaxonomyJSON,
)
from mireport.xbrljson_reader import XbrlJsonProcessor

pytestmark = pytest.mark.slow

SAMPLE = SampleEntityPeriod(
    entity="lei:529900T8BM49AURSDO55",
    entityPrefix="lei",
    entityNamespace="http://standards.iso.org/iso/17442",
    periodInstant="2026-01-01T00:00:00",
    periodDuration="2025-01-01T00:00:00/2026-01-01T00:00:00",
)


def _taxonomy() -> Taxonomy:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    return getTaxonomy(
        next(ep for ep in listTaxonomies() if any("vsme" in uri.lower() for uri in ep))
    )


def _typedAxes(taxonomy: Taxonomy, fact: dict[str, Any]) -> list[str]:
    return [
        n
        for n in fact["dimensions"]
        if ":" in n and taxonomy.getConcept(n).isTypedDimension
    ]


def test_a_nil_typed_member_survives_inline_xbrl_and_arelle() -> None:
    taxonomy = _taxonomy()
    reports = buildCoverageReportSet(taxonomy, SAMPLE, sorted(taxonomy.entryPointSet))
    document = reports.documents["all-values.json"].toDict()
    # One fact, its typed member made nil: the only fact in its context, so Arelle's output can
    # be searched for it by concept and the nil dimension.
    factId, fact = next(
        (i, f) for i, f in document["facts"].items() if _typedAxes(taxonomy, f)
    )
    axis = _typedAxes(taxonomy, fact)[0]
    fact["dimensions"][axis] = None

    results = ConversionResultsBuilder(consoleOutput=False)
    processor = XbrlJsonProcessor(document, results, strict=True)
    report = processor.createReport()
    assert processor.factsSkipped == 0
    inline = report.getInlineReport()
    assert b'xsi:nil="true"/></xbrldi:typedMember>' in inline.fileContent

    result = offlineArelle().generateXbrlJson(packageReportDocument(inline))
    # The coverage report's values are samples, not sums, so Arelle's calculation checks find
    # fault with them; what this is about is dimensions and the round trip.
    problems = [
        m.messageText
        for m in result.messages
        if m.severity in (Severity.ERROR, Severity.WARNING)
        and not str(m.messageText).startswith("[calc11e:")
    ]
    assert result.has_json and not problems, problems
    back = json.loads(result.xbrl_json.fileContent)["facts"].values()
    concept = fact["dimensions"]["concept"]
    assert any(
        f["dimensions"]["concept"] == concept
        and axis in f["dimensions"]
        and f["dimensions"][axis] is None
        for f in back
    ), f"{factId}: no {concept} with {axis} nil in Arelle's xBRL-JSON"
