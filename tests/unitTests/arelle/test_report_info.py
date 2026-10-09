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

from mireport.arelle.capabilities import ArelleCapability, MissingPluginError
from mireport.arelle.report_info import (
    ArelleReportProcessor,
    _singleFileFromResponseZip,
)
from mireport.arelle.support import ArelleRelatedException
from mireport.filesupport import FilelikeAndFileName


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


def plainPackage() -> FilelikeAndFileName:
    return FilelikeAndFileName(fileContent=makeZip({}).getvalue(), filename="r.xbri")


def inlinePackage(*, sec: bool = False, documents: int = 1) -> FilelikeAndFileName:
    ixt = (
        "http://www.sec.gov/inlineXBRL/transformation/2015-08-31"
        if sec
        else "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
    )
    doc = (
        f'<html xmlns="http://www.w3.org/1999/xhtml" '
        f'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:ixt="{ixt}"/>'
    ).encode()
    stream = makeZip({f"r/reports/{n}.htm": doc for n in range(documents)})
    return FilelikeAndFileName(fileContent=stream.getvalue(), filename="r.xbri")


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
                call(plainPackage())
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
            processor.generateXBRLJsonWithTaxonomy(plainPackage(), target)
        (options,) = seen
        assert options.plugins == f"saveLoadableOIM|{taxonomy_info.__file__}"
        assert options.saveLoadableOIM == "report.json"
        assert options.taxonomyDataFile == str(target)
        assert options.checkBaseHygiene is False
        assert options.formulaAction == "none"
        assert options.validate is True
        assert options.abortOnMajorError is True
        # The diagnostics collector is closed even when the run raises.
        assert not DiagnosticCollector.exists(tokens[0])


class TestCapabilitiesBecomePlugins:
    def makeProcessor(self, **kwargs: Any) -> ArelleReportProcessor:
        return ArelleReportProcessor(taxonomyPackages=[Path("a.zip")], **kwargs)

    def optionsSeen(
        self,
        monkeypatch: pytest.MonkeyPatch,
        processor: ArelleReportProcessor,
        call: str,
        source: Any,
    ) -> Any:
        seen: list[Any] = []

        def fakeRun(source: Any, options: Any, responseZipStream: Any = None) -> Any:
            seen.append(options)
            raise StopIteration

        monkeypatch.setattr(processor, "_run", fakeRun)
        with pytest.raises(StopIteration):
            getattr(processor, call)(
                source, *([Path("t.json")] if call.endswith("Taxonomy") else [])
            )
        (options,) = seen
        return options

    @pytest.mark.parametrize("call", ["generateXBRLJson", "validateReportPackage"])
    def test_a_document_set_gets_the_document_set_plugin(
        self, monkeypatch: pytest.MonkeyPatch, call: str
    ) -> None:
        options = self.optionsSeen(
            monkeypatch, self.makeProcessor(), call, inlinePackage(documents=2)
        )
        assert "inlineXbrlDocumentSet" in (options.plugins or "").split("|")

    def test_detected_capabilities_add_to_the_json_and_taxonomy_plugins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from mireport.arelle import taxonomy_info

        edgar = tmp_path / "EDGAR" / "transform"
        edgar.mkdir(parents=True)
        (edgar / "__init__.py").write_text("")
        options = self.optionsSeen(
            monkeypatch,
            self.makeProcessor(pluginDirectory=tmp_path),
            "generateXBRLJsonWithTaxonomy",
            inlinePackage(sec=True),
        )
        assert options.plugins.split("|") == [
            "saveLoadableOIM",
            taxonomy_info.__file__,
            str(edgar),
        ]

    def test_a_plain_report_gets_no_extra_plugins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        options = self.optionsSeen(
            monkeypatch, self.makeProcessor(), "validateReportPackage", inlinePackage()
        )
        assert not options.plugins

    def test_explicit_capabilities_replace_detection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        processor = self.makeProcessor(capabilities=[])
        options = self.optionsSeen(
            monkeypatch, processor, "validateReportPackage", inlinePackage(documents=2)
        )
        assert not options.plugins

    def test_the_plugin_directory_comes_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        edgar = tmp_path / "EDGAR" / "transform"
        edgar.mkdir(parents=True)
        (edgar / "__init__.py").write_text("")
        monkeypatch.setenv("MIREPORT_ARELLE_PLUGINS", str(tmp_path))
        options = self.optionsSeen(
            monkeypatch,
            self.makeProcessor(),
            "validateReportPackage",
            inlinePackage(sec=True),
        )
        assert options.plugins == str(edgar)

    def test_a_missing_plugin_fails_before_arelle_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MIREPORT_ARELLE_PLUGINS", raising=False)
        processor = self.makeProcessor()
        ran: list[bool] = []
        monkeypatch.setattr(processor, "_run", lambda *a, **k: ran.append(True))
        with pytest.raises(MissingPluginError, match="EDGAR"):
            processor.validateReportPackage(inlinePackage(sec=True))
        assert not ran
        assert ArelleCapability.SEC_INLINE_TRANSFORMS
