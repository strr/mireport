"""Unit tests for the optional top-level "domainRoots" section (see
mireport.arelle.taxonomy_extraction.extractDomainRoots(), which writes it), exposed
through Taxonomy.domainRoots.

A domain-member tree that no dimension in the DTS uses (a country list, say) is named
by its root and its members, so that a reader can see a domain with no dimension.
"""

from __future__ import annotations

from typing import Any

from mireport.taxonomy import Taxonomy

_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"


def _concept() -> dict[str, Any]:
    return {
        "labels": {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": "xbrli:stringItemType",
        "baseDataType": "xbrli:stringItemType",
        "periodType": "duration",
        "numeric": False,
    }


def _bits(roots: list[dict[str, Any]] | None) -> dict[str, Any]:
    bits: dict[str, Any] = {
        "entryPoint": "test://domain-roots",
        "namespaces": {"ext": "https://example.com/ext"},
        "concepts": {
            f"ext:{n}": _concept() for n in ("Domain", "AD", "AE", "Other", "Item")
        },
        "presentation": {},
        "dimensions": {},
    }
    if roots is not None:
        bits["domainRoots"] = roots
    return bits


def _names(taxonomy: Taxonomy) -> dict[str, tuple[str, ...]]:
    return {
        root.qname.localName: tuple(m.qname.localName for m in members)
        for root, members in taxonomy.domainRoots.items()
    }


class TestDomainRoots:
    def test_a_root_maps_to_its_members_in_order(self) -> None:
        taxonomy = Taxonomy.fromJSON(
            _bits(
                [
                    {
                        "elr": "https://example.com/elr",
                        "root": "ext:Domain",
                        "members": ["ext:AE", "ext:AD"],
                    }
                ]
            )
        )
        assert _names(taxonomy) == {"Domain": ("AE", "AD")}

    def test_the_roots_and_members_are_concepts_of_the_taxonomy(self) -> None:
        taxonomy = Taxonomy.fromJSON(
            _bits([{"elr": "e", "root": "ext:Domain", "members": ["ext:AD"]}])
        )
        (root,) = taxonomy.domainRoots
        assert root is taxonomy.getConcept(root.qname)

    def test_roots_of_several_elrs_are_all_kept(self) -> None:
        taxonomy = Taxonomy.fromJSON(
            _bits(
                [
                    {"elr": "a", "root": "ext:Domain", "members": ["ext:AD"]},
                    {"elr": "b", "root": "ext:Other", "members": ["ext:AE"]},
                ]
            )
        )
        assert _names(taxonomy) == {"Domain": ("AD",), "Other": ("AE",)}

    def test_a_root_in_two_elrs_has_both_member_lists(self) -> None:
        taxonomy = Taxonomy.fromJSON(
            _bits(
                [
                    {"elr": "a", "root": "ext:Domain", "members": ["ext:AD"]},
                    {"elr": "b", "root": "ext:Domain", "members": ["ext:AD", "ext:AE"]},
                ]
            )
        )
        assert _names(taxonomy) == {"Domain": ("AD", "AE")}

    def test_absent_means_none(self) -> None:
        assert dict(Taxonomy.fromJSON(_bits(None)).domainRoots) == {}

    def test_empty_means_none(self) -> None:
        assert dict(Taxonomy.fromJSON(_bits([])).domainRoots) == {}
