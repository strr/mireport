"""An InlineReport's creation time: given, else SOURCE_DATE_EPOCH, else now. The report states it,
and its package's entries carry it."""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from io import BytesIO

import pytest

from mireport.filesupport import FilelikeAndFileName
from mireport.report.inlinereport import InlineReport
from mireport.taxonomy import (
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
    loadBuiltInTaxonomyJSON,
)


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    return getTaxonomy(
        next(ep for ep in listTaxonomies() if any("vsme" in d.lower() for d in ep))
    )


def test_given_wins_over_source_date_epoch(
    taxonomy: Taxonomy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    given = datetime(2023, 10, 9, tzinfo=UTC)
    assert InlineReport(taxonomy, creationDateTime=given).creationDateTime == given


def test_source_date_epoch_pins_it(
    taxonomy: Taxonomy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    assert InlineReport(taxonomy).creationDateTime == datetime(2026, 1, 1, tzinfo=UTC)


def test_otherwise_it_is_now(
    taxonomy: Taxonomy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    before = datetime.now(UTC)
    assert before <= InlineReport(taxonomy).creationDateTime <= datetime.now(UTC)


def test_its_package_carries_it(
    taxonomy: Taxonomy, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = InlineReport(
        taxonomy, creationDateTime=datetime(2023, 10, 9, 12, 30, tzinfo=UTC)
    )
    monkeypatch.setattr(
        InlineReport,
        "getInlineReport",
        lambda self: FilelikeAndFileName(fileContent=b"<html/>", filename="r.html"),
    )
    monkeypatch.setattr(
        InlineReport, "packageTopLevelName", property(lambda self: "R_2023")
    )
    with zipfile.ZipFile(BytesIO(report.getInlineReportPackage().fileContent)) as zf:
        assert {info.date_time for info in zf.infolist()} == {(2023, 10, 9, 12, 30, 0)}
