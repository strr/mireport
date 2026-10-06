"""Unit tests for report_info.py's ArelleReportProcessor helpers.

The Session-driving methods themselves (validateReportPackage, ...) are
exercised end-to-end by the integration tests; these tests cover the
option-building and response-handling helpers.
"""

import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from mireport.arelle.report_info import (
    ArelleReportProcessor,
    _singleFileFromResponseZip,
)
from mireport.arelle.support import ArelleRelatedException


def makeZip(entries: dict[str, bytes]) -> BytesIO:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return stream


class TestSingleFileFromResponseZip:
    def test_returns_single_entry_content(self) -> None:
        stream = makeZip({"foo.json": b'{"documentInfo": {}}'})
        assert _singleFileFromResponseZip(stream, "test") == b'{"documentInfo": {}}'

    def test_rewinds_before_reading(self) -> None:
        stream = makeZip({"foo.json": b"content"})
        stream.seek(0, 2)  # simulate a fully-written, unrewound stream
        assert _singleFileFromResponseZip(stream, "test") == b"content"

    def test_raises_on_empty_zip(self) -> None:
        with pytest.raises(ArelleRelatedException, match="test thing"):
            _singleFileFromResponseZip(makeZip({}), "test thing")

    def test_raises_on_multiple_entries(self) -> None:
        stream = makeZip({"foo.json": b"{}", "viewer.html": b"<html/>"})
        with pytest.raises(ArelleRelatedException, match="viewer.html"):
            _singleFileFromResponseZip(stream, "test thing")


class TestMakeOptions:
    def makeProcessor(self, **kwargs: Any) -> ArelleReportProcessor:
        return ArelleReportProcessor(
            taxonomyPackages=[Path("a.zip"), Path("b.zip")], **kwargs
        )

    def test_shared_defaults(self) -> None:
        options = self.makeProcessor()._makeOptions()
        assert options.internetConnectivity == "offline"
        assert options.keepOpen is True
        assert options.logFile == "logToBuffer"
        assert options.logFormat == "%(message)s"
        assert options.logPropagate is False
        assert options.packages == ["a.zip", "b.zip"]
        assert options.validate is True
        assert options.calcs == "c11r"
        assert options.utrValidate is True
        assert options.validateDuplicateFacts == "inconsistent"
        assert options.showOptions is False
        assert options.plugins is None

    def test_online_when_not_offline(self) -> None:
        options = self.makeProcessor(workOffline=False)._makeOptions()
        assert options.internetConnectivity == "online"

    def test_overrides(self) -> None:
        options = self.makeProcessor()._makeOptions(
            calcs="none",
            plugins="saveLoadableOIM",
            pluginOptions={"saveLoadableOIM": "out.json"},
        )
        assert options.calcs == "none"
        assert options.plugins == "saveLoadableOIM"
        # RuntimeOptions flattens pluginOptions into attributes via setattr
        assert options.saveLoadableOIM == "out.json"

    def test_formulas_run_by_default(self) -> None:
        assert self.makeProcessor()._makeOptions().formulaAction is None

    def test_formula_action_can_be_overridden(self) -> None:
        options = self.makeProcessor()._makeOptions(formulaAction="none")
        assert options.formulaAction == "none"

    def test_xbrl_json_generation_skips_formulas_but_report_validation_keeps_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        processor = self.makeProcessor()
        seen: list[Any] = []

        def fakeRun(source: Any, options: Any, responseZipStream: Any = None) -> Any:
            seen.append(options)
            raise StopIteration

        monkeypatch.setattr(processor, "_run", fakeRun)
        for call in (processor.generateXBRLJson, processor.validateReportPackage):
            with pytest.raises(StopIteration):
                call(object())  # type: ignore[arg-type]
        jsonOptions, validateOptions = seen
        assert jsonOptions.formulaAction == "none"
        assert validateOptions.formulaAction is None

    def test_xbrl_json_with_taxonomy_loads_both_plugins_in_one_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from mireport.arelle import taxonomy_info
        from mireport.arelle.diagnostics import DiagnosticCollector

        processor = self.makeProcessor()
        seen: list[Any] = []
        tokens: list[str] = []

        def fakeRun(source: Any, options: Any, responseZipStream: Any = None) -> Any:
            seen.append(options)
            tokens.append(options.diagnosticsToken)
            raise StopIteration

        monkeypatch.setattr(processor, "_run", fakeRun)
        target = tmp_path / "taxonomy.json"
        with pytest.raises(StopIteration):
            processor.generateXBRLJsonWithTaxonomy(object(), target)  # type: ignore[arg-type]
        (options,) = seen
        assert options.plugins == f"saveLoadableOIM|{taxonomy_info.__file__}"
        assert options.saveLoadableOIM == "report.json"
        assert options.taxonomyDataFile == str(target)
        assert options.formulaAction == "none"
        assert options.validate is True
        assert options.abortOnMajorError is True
        # The diagnostics collector is closed even when the run raises.
        assert not DiagnosticCollector.exists(tokens[0])
