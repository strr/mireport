"""Unit tests for the taxonomy-info Arelle plugin harness.

The extraction logic itself is tested in test_taxonomy_extraction.py; the
end-to-end plugin run is covered by
tests/integrationTests/test_taxonomy_info_regeneration.py.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from mireport.arelle.taxonomy_info import TaxonomyInfoPluginData


class TestTaxonomyInfoPluginData:
    def test_instances_have_independent_taxonomy_dicts(self) -> None:
        first = TaxonomyInfoPluginData("first")
        second = TaxonomyInfoPluginData("second")
        first.Taxonomy["concepts"] = {"vsme:Thing": {}}
        assert second.Taxonomy == {}
        assert first.Taxonomy is not second.Taxonomy

    def test_instances_have_independent_utr_dicts(self) -> None:
        first = TaxonomyInfoPluginData("first")
        second = TaxonomyInfoPluginData("second")
        first.UTR["utr"] = [{"unitId": "kg"}]
        assert second.UTR == {}
        assert first.UTR is not second.UTR


class TestARunThatFails:
    """Arelle prints a plug-in's traceback to stderr but puts nothing in the log it hands
    back, so a caller was told only that no taxonomy file was written."""

    class Cntlr:
        def __init__(self) -> None:
            self.logged: list[tuple[str, int | None]] = []
            self._data: Any = None

        def addToLog(self, message: str, **kwargs: Any) -> None:
            self.logged.append((message, kwargs.get("level")))

        def getPluginData(self, name: str) -> Any:
            return self._data

        def setPluginData(self, data: Any) -> None:
            self._data = data

    def test_the_failure_is_logged_with_its_type_and_text_and_still_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mireport.arelle import taxonomy_info

        class Boom:
            def __init__(self, *args: Any) -> None:
                pass

            def extract(self) -> dict:
                raise ValueError("a name that is not one")

        monkeypatch.setattr(taxonomy_info, "TaxonomyInfoExtractor", Boom)
        cntlr = self.Cntlr()
        with pytest.raises(ValueError, match="not one"):
            taxonomy_info.runTaxonomyInfo(cntlr, SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]
        errors = [m for m, level in cntlr.logged if level == logging.ERROR]
        assert len(errors) == 1
        assert "ValueError: a name that is not one" in errors[0]
