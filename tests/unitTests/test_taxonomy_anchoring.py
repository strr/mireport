"""Unit tests for the optional top-level "anchoring" section (see
mireport.arelle.taxonomy_extraction.extractAnchoring(), which writes it),
exposed through Taxonomy.anchoring."""

from __future__ import annotations

from typing import Any

import pytest

from mireport.taxonomy import AnchoringGroup, AnchoringRelationship, Taxonomy

_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"
_ROLE = "https://example.com/role/Anchoring"
_OTHER_ROLE = "https://example.com/role/AAnchoring"


def _concept() -> dict[str, Any]:
    return {
        "labels": {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": "xbrli:monetaryItemType",
        "baseDataType": "xbrli:monetaryItemType",
        "periodType": "duration",
        "numeric": True,
    }


def _rel(source: str, target: str, order: float) -> dict[str, Any]:
    return {"source": source, "target": target, "order": order}


def _bits(anchoring: dict[str, Any] | None) -> dict[str, Any]:
    bits: dict[str, Any] = {
        "entryPoint": "test://anchoring",
        "namespaces": {
            "ext": "https://example.com/ext",
            "base": "https://example.com/base",
        },
        "concepts": {k: _concept() for k in ("ext:A", "ext:B", "base:A", "base:B")},
        "presentation": {},
        "dimensions": {},
    }
    if anchoring is not None:
        bits["anchoring"] = anchoring
    return bits


_ANCHORING = {
    _ROLE: {
        "relationships": [
            _rel("ext:A", "base:A", 1.0),
            _rel("base:B", "ext:B", 2.5),
        ]
    },
    _OTHER_ROLE: {"relationships": [_rel("ext:B", "base:B", 1.0)]},
}


class TestAnchoring:
    def test_groups_sorted_by_role_with_relationships_in_file_order(self) -> None:
        taxonomy = Taxonomy.fromJSON(_bits(_ANCHORING))
        c = taxonomy.getConcept
        assert taxonomy.anchoring == (
            AnchoringGroup(
                _OTHER_ROLE,
                (AnchoringRelationship(c("ext:B"), c("base:B"), 1.0),),
            ),
            AnchoringGroup(
                _ROLE,
                (
                    AnchoringRelationship(c("ext:A"), c("base:A"), 1.0),
                    AnchoringRelationship(c("base:B"), c("ext:B"), 2.5),
                ),
            ),
        )

    def test_absent_key_gives_empty_tuple(self) -> None:
        assert Taxonomy.fromJSON(_bits(None)).anchoring == ()

    def test_empty_section_gives_empty_tuple(self) -> None:
        assert Taxonomy.fromJSON(_bits({})).anchoring == ()

    def test_unknown_endpoint_is_a_clear_error(self) -> None:
        bad = {_ROLE: {"relationships": [_rel("ext:A", "ext:Nope", 1.0)]}}
        with pytest.raises(ValueError, match="ext:Nope"):
            Taxonomy.fromJSON(_bits(bad))

    def test_json_is_not_modified(self) -> None:
        bits = _bits(_ANCHORING)
        Taxonomy.fromJSON(bits)
        assert bits["anchoring"] == _ANCHORING
