"""Unit tests for mireport.arelle.capabilities: what a report needs from Arelle.

Callers name capabilities, mireport maps them to Arelle plug-ins. Detection reads
the report package itself, so no caller needs a table of regimes.
"""

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from mireport.arelle.capabilities import (
    ArelleCapability,
    MissingPluginError,
    detect_capabilities,
    plugins_for,
)
from mireport.arelle.support import ArelleRelatedException
from mireport.filesupport import FilelikeAndFileName

IX = "http://www.xbrl.org/2013/inlineXBRL"
IX_1_0 = "http://www.xbrl.org/2008/inlineXBRL"
SEC_IXT = "http://www.sec.gov/inlineXBRL/transformation/2015-08-31"
PLAIN_IXT = "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"


def inlineDoc(*, ixt: str = PLAIN_IXT, padding: int = 0, ix: str = IX) -> bytes:
    head = (
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="{ix}" '
        f'xmlns:ixt="{ixt}"><head/><body>'
    )
    return (head + "x" * padding + "</body></html>").encode()


def package(entries: dict[str, bytes], name: str = "r.xbri") -> FilelikeAndFileName:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as zf:
        for entry, content in entries.items():
            zf.writestr(entry, content)
    return FilelikeAndFileName(fileContent=stream.getvalue(), filename=name)


class TestDetectCapabilities:
    def test_single_plain_inline_document_needs_nothing(self) -> None:
        pkg = package({"r/reports/a.xhtml": inlineDoc()})
        assert detect_capabilities(pkg) == frozenset()

    def test_several_inline_documents_are_a_document_set(self) -> None:
        pkg = package(
            {
                "r/reports/d/0.htm": inlineDoc(),
                "r/reports/d/1.htm": inlineDoc(),
                "r/reports/d/img.png": b"\x89PNG",
            }
        )
        assert detect_capabilities(pkg) == {ArelleCapability.INLINE_DOCUMENT_SET}

    def test_inline_xbrl_1_0_documents_count_too(self) -> None:
        # EDINET's documents declare the 2008 namespace of Inline XBRL 1.0.
        pkg = package(
            {
                "r/reports/d/0.htm": inlineDoc(ix=IX_1_0),
                "r/reports/d/1.htm": inlineDoc(ix=IX_1_0),
            }
        )
        assert detect_capabilities(pkg) == {ArelleCapability.INLINE_DOCUMENT_SET}

    def test_html_that_is_not_inline_xbrl_does_not_count(self) -> None:
        pkg = package(
            {
                "r/reports/a.htm": inlineDoc(),
                "r/reports/readme.htm": b"<html><body>hello</body></html>",
            }
        )
        assert detect_capabilities(pkg) == frozenset()

    def test_sec_transformation_namespace(self) -> None:
        pkg = package({"r/reports/a.htm": inlineDoc(ixt=SEC_IXT)})
        assert detect_capabilities(pkg) == {ArelleCapability.SEC_INLINE_TRANSFORMS}

    def test_sec_namespace_in_any_document_of_a_set(self) -> None:
        pkg = package(
            {"r/reports/0.htm": inlineDoc(), "r/reports/1.htm": inlineDoc(ixt=SEC_IXT)}
        )
        assert detect_capabilities(pkg) == {
            ArelleCapability.INLINE_DOCUMENT_SET,
            ArelleCapability.SEC_INLINE_TRANSFORMS,
        }

    def test_only_the_start_of_a_document_is_read(self) -> None:
        # Namespace declarations are on the root element; a huge document is not scanned whole.
        doc = inlineDoc(padding=2_000_000) + SEC_IXT.encode()
        pkg = package({"r/reports/a.htm": doc})
        assert detect_capabilities(pkg) == frozenset()

    def test_xbrl_xml_report_needs_nothing(self) -> None:
        pkg = package({"r/reports/a.xbrl": b"<xbrl/>"}, name="r.xbr")
        assert detect_capabilities(pkg) == frozenset()

    def test_a_non_zip_is_not_inspected(self) -> None:
        pkg = FilelikeAndFileName(fileContent=b"<html/>", filename="a.html")
        assert detect_capabilities(pkg) == frozenset()


class TestPluginsFor:
    def makeEdgar(self, root: Path) -> Path:
        transform = root / "EDGAR" / "transform"
        transform.mkdir(parents=True)
        (transform / "__init__.py").write_text("")
        return root

    def test_no_capabilities_no_plugins(self) -> None:
        assert plugins_for(frozenset(), None) == []

    def test_document_set_uses_the_bundled_plugin(self) -> None:
        assert plugins_for({ArelleCapability.INLINE_DOCUMENT_SET}, None) == [
            "inlineXbrlDocumentSet"
        ]

    def test_sec_transforms_use_the_edgar_plugin_under_the_plugin_directory(
        self, tmp_path: Path
    ) -> None:
        root = self.makeEdgar(tmp_path)
        assert plugins_for({ArelleCapability.SEC_INLINE_TRANSFORMS}, root) == [
            str(root / "EDGAR" / "transform")
        ]

    def test_order_is_stable(self, tmp_path: Path) -> None:
        root = self.makeEdgar(tmp_path)
        both = {
            ArelleCapability.SEC_INLINE_TRANSFORMS,
            ArelleCapability.INLINE_DOCUMENT_SET,
        }
        assert plugins_for(both, root) == plugins_for(both, root)
        assert len(plugins_for(both, root)) == 2

    def test_missing_plugin_directory_is_named_not_silent(self) -> None:
        with pytest.raises(MissingPluginError, match="SEC inline transformations"):
            plugins_for({ArelleCapability.SEC_INLINE_TRANSFORMS}, None)

    def test_missing_plugin_under_a_directory_names_the_expected_path(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(MissingPluginError, match="EDGAR"):
            plugins_for({ArelleCapability.SEC_INLINE_TRANSFORMS}, tmp_path)

    def test_missing_plugin_is_an_arelle_related_exception(self) -> None:
        assert issubclass(MissingPluginError, ArelleRelatedException)
