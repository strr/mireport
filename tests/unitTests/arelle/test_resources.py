"""Unit tests for mireport.arelle.resources: the files Arelle ships and needs no package for."""

from __future__ import annotations

from pathlib import Path

from mireport.arelle.resources import BundledResources


def makeCache(root: Path, *relative: str) -> Path:
    for name in relative:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    return root


class TestBundledResources:
    def test_a_cached_file_is_bundled_under_its_exact_url(self, tmp_path: Path) -> None:
        cache = makeCache(
            tmp_path, "http/www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"
        )
        bundled = BundledResources(cache)
        assert bundled.contains("http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd")

    def test_http_and_https_are_different_resources_on_other_hosts(
        self, tmp_path: Path
    ) -> None:
        # Arelle keys a document by how a reference spells it.
        cache = makeCache(tmp_path, "https/www.eurofiling.info/a.xsd")
        bundled = BundledResources(cache)
        assert bundled.contains("https://www.eurofiling.info/a.xsd")
        assert not bundled.contains("http://www.eurofiling.info/a.xsd")

    def test_the_four_spellings_of_xbrl_org_are_one_location(
        self, tmp_path: Path
    ) -> None:
        # Arelle's own rule: xbrl.org serves http and https, with and without www.
        cache = makeCache(tmp_path, "https/www.xbrl.org/dtr/type/2022-03-31/types.xsd")
        bundled = BundledResources(cache)
        for prefix in (
            "http://www.xbrl.org/",
            "http://xbrl.org/",
            "https://www.xbrl.org/",
            "https://xbrl.org/",
        ):
            assert bundled.contains(f"{prefix}dtr/type/2022-03-31/types.xsd"), prefix

    def test_an_uncached_file_is_not_bundled(self, tmp_path: Path) -> None:
        bundled = BundledResources(makeCache(tmp_path, "http/www.xbrl.org/a.xsd"))
        assert not bundled.contains("http://www.xbrl.org/b.xsd")

    def test_a_fragment_is_ignored(self, tmp_path: Path) -> None:
        bundled = BundledResources(makeCache(tmp_path, "http/www.xbrl.org/a.xsd"))
        assert bundled.contains("http://www.xbrl.org/a.xsd#element")

    def test_a_directory_is_not_a_resource(self, tmp_path: Path) -> None:
        bundled = BundledResources(makeCache(tmp_path, "http/www.xbrl.org/2003/a.xsd"))
        assert not bundled.contains("http://www.xbrl.org/2003")

    def test_other_schemes_are_never_bundled(self, tmp_path: Path) -> None:
        bundled = BundledResources(makeCache(tmp_path, "http/www.xbrl.org/a.xsd"))
        assert not bundled.contains("ftp://www.xbrl.org/a.xsd")
        assert not bundled.contains("a.xsd")

    def test_arelles_own_cache_is_found_by_default(self) -> None:
        bundled = BundledResources()
        assert bundled.contains("http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd")
