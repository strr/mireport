"""Unit tests for explicit-dimension domain trees: DimensionDomainTree and
DomainMemberRelationship, loaded from a cube's optional
"explicitDimensionDomains" key (see
mireport.arelle.taxonomy_extraction.getDomainTreesForExplicitDimension(),
which writes it alongside the flat "explicitDimensions" usable set), and
exposed through ExplicitDimensionSignature.domainTrees,
Taxonomy.getDomainTreesForExplicitDimension() and
Taxonomy.getDomainHeadsForExplicitDimension().

Built over hand-written taxonomy JSON via Taxonomy.fromJSON(), so nothing is
registered (see test_taxonomy_fromJSON.py) and no Arelle DTS is needed.
"""

from __future__ import annotations

from typing import Any

from mireport.data import taxonomies
from mireport.json import getJsonFiles, getObject
from mireport.taxonomy import (
    DimensionDomainTree,
    DomainMemberRelationship,
    ExplicitDimensionSignature,
    Taxonomy,
)

_NS = "https://example.com/vsme"
_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"
_ROLE = "https://example.com/role/cube"
_OTHER_ROLE = "https://example.com/role/other-cube"
_DOMAIN_ROLE = "https://example.com/role/domain"


def _concept(*, abstract: bool = False, **flags: bool) -> dict[str, Any]:
    jconcept: dict[str, Any] = {
        "labels": {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": "xbrli:stringItemType",
        "baseDataType": "xbrli:stringItemType",
        "periodType": "duration",
    }
    if abstract or flags:
        jconcept["abstract"] = True
    jconcept.update(flags)
    return jconcept


_CONCEPTS = {
    "vsme:Table": _concept(hypercube=True),
    "vsme:OtherTable": _concept(hypercube=True),
    "vsme:Item": _concept(),
    "vsme:RegionAxis": _concept(dimension=True),
    "vsme:RegionDomain": _concept(abstract=True),
    "vsme:Europe": _concept(abstract=True),
    "vsme:France": _concept(abstract=True),
    "vsme:Germany": _concept(abstract=True),
    "vsme:Asia": _concept(abstract=True),
}


def _rel(
    parent: str,
    member: str,
    order: float,
    *,
    usable: bool = True,
    elr: str = _ROLE,
) -> dict[str, Any]:
    return {
        "elr": elr,
        "parent": parent,
        "member": member,
        "order": order,
        "usable": usable,
    }


def _tree(
    members: list[dict[str, Any]],
    *,
    domain: str = "vsme:RegionDomain",
    order: float = 1.0,
    usable: bool = True,
    elr: str = _ROLE,
) -> dict[str, Any]:
    return {
        "elr": elr,
        "domain": domain,
        "order": order,
        "usable": usable,
        "members": members,
    }


# RegionDomain
#   Europe (not usable: a grouping only)
#     France
#     Germany
#   Asia
_NESTED_MEMBERS = [
    _rel("vsme:RegionDomain", "vsme:Europe", 1.0, usable=False),
    _rel("vsme:Europe", "vsme:France", 1.0),
    _rel("vsme:Europe", "vsme:Germany", 2.0),
    _rel("vsme:RegionDomain", "vsme:Asia", 2.0),
]
_NESTED_USABLE = ["vsme:RegionDomain", "vsme:France", "vsme:Germany", "vsme:Asia"]


def _cube(
    usable: list[str], trees: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    cube: dict[str, Any] = {
        "primaryItems": [[0, "vsme:Item"]],
        "xbrldt:contextElement": "scenario",
        "xbrldt:closed": True,
        "explicitDimensions": {"vsme:RegionAxis": usable},
    }
    if trees is not None:
        cube["explicitDimensionDomains"] = {"vsme:RegionAxis": trees}
    return cube


def _taxonomy(dimensions: dict[str, Any]) -> Taxonomy:
    return Taxonomy.fromJSON(
        {
            "entryPoint": "test://domain-trees",
            "namespaces": {"vsme": _NS},
            "concepts": _CONCEPTS,
            "presentation": {},
            "dimensions": dimensions,
        }
    )


def _signature(taxonomy: Taxonomy, role: str = _ROLE) -> ExplicitDimensionSignature:
    [declaration] = [d for d in taxonomy.hypercubeDeclarations if d.roleUri == role]
    [signature] = declaration.explicitDimensions
    return signature


class TestNestedDomainTree:
    def setup_method(self) -> None:
        self.taxonomy = _taxonomy(
            {_ROLE: {"vsme:Table": _cube(_NESTED_USABLE, [_tree(_NESTED_MEMBERS)])}}
        )
        self.c = self.taxonomy.getConcept

    def test_signature_carries_the_declared_tree(self) -> None:
        [tree] = _signature(self.taxonomy).domainTrees
        assert tree == DimensionDomainTree(
            dimension=self.c("vsme:RegionAxis"),
            roleUri=_ROLE,
            domainHead=self.c("vsme:RegionDomain"),
            order=1.0,
            usable=True,
            relationships=(
                DomainMemberRelationship(
                    _ROLE,
                    self.c("vsme:RegionDomain"),
                    self.c("vsme:Europe"),
                    1.0,
                    False,
                ),
                DomainMemberRelationship(
                    _ROLE, self.c("vsme:Europe"), self.c("vsme:France"), 1.0, True
                ),
                DomainMemberRelationship(
                    _ROLE, self.c("vsme:Europe"), self.c("vsme:Germany"), 2.0, True
                ),
                DomainMemberRelationship(
                    _ROLE, self.c("vsme:RegionDomain"), self.c("vsme:Asia"), 2.0, True
                ),
            ),
        )

    def test_children_keep_nesting_and_arc_order(self) -> None:
        [tree] = _signature(self.taxonomy).domainTrees
        assert [r.member for r in tree.getChildren(tree.domainHead)] == [
            self.c("vsme:Europe"),
            self.c("vsme:Asia"),
        ]
        assert [r.member for r in tree.getChildren(self.c("vsme:Europe"))] == [
            self.c("vsme:France"),
            self.c("vsme:Germany"),
        ]
        assert tree.getChildren(self.c("vsme:France")) == ()

    def test_tree_members_match_the_flat_usable_domain(self) -> None:
        signature = _signature(self.taxonomy)
        [tree] = signature.domainTrees
        assert tree.members == signature.domain
        assert self.c("vsme:Europe") not in tree.members

    def test_flat_api_is_unchanged(self) -> None:
        assert self.taxonomy.getDomainMembersForExplicitDimension(
            self.c("vsme:RegionAxis")
        ) == frozenset(self.c(q) for q in _NESTED_USABLE)

    def test_domain_head(self) -> None:
        axis = self.c("vsme:RegionAxis")
        assert self.taxonomy.getDomainHeadsForExplicitDimension(axis) == {
            self.c("vsme:RegionDomain")
        }
        assert self.taxonomy.getDomainTreesForExplicitDimension(axis) == (
            _signature(self.taxonomy).domainTrees
        )

    def test_unusable_domain_head_is_not_a_member(self) -> None:
        taxonomy = _taxonomy(
            {
                _ROLE: {
                    "vsme:Table": _cube(
                        _NESTED_USABLE[1:], [_tree(_NESTED_MEMBERS, usable=False)]
                    )
                }
            }
        )
        signature = _signature(taxonomy)
        [tree] = signature.domainTrees
        assert tree.usable is False
        assert tree.members == signature.domain
        assert taxonomy.getDomainHeadsForExplicitDimension(
            taxonomy.getConcept("vsme:RegionAxis")
        ) == {taxonomy.getConcept("vsme:RegionDomain")}

    def test_arc_in_a_target_role_keeps_its_own_elr(self) -> None:
        members = [
            _rel("vsme:RegionDomain", "vsme:Europe", 1.0),
            _rel("vsme:Europe", "vsme:France", 1.0, elr=_DOMAIN_ROLE),
        ]
        taxonomy = _taxonomy(
            {
                _ROLE: {
                    "vsme:Table": _cube(
                        ["vsme:RegionDomain", "vsme:Europe", "vsme:France"],
                        [_tree(members)],
                    )
                }
            }
        )
        [tree] = _signature(taxonomy).domainTrees
        assert [r.roleUri for r in tree.relationships] == [_ROLE, _DOMAIN_ROLE]


class TestFlatDomainTree:
    """The shape nearly every real domain has: every member a direct child of
    the domain head."""

    def test_single_level_domain_round_trips(self) -> None:
        members = [
            _rel("vsme:RegionDomain", "vsme:France", 1.0),
            _rel("vsme:RegionDomain", "vsme:Germany", 2.0),
        ]
        usable = ["vsme:RegionDomain", "vsme:France", "vsme:Germany"]
        taxonomy = _taxonomy({_ROLE: {"vsme:Table": _cube(usable, [_tree(members)])}})
        c = taxonomy.getConcept
        signature = _signature(taxonomy)
        [tree] = signature.domainTrees
        assert [(r.parent, r.member, r.order) for r in tree.relationships] == [
            (c("vsme:RegionDomain"), c("vsme:France"), 1.0),
            (c("vsme:RegionDomain"), c("vsme:Germany"), 2.0),
        ]
        assert tree.getChildren(tree.domainHead) == tree.relationships
        assert tree.members == signature.domain

    def test_domain_head_without_members(self) -> None:
        taxonomy = _taxonomy(
            {_ROLE: {"vsme:Table": _cube(["vsme:RegionDomain"], [_tree([])])}}
        )
        [tree] = _signature(taxonomy).domainTrees
        assert tree.relationships == ()
        assert tree.members == {taxonomy.getConcept("vsme:RegionDomain")}


class TestAcrossBaseSets:
    def test_same_arc_reached_from_two_cubes_of_one_role_is_one_tree(self) -> None:
        cube = _cube(_NESTED_USABLE, [_tree(_NESTED_MEMBERS)])
        taxonomy = _taxonomy({_ROLE: {"vsme:Table": cube, "vsme:OtherTable": cube}})
        axis = taxonomy.getConcept("vsme:RegionAxis")
        assert len(taxonomy.getDomainTreesForExplicitDimension(axis)) == 1

    def test_same_domain_in_two_roles_is_two_trees_ordered_by_role(self) -> None:
        def cubeIn(role: str) -> dict[str, Any]:
            members = [dict(rel, elr=role) for rel in _NESTED_MEMBERS]
            return _cube(_NESTED_USABLE, [_tree(members, elr=role)])

        taxonomy = _taxonomy(
            {
                _ROLE: {"vsme:Table": cubeIn(_ROLE)},
                _OTHER_ROLE: {"vsme:Table": cubeIn(_OTHER_ROLE)},
            }
        )
        axis = taxonomy.getConcept("vsme:RegionAxis")
        trees = taxonomy.getDomainTreesForExplicitDimension(axis)
        assert [t.roleUri for t in trees] == sorted([_ROLE, _OTHER_ROLE])
        assert taxonomy.getDomainHeadsForExplicitDimension(axis) == {
            taxonomy.getConcept("vsme:RegionDomain")
        }

    def test_signatures_still_compare_by_domain_alone(self) -> None:
        withTree = _signature(
            _taxonomy(
                {_ROLE: {"vsme:Table": _cube(_NESTED_USABLE, [_tree(_NESTED_MEMBERS)])}}
            )
        )
        assert withTree == ExplicitDimensionSignature(
            withTree.dimension, withTree.domain
        )


class TestJSONWithoutDomainTrees:
    """JSON baked before domain trees were extracted has only the flat
    "explicitDimensions" usable set."""

    def test_legacy_cube_loads_with_no_trees(self) -> None:
        taxonomy = _taxonomy({_ROLE: {"vsme:Table": _cube(_NESTED_USABLE)}})
        axis = taxonomy.getConcept("vsme:RegionAxis")
        assert _signature(taxonomy).domainTrees == ()
        assert taxonomy.getDomainTreesForExplicitDimension(axis) == ()
        assert taxonomy.getDomainHeadsForExplicitDimension(axis) == frozenset()
        assert taxonomy.getDomainMembersForExplicitDimension(axis) == frozenset(
            taxonomy.getConcept(q) for q in _NESTED_USABLE
        )

    def test_undeclared_dimension_has_no_trees(self) -> None:
        taxonomy = _taxonomy({})
        axis = taxonomy.getConcept("vsme:RegionAxis")
        assert taxonomy.getDomainTreesForExplicitDimension(axis) == ()

    def test_built_in_taxonomies_load(self) -> None:
        """Every built-in taxonomy JSON still loads, and any tree it does
        carry flattens to its signature's domain (none of them may carry any
        yet, if baked before domain trees were extracted -- that is fine
        too)."""
        for path in getJsonFiles(taxonomies):
            taxonomy = Taxonomy.fromJSON(getObject(path))
            for declaration in taxonomy.hypercubeDeclarations:
                for signature in declaration.explicitDimensions:
                    if signature.domainTrees:
                        assert (
                            frozenset().union(
                                *(tree.members for tree in signature.domainTrees)
                            )
                            == signature.domain
                        )
