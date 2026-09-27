"""Unit tests for enum2 concept domain trees: EnumerationDomainTree, loaded
from a concept's optional "other"."ee20Domain" key (see
mireport.arelle.taxonomy_extraction.getDomainTreeForEnumeration(), which
writes it alongside the flat "ee20DomainMembers" list), and exposed through
Concept.getEEDomainTree() and Concept.getEEDomainHead().

Built over hand-written taxonomy JSON via Taxonomy.fromJSON(), so nothing is
registered (see test_taxonomy_fromJSON.py) and no Arelle DTS is needed.
"""

from __future__ import annotations

from typing import Any

from mireport.data import taxonomies
from mireport.json import getJsonFiles, getObject
from mireport.taxonomy import (
    DomainMemberRelationship,
    EnumerationDomainTree,
    Taxonomy,
)

_NS = "https://example.com/vsme"
_EXT_NS = "https://example.com/country"
_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"
_ROLE = "https://example.com/role/enum-domain"
_TARGET_ROLE = "https://example.com/role/enum-target"


def _concept(other: dict[str, Any] | None = None, **flags: bool) -> dict[str, Any]:
    jconcept: dict[str, Any] = {
        "labels": {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": "xbrli:stringItemType",
        "baseDataType": "xbrli:stringItemType",
        "periodType": "duration",
        **flags,
    }
    if other is not None:
        jconcept["dataType"] = "enum2:enumerationItemType"
        jconcept["baseDataType"] = "xbrli:tokenItemType"
        jconcept["other"] = other
    return jconcept


def _rel(
    parent: str, member: str, order: float, *, usable: bool = True, elr: str = _ROLE
) -> dict[str, Any]:
    return {
        "elr": elr,
        "parent": parent,
        "member": member,
        "order": order,
        "usable": usable,
    }


# country:CountryDomain (enum2:headUsable="false"; in another namespace, as
# a real enum2 domain usually is)
#   country:Europe (not usable: a grouping only)
#     country:FR
#     country:DE
#   country:JP
_NESTED_MEMBERS = [
    _rel("country:CountryDomain", "country:Europe", 1.0, usable=False),
    _rel("country:Europe", "country:FR", 1.0),
    _rel("country:Europe", "country:DE", 2.0),
    _rel("country:CountryDomain", "country:JP", 2.0),
]
_NESTED_USABLE = ["country:FR", "country:DE", "country:JP"]


def _tree(
    members: list[dict[str, Any]],
    *,
    domain: str = "country:CountryDomain",
    usable: bool = False,
    elr: str = _ROLE,
) -> dict[str, Any]:
    return {"elr": elr, "domain": domain, "usable": usable, "members": members}


def _taxonomy(choice: dict[str, Any]) -> Taxonomy:
    return Taxonomy.fromJSON(
        {
            "entryPoint": "test://enumeration-domain-trees",
            "namespaces": {
                "vsme": _NS,
                "country": _EXT_NS,
                "enum2": "http://xbrl.org/2020/extensible-enumerations-2.0",
            },
            "concepts": {
                "vsme:Choice": choice,
                "vsme:Plain": _concept(),
                "country:CountryDomain": _concept(abstract=True),
                "country:Europe": _concept(abstract=True),
                "country:FR": _concept(abstract=True),
                "country:DE": _concept(abstract=True),
                "country:JP": _concept(abstract=True),
            },
            "presentation": {},
            "dimensions": {},
        }
    )


class TestNestedEnumerationDomain:
    def setup_method(self) -> None:
        self.taxonomy = _taxonomy(
            _concept(
                {
                    "ee20DomainMembers": _NESTED_USABLE,
                    "ee20Domain": _tree(_NESTED_MEMBERS),
                }
            )
        )
        self.c = self.taxonomy.getConcept
        self.choice = self.c("vsme:Choice")

    def test_concept_carries_the_declared_tree(self) -> None:
        c = self.c
        assert self.choice.getEEDomainTree() == EnumerationDomainTree(
            enumeration=self.choice,
            roleUri=_ROLE,
            domainHead=c("country:CountryDomain"),
            usable=False,
            relationships=(
                DomainMemberRelationship(
                    _ROLE, c("country:CountryDomain"), c("country:Europe"), 1.0, False
                ),
                DomainMemberRelationship(
                    _ROLE, c("country:Europe"), c("country:FR"), 1.0, True
                ),
                DomainMemberRelationship(
                    _ROLE, c("country:Europe"), c("country:DE"), 2.0, True
                ),
                DomainMemberRelationship(
                    _ROLE, c("country:CountryDomain"), c("country:JP"), 2.0, True
                ),
            ),
        )

    def test_domain_head_is_the_real_head_even_when_not_usable(self) -> None:
        head = self.c("country:CountryDomain")
        assert self.choice.getEEDomainHead() == head
        assert head not in self.choice.getEEDomain()

    def test_children_keep_nesting_and_arc_order(self) -> None:
        tree = self.choice.getEEDomainTree()
        assert tree is not None
        assert [r.member for r in tree.getChildren(tree.domainHead)] == [
            self.c("country:Europe"),
            self.c("country:JP"),
        ]
        assert [r.member for r in tree.getChildren(self.c("country:Europe"))] == [
            self.c("country:FR"),
            self.c("country:DE"),
        ]
        assert tree.getChildren(self.c("country:FR")) == ()

    def test_tree_members_match_the_flat_list(self) -> None:
        tree = self.choice.getEEDomainTree()
        assert tree is not None
        assert tree.members == frozenset(self.choice.getEEDomain())
        assert self.c("country:Europe") not in tree.members

    def test_flat_api_is_unchanged(self) -> None:
        assert self.choice.getEEDomain() == tuple(self.c(q) for q in _NESTED_USABLE)


class TestEnumerationDomainVariants:
    def test_usable_head_is_a_member(self) -> None:
        taxonomy = _taxonomy(
            _concept(
                {
                    "ee20DomainMembers": ["country:CountryDomain", *_NESTED_USABLE],
                    "ee20Domain": _tree(_NESTED_MEMBERS, usable=True),
                }
            )
        )
        choice = taxonomy.getConcept("vsme:Choice")
        tree = choice.getEEDomainTree()
        assert tree is not None
        assert tree.usable is True
        assert tree.members == frozenset(choice.getEEDomain())
        assert taxonomy.getConcept("country:CountryDomain") in tree.members

    def test_arc_in_a_target_role_keeps_its_own_elr(self) -> None:
        members = [
            _rel("country:CountryDomain", "country:Europe", 1.0),
            _rel("country:Europe", "country:FR", 1.0, elr=_TARGET_ROLE),
        ]
        taxonomy = _taxonomy(
            _concept(
                {
                    "ee20DomainMembers": ["country:Europe", "country:FR"],
                    "ee20Domain": _tree(members),
                }
            )
        )
        tree = taxonomy.getConcept("vsme:Choice").getEEDomainTree()
        assert tree is not None
        assert tree.roleUri == _ROLE
        assert [r.roleUri for r in tree.relationships] == [_ROLE, _TARGET_ROLE]

    def test_domain_head_without_members(self) -> None:
        taxonomy = _taxonomy(
            _concept(
                {
                    "ee20DomainMembers": ["country:CountryDomain"],
                    "ee20Domain": _tree([], usable=True),
                }
            )
        )
        tree = taxonomy.getConcept("vsme:Choice").getEEDomainTree()
        assert tree is not None
        assert tree.relationships == ()
        assert tree.members == {taxonomy.getConcept("country:CountryDomain")}


class TestJSONWithoutEnumerationDomainTrees:
    """JSON baked before enumeration domain trees were extracted has only the
    flat "ee20DomainMembers" list."""

    def test_legacy_concept_loads_with_no_tree(self) -> None:
        taxonomy = _taxonomy(_concept({"ee20DomainMembers": _NESTED_USABLE}))
        choice = taxonomy.getConcept("vsme:Choice")
        assert choice.getEEDomainTree() is None
        assert choice.getEEDomainHead() is None
        assert choice.getEEDomain() == tuple(
            taxonomy.getConcept(q) for q in _NESTED_USABLE
        )

    def test_non_enumeration_concept_has_no_tree(self) -> None:
        taxonomy = _taxonomy(_concept({"ee20DomainMembers": _NESTED_USABLE}))
        plain = taxonomy.getConcept("vsme:Plain")
        assert plain.getEEDomainTree() is None
        assert plain.getEEDomainHead() is None

    def test_built_in_taxonomies_load(self) -> None:
        """Every built-in taxonomy JSON still loads, and any enumeration
        domain tree it carries flattens to that concept's getEEDomain()."""
        for path in getJsonFiles(taxonomies):
            taxonomy = Taxonomy.fromJSON(getObject(path))
            for concept in taxonomy.concepts:
                if (tree := concept.getEEDomainTree()) is not None:
                    assert tree.enumeration == concept
                    assert tree.members == frozenset(concept.getEEDomain())
