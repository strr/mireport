"""ArelleReportProcessor.validateFile: Arelle opening a file itself, as arelleCmdLine -f does, rather
than a report package built in memory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.offline_arelle import offlineArelle, taxonomyPackagesDir

from mireport.conversionresults import Severity

pytestmark = pytest.mark.slow


def _problems(result) -> list[str]:
    return [m.messageText for m in result.messages if m.severity is not Severity.INFO]


def test_a_taxonomy_package_validates_as_one() -> None:
    package = next(taxonomyPackagesDir().glob("country-current-*.zip"))
    result = offlineArelle().validateFile(package, taxonomyPackage=True)
    assert result.validated
    assert not _problems(result)


def test_a_broken_report_is_reported(tmp_path: Path) -> None:
    report = tmp_path / "broken.json"
    report.write_text(
        json.dumps(
            {
                "documentInfo": {
                    "documentType": "https://xbrl.org/2021/xbrl-json",
                    "taxonomy": ["https://example.com/no-such-taxonomy.xsd"],
                },
                "facts": {},
            }
        ),
        encoding="utf-8",
    )
    result = offlineArelle().validateFile(report)
    assert _problems(result)
