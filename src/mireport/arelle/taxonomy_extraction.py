"""Taxonomy and UTR extraction over a loaded Arelle DTS.

This module holds the extraction logic used by the ``taxonomy_info.py``
Arelle plugin. It is a normal importable module (unlike the plugin file,
which Arelle loads by file path as its own module) so the extractors can be
unit tested and reused without the plugin machinery. The extractors know
nothing about Arelle plugin data: :meth:`TaxonomyInfoExtractor.extract` and
:meth:`UTRInfoExtractor.extract` simply return the extracted data and the
plugin decides where to put it.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from typing import Any

from arelle import XbrlConst
from arelle.Cntlr import Cntlr
from arelle.ModelDtsObject import ModelConcept, ModelRoleType
from arelle.ModelValue import QName
from arelle.ModelXbrl import ModelXbrl
from arelle.RuntimeOptions import RuntimeOptions
from arelle.ValidateUtr import UtrEntry

from mireport.arelle.diagnostics import ArelleDiagnostic, DiagnosticEmitter
from mireport.arelle.model_access import (
    ConceptRelationship,
    ConceptRelationshipSet,
    ValidatedModel,
    qnameOf,
)
from mireport.arelle.support import (
    ArelleModelInconsistency,
    ArelleObjectJSONEncoder,
    ArelleQNameCanonicaliser,
    ArelleRelatedException,
    unique_list,
)

# The UtrEntry attributes worth serialising (the UTR schema's primary key is
# status + unitId).
_UTR_INTERESTING_KEYS = (
    "unitId",
    "unitName",
    "nsUnit",
    "itemType",
    "nsItemType",
    "numeratorItemType",
    "nsNumeratorItemType",
    "definition",
    "denominatorItemType",
    "nsDenominatorItemType",
    "symbol",
    "status",
)


def _overlappingPrimaryItems(
    primaryItemsByHypercube: Mapping[QName, Collection[QName]],
) -> frozenset[QName]:
    """The primary items declared in more than one hypercube of a base set.

    Mirrors Taxonomy._overlappingPrimaryItems in mireport/taxonomy.py, which acts
    on the same data once it has been loaded back out of the baked JSON (keyed by
    canonicalised QName strings there, rather than QNames here). Keep the two in
    step if the underlying rule ever changes.
    """
    hypercubesPerPrimaryItem = Counter(
        qname
        for primaryItems in primaryItemsByHypercube.values()
        for qname in set(primaryItems)
    )
    return frozenset(
        qname for qname, count in hypercubesPerPrimaryItem.items() if count > 1
    )


def _hypercubeType(arcrole: str, elrUri: str, hypercubeQName: QName) -> str:
    """ "positive" for an "all" relationship, "negative" for "notAll" -- never
    inferred by elimination, since a third arcrole here would be a modelling
    error we want to know about, not one we want to default to positive."""
    match arcrole:
        case XbrlConst.all:
            return "positive"
        case XbrlConst.notAll:
            return "negative"
        case _:
            raise ArelleModelInconsistency(
                ArelleDiagnostic.error(
                    "Hypercube relationship has neither the all nor the notAll arcrole",
                    elr=elrUri,
                    concepts=(hypercubeQName,),
                    arcrole=arcrole,
                )
            )


class DefinitionRow(NamedTuple):
    """One concept in a depth-first walk of a definition (domain-member) tree."""

    indent: int
    qname: QName
    isUsable: bool


class DefinitionRelationship(NamedTuple):
    """One arc in a depth-first walk of a definition (domain-member) tree,
    kept as an edge -- parent, arc order and the ELR the arc is declared in
    -- rather than as a DefinitionRow's indent, so the tree's exact shape
    survives."""

    elr: str
    parent: QName
    member: QName
    order: float
    isUsable: bool


class PresentationRow(NamedTuple):
    """One concept in a depth-first walk of a presentation tree."""

    indent: int
    qname: QName
    preferredLabel: str | None


def writeDataFile(
    cntlr: Cntlr,
    jsonPath: str | Path,
    dataName: str,
    data: dict,
) -> None:
    if not data:
        cntlr.addToLog(f"No {dataName} data to write")
        return

    # N.B. dumps() rather than dump(): only the one-shot dumps()/encode()
    # path can use the C-accelerated encoder, and from Python 3.14 that
    # extends to indented output. Streaming via dump() never gets it.
    payload = json.dumps(data, indent=2, sort_keys=True, cls=ArelleObjectJSONEncoder)
    Path(jsonPath).write_text(payload, encoding="UTF-8")
    cntlr.addToLog(f"{dataName} data written to {jsonPath}")


class UTRInfoExtractor:
    def __init__(self, cntlr: Cntlr, modelXbrl: ModelXbrl):
        self.cntlr: Cntlr = cntlr
        self.modelXbrl: ModelXbrl = modelXbrl
        if (
            utrModel := getattr(
                self.modelXbrl.modelManager.disclosureSystem, "utrItemTypeEntries", None
            )
        ) is not None:
            self.utrModel: dict[str, dict[str, UtrEntry]] = utrModel
        else:
            message = (
                "No UTR entries found. Perhaps you forgot to set `utrValidate=True`?"
            )
            self.cntlr.addToLog(message)
            raise ArelleRelatedException(message)

    def extract(self) -> dict[str, Any]:
        return {"utr": self.getUTRForJSON()}

    def getUTRForJSON(self) -> list[dict]:
        """Get the UTR entries from the modelXbrl."""
        # N.B. UTR schema primary key is the status and unitId
        jUTR: list[dict] = []
        utrEntries = [
            entry
            for entriesByUnitId in self.utrModel.values()
            for entry in entriesByUnitId.values()
        ]
        for entry in sorted(utrEntries, key=lambda e: e.unitId or ""):
            jEntry = {}
            for key in _UTR_INTERESTING_KEYS:
                if (value := getattr(entry, key)) is not None and value.strip() != "":
                    jEntry[key] = value
            jUTR.append(jEntry)
        return jUTR


class TaxonomyInfoExtractor:
    def __init__(self, cntlr: Cntlr, options: RuntimeOptions, modelXbrl: ModelXbrl):
        self.cntlr: Cntlr = cntlr
        self.options: RuntimeOptions = options
        self.modelXbrl: ModelXbrl = modelXbrl
        self.model: ValidatedModel = ValidatedModel(modelXbrl)
        self.diagnostics: DiagnosticEmitter = DiagnosticEmitter(
            cntlr, getattr(options, "diagnosticsToken", None)
        )
        self.taxonomyJson: dict[str, Any] = defaultdict(dict)
        # A plain dict here would auto-vivify each ELR's cube dict via
        # defaultdict(dict), but leave the per-ELR mapping itself a plain
        # dict on first access -- fine when populated only through extract(),
        # but tests exercising extractDimensionDefinitions() directly (never
        # going through extract()) would KeyError on the first cube. Set it
        # here so both paths see the same structure.
        self.taxonomyJson["dimensions"] = defaultdict(dict)
        self.qnameConverter: ArelleQNameCanonicaliser = (
            ArelleQNameCanonicaliser.bootstrap(modelXbrl)
        )
        self.dimensionDefaults: dict[ModelConcept, ModelConcept] = {}
        # Accumulates references across all concepts, keyed by (role, parts)
        # so that an identical reference cited by several concepts folds into
        # a single entry -- see collectReferences()/extractReferences().
        self._references: dict[
            tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]
        ] = {}

    def extract(self) -> dict[str, Any]:
        """Extract the taxonomy information and return it as a JSON-ready
        dict with all QNames canonicalised to strings."""
        self.taxonomyJson["entryPoint"] = self.options.entrypointFile

        self.extractPresentation()
        self.extractCalculation()
        # Extract dimension defaults before other dimension-related information
        # (used by other dimension-related extraction methods)
        self.extractDimensionDefaults()
        self.extractDimensionDefinitions()
        self.reportDomainMemberOnlyLinkroleRoots()
        self.extractConceptsAndMetadata()
        self.extractReferences()
        self.reportIsolatedConcepts()

        self.cntlr.addToLog("Processing namespaces and namespace prefixes")
        self.taxonomyJson = self.qnameConverter.convertRecursive(self.taxonomyJson)
        self.taxonomyJson["namespaces"] = self.qnameConverter.getNamespacePrefixMap()
        return self.taxonomyJson

    def walkDefinitionChildren(
        self,
        parent_concept: ModelConcept,
        relSet: ConceptRelationshipSet,
        indent: int,
    ) -> Iterator[DefinitionRow]:
        """Yield the descendants of `parent_concept` depth-first, following
        each arc's consecutive linkrole (xbrldt:targetRole)."""
        for rel in relSet.relationshipsFrom(parent_concept):
            yield DefinitionRow(indent, rel.targetQName, rel.isUsable)
            yield from self.walkDefinitionChildren(
                rel.target, relSet.consecutiveSet(rel), indent + 1
            )

    def walkDefinitionRelationships(
        self,
        parent_concept: ModelConcept,
        relSet: ConceptRelationshipSet,
        _seen: set[tuple[str, QName, QName]] | None = None,
    ) -> Iterator[DefinitionRelationship]:
        """Yield the arcs beneath `parent_concept` depth-first, siblings in arc
        order, following each arc's consecutive linkrole (xbrldt:targetRole).

        Unlike walkDefinitionChildren(), each arc is yielded once: a member
        reached through several parents yields one arc per parent, but the
        arcs beneath it only the first time. That also stops a (XDT-invalid)
        directed cycle from recursing forever."""
        seen = set() if _seen is None else _seen
        parentQName = qnameOf(parent_concept)
        elr = relSet.linkrole
        for rel in relSet.relationshipsFrom(parent_concept):
            key = (elr, parentQName, rel.targetQName)
            if key in seen:
                continue
            seen.add(key)
            yield DefinitionRelationship(
                elr, parentQName, rel.targetQName, rel.order, rel.isUsable
            )
            yield from self.walkDefinitionRelationships(
                rel.target, relSet.consecutiveSet(rel), seen
            )

    def walkPresentationChildren(
        self,
        parent_concept: ModelConcept,
        relSet: ConceptRelationshipSet,
        indent: int,
    ) -> Iterator[PresentationRow]:
        """Yield the descendants of `parent_concept` depth-first."""
        for rel in relSet.relationshipsFrom(parent_concept):
            yield PresentationRow(indent, rel.targetQName, rel.preferredLabel)
            yield from self.walkPresentationChildren(rel.target, relSet, indent + 1)

    def getPrimaryItems(
        self, elrUri: str, domainHeadConcept: ModelConcept
    ) -> list[tuple[int, QName]]:
        relSet = self.model.conceptRelationshipSet(XbrlConst.domainMember, elrUri)
        domainHeadQName = qnameOf(domainHeadConcept)

        # N.B. domainHeadConcept does not have to be a root concept

        if not relSet.hasRelationshipsFrom(domainHeadConcept):
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    "Hypercube has no primary items beyond the domain head (no outgoing domain-member relationships)",
                    elr=elrUri,
                    concepts=(domainHeadQName,),
                ),
            )
            return [(0, domainHeadQName)]

        return [(0, domainHeadQName)] + [
            (row.indent, row.qname)
            for row in self.walkDefinitionChildren(domainHeadConcept, relSet, 1)
        ]

    def getDimensions(
        self, elrUri: str, hypercube: ModelConcept, hypercubeIsClosed: bool
    ) -> list[ConceptRelationship]:
        relSet = self.model.conceptRelationshipSet(XbrlConst.hypercubeDimension, elrUri)

        if not relSet.hasRelationshipsFrom(hypercube):
            # This hypercube has no dimensions of its own. Other hypercubes
            # sharing the same ELR may still have dimensions of their own, so
            # this is not by itself a model inconsistency -- and a closed,
            # dimensionless hypercube is the WGN "Guidance on the use of
            # dimensions" section 3.4 way to give an otherwise-undimensioned
            # concept full dimensional validity, so this is informational
            # rather than a defect to fix.
            if hypercubeIsClosed:
                self.diagnostics.emit(
                    ArelleDiagnostic.info(
                        "Closed hypercube has no dimensions (no outgoing "
                        "hypercube-dimension relationships)",
                        elr=elrUri,
                        concepts=(qnameOf(hypercube),),
                    ),
                )
            return []

        if relSet.hasRelationshipsTo(hypercube):
            # It has outgoing relationships (we didn't return above) but is
            # also somebody else's target within the same hypercube-dimension
            # set, i.e. it isn't a root of that set. A hypercube must not
            # itself be used as a dimension.
            raise ArelleModelInconsistency(
                ArelleDiagnostic.error(
                    "Hypercube is also the target of a hypercube-dimension relationship",
                    elr=elrUri,
                    concepts=(qnameOf(hypercube),),
                )
            )
        return relSet.relationshipsFrom(hypercube)

    def getDomainMembersForExplicitDimension(
        self,
        explicitDimension: ModelConcept,
        elrUri: str,
    ) -> list[QName]:
        dimensionDomainRelSet = self.model.conceptRelationshipSet(
            XbrlConst.dimensionDomain, elrUri
        )

        dimensionDomainRoots = dimensionDomainRelSet.rootConcepts()
        if explicitDimension not in dimensionDomainRoots:
            raise ArelleModelInconsistency(
                ArelleDiagnostic.error(
                    "Dimension is not a root of the dimension-domain relationship set",
                    elr=elrUri,
                    concepts=(qnameOf(explicitDimension),),
                    roots=sorted(qnameOf(root) for root in dimensionDomainRoots),
                )
            )
        dimensionDomainRels = dimensionDomainRelSet.relationshipsFrom(explicitDimension)
        domainMemberTrees: list[tuple[ModelConcept, bool, ConceptRelationshipSet]] = [
            (
                rel.target,
                rel.isUsable,
                self.model.conceptRelationshipSet(
                    XbrlConst.domainMember, rel.consecutiveLinkrole
                ),
            )
            for rel in dimensionDomainRels
        ]

        hasDefaultedDomainMember = explicitDimension in self.dimensionDefaults

        if not domainMemberTrees:
            if hasDefaultedDomainMember:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Dimension has a defaulted domain member but no domain relationships",
                        elr=elrUri,
                        concepts=(qnameOf(explicitDimension),),
                        defaultMember=qnameOf(
                            self.dimensionDefaults[explicitDimension]
                        ),
                    ),
                )
            else:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Dimension has no domain relationships",
                        elr=elrUri,
                        concepts=(qnameOf(explicitDimension),),
                    ),
                )
            return []

        dimensionHasMultipleDimensionDomainRelationships = 1 < len(dimensionDomainRels)

        members: list[QName] = []
        for domainHeadConcept, usable, domainMemberRelSet in domainMemberTrees:
            self.verifyDomainMemberTree(
                explicitDimension,
                hasDefaultedDomainMember,
                dimensionHasMultipleDimensionDomainRelationships,
                domainHeadConcept,
                domainMemberRelSet,
            )
            if usable:
                members.append(qnameOf(domainHeadConcept))
            members.extend(
                row.qname
                for row in self.walkDefinitionChildren(
                    domainHeadConcept, domainMemberRelSet, 1
                )
                if row.isUsable
            )
        return unique_list(members)

    def getDomainTreesForExplicitDimension(
        self,
        explicitDimension: ModelConcept,
        elrUri: str,
    ) -> list[dict[str, Any]]:
        """The declared shape of the domain(s) getDomainMembersForExplicitDimension()
        flattens: one entry per dimension-domain arc from `explicitDimension` in
        `elrUri`, in arc order, holding the domain head and every domain-member
        arc beneath it (see walkDefinitionRelationships()).

        Call getDomainMembersForExplicitDimension() first: this relies on its
        checks, and adds no diagnostics of its own."""
        dimensionDomainRelSet = self.model.conceptRelationshipSet(
            XbrlConst.dimensionDomain, elrUri
        )
        return [
            {
                "elr": elrUri,
                "domain": rel.targetQName,
                "order": rel.order,
                "usable": rel.isUsable,
                "members": [
                    {
                        "elr": arc.elr,
                        "parent": arc.parent,
                        "member": arc.member,
                        "order": arc.order,
                        "usable": arc.isUsable,
                    }
                    for arc in self.walkDefinitionRelationships(
                        rel.target,
                        self.model.conceptRelationshipSet(
                            XbrlConst.domainMember, rel.consecutiveLinkrole
                        ),
                    )
                ],
            }
            for rel in dimensionDomainRelSet.relationshipsFrom(explicitDimension)
        ]

    def verifyDomainMemberTree(
        self,
        explicitDimension: ModelConcept,
        hasDefaultedDomainMember: bool,
        dimensionHasMultipleDimensionDomainRelationships: bool,
        domainHeadConcept: ModelConcept,
        domainMemberRelSet: ConceptRelationshipSet,
    ) -> None:
        outgoing = domainMemberRelSet.hasRelationshipsFrom(domainHeadConcept)
        incoming = domainMemberRelSet.hasRelationshipsTo(domainHeadConcept)
        elrUri = domainMemberRelSet.linkrole

        if not outgoing:
            if (
                hasDefaultedDomainMember
                and domainHeadConcept == self.dimensionDefaults[explicitDimension]
            ):
                # Dimension pointing at domain head with no members that's
                # also the default is the standard pattern for a domain that
                # is expected to be extended by the reporting entity.
                pass
            elif dimensionHasMultipleDimensionDomainRelationships:
                # Multiple dimension-domain relationships instead of one
                # dimension-domain followed by domain-member(s) is not great
                # modelling but technically OK.
                pass
            else:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Dimension has a domain head with no outgoing domain-member relationships",
                        elr=elrUri,
                        concepts=(qnameOf(explicitDimension),),
                        domainHead=qnameOf(domainHeadConcept),
                    ),
                )

        if incoming:
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    "Dimension has a domain head with incoming domain-member relationships. How exciting!",
                    elr=elrUri,
                    concepts=(qnameOf(explicitDimension),),
                    domainHead=qnameOf(domainHeadConcept),
                ),
            )

    def getDomainMembersForEnumeration(
        self,
        elrUri: str,
        headUsable: bool,
        domainHeadConcept: ModelConcept,
        enumerationConcept: QName,
    ) -> list[QName]:
        domainHeadQName = qnameOf(domainHeadConcept)
        domainMemberRelSet = self.model.conceptRelationshipSet(
            XbrlConst.domainMember, elrUri
        )
        if elrUri not in self.model.linkrolesFor(XbrlConst.domainMember):
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    "Extensible enumeration linkrole has no domain-member relationships",
                    elr=elrUri,
                    concepts=(enumerationConcept,),
                    domainHead=domainHeadQName,
                ),
            )
        else:
            if not domainMemberRelSet.hasRelationshipsFrom(domainHeadConcept):
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Extensible enumeration domain head has no outgoing domain-member relationships",
                        elr=elrUri,
                        concepts=(enumerationConcept,),
                        domainHead=domainHeadQName,
                    ),
                )
            if domainMemberRelSet.hasRelationshipsTo(domainHeadConcept):
                # Unlike the other conditions here, this doesn't stop the
                # domain from resolving -- walkDefinitionChildren() still
                # walks correctly downward from the declared head regardless
                # of what points to it. Just a curiosity, not a defect.
                self.diagnostics.emit(
                    ArelleDiagnostic.info(
                        "Extensible enumeration domain head is not a root of the domain-member relationship set",
                        elr=elrUri,
                        concepts=(enumerationConcept,),
                        domainHead=domainHeadQName,
                    ),
                )

        members: list[QName] = []
        if headUsable:
            members.append(domainHeadQName)
        members.extend(
            row.qname
            for row in self.walkDefinitionChildren(
                domainHeadConcept, domainMemberRelSet, 1
            )
            if row.isUsable
        )
        if not (members := unique_list(members)):
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    "Extensible enumeration resolved no usable domain members",
                    elr=elrUri,
                    concepts=(enumerationConcept,),
                    domainHead=domainHeadQName,
                ),
            )
        return members

    def extractDimensionDefaults(self) -> None:
        self.cntlr.addToLog("Processing dimension defaults")
        elrsWithDefaults = self.model.linkrolesFor(XbrlConst.dimensionDefault)
        dimToElrMap: dict[ModelConcept, list[str]] = defaultdict(list)

        for elrUri in elrsWithDefaults:
            dimensionDefaultRelSet = self.model.conceptRelationshipSet(
                XbrlConst.dimensionDefault, elrUri
            )

            for d in dimensionDefaultRelSet.rootConcepts():
                dimToElrMap[d].append(elrUri)

                defaultRels = dimensionDefaultRelSet.relationshipsFrom(d)
                if len(defaultRels) != 1:
                    raise ArelleModelInconsistency(
                        ArelleDiagnostic.error(
                            "More than one default member for dimension",
                            elr=elrUri,
                            concepts=(qnameOf(d),),
                            members=[rel.targetQName for rel in defaultRels],
                        )
                    )
                m = defaultRels[0].target
                if (m0 := self.dimensionDefaults.get(d)) is not None:
                    otherElrs = dimToElrMap[d][:-1]
                    if m0 != m:
                        self.diagnostics.emit(
                            ArelleDiagnostic.warning(
                                "Inconsistent duplicate definition of dimension default",
                                elr=elrUri,
                                concepts=(qnameOf(d),),
                                member=qnameOf(m),
                                previousMember=qnameOf(m0),
                                otherElrs=otherElrs,
                            ),
                        )
                    else:
                        self.diagnostics.emit(
                            ArelleDiagnostic.info(
                                "Consistent duplicate definition of dimension default",
                                elr=elrUri,
                                concepts=(qnameOf(d),),
                                member=qnameOf(m),
                                otherElrs=otherElrs,
                            ),
                        )
                self.dimensionDefaults[d] = m

        if self.dimensionDefaults:
            self.taxonomyJson["dimensions"]["_defaults"] = {
                qnameOf(d): qnameOf(m) for d, m in self.dimensionDefaults.items()
            }
        else:
            self.diagnostics.emit(ArelleDiagnostic.info("No dimension defaults found"))

    def addConceptMetadata(self, concept: ModelConcept, jconcept: dict) -> None:
        """Add the concept's boolean flags (each written only when true) and
        its xbrli:balance (written only when declared -- see
        ValidatedModel.balanceOf())."""
        if (balance := self.model.balanceOf(concept)) is not None:
            jconcept["balance"] = balance
        meta = {
            "abstract": concept.isAbstract,
            "dimension": concept.isDimensionItem,
            "hypercube": concept.isHypercubeItem,
            "nillable": concept.isNillable,
            "numeric": concept.isNumeric,
        }
        for json_key, concept_property in meta.items():
            if concept_property is True:
                jconcept[json_key] = concept_property

    def keepLongerLabel(
        self,
        existing: str | None,
        label: str,
        *,
        elr: str | None = None,
        concepts: Iterable[QName] = (),
        **details: Any,
    ) -> str:
        """Resolve an inconsistent duplicate label by keeping the longer
        text, emitting a diagnostic. Returns `label` unchanged when there is
        no conflicting existing label."""
        if existing and existing != label:
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    "Inconsistent duplicate labels found; keeping the longer label",
                    elr=elr,
                    concepts=concepts,
                    label=label,
                    otherLabel=existing,
                    **details,
                ),
            )
            label = max(existing, label, key=len)
        return label

    def addLabels(
        self,
        concept: ModelConcept,
        jconcept: dict,
    ) -> None:
        """Add labels to the concept JSON."""
        labels: dict[str, dict[str, str]] = {}
        jconcept["labels"] = labels
        for labelRel in self.model.resourceRelationshipsFrom(
            concept, XbrlConst.conceptLabel
        ):
            label_resource = labelRel.resource
            role: str = label_resource.role or XbrlConst.standardLabel
            if (lang := label_resource.xmlLang) and (lang := lang.strip().lower()):
                # BCP47 says that xml:lang is case insensitive
                langLabels = labels.setdefault(lang, {})
                langLabels[role] = self.keepLongerLabel(
                    langLabels.get(role),
                    label_resource.stringValue.strip(),
                    concepts=(qnameOf(concept),),
                    lang=lang,
                    role=role,
                )
            else:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Label has no xml:lang so is being ignored",
                        concepts=(qnameOf(concept),),
                        role=role,
                    ),
                )

    def collectReferences(
        self,
        concept: ModelConcept,
    ) -> None:
        """Accumulate this concept's references into self._references.

        References live at the top level of the taxonomy JSON (see
        extractReferences()), not on each concept: many taxonomies cite the
        same reference -- same role, same ordered parts -- from a large
        number of concepts (measured on IFRS 2025: 7527 concept-reference
        arcs collapse to 2634 distinct references), so folding by content
        here rather than serialising one copy per citing concept avoids
        that duplication.
        """
        # Doesn't depend on refRel: computed once here rather than once per
        # reference relationship below.
        conceptQName = qnameOf(concept)
        for refRel in self.model.resourceRelationshipsFrom(
            concept, XbrlConst.conceptReference
        ):
            ref_resource = refRel.resource
            if not refRel.role:
                raise ArelleModelInconsistency(
                    ArelleDiagnostic.error(
                        "Reference resource has no role",
                        concepts=(conceptQName,),
                        resource=repr(ref_resource),
                    )
                )
            role: str = str(refRel.role)

            ref_parts: tuple[tuple[QName, str], ...] = tuple(
                (part.qname, value)
                for part in ref_resource.iterchildren()
                if (value := part.stringValue.strip())
            )
            if not ref_parts:
                continue

            # The key must be stable regardless of which document's prefixes
            # happen to be bound to the part QNames, hence clarkNotation
            # rather than str(QName) (which would use those prefixes). Kept
            # around as the entry's own sort key in extractReferences() --
            # clarkNotation rebuilds its string on every access, so this
            # avoids recomputing it there from the parts all over again.
            key = (role, tuple((p.clarkNotation, v) for p, v in ref_parts))
            entry = self._references.setdefault(
                key,
                {
                    "role": role,
                    "parts": ref_parts,
                    "concepts": set(),
                    "orders": {},
                },
            )
            entry["concepts"].add(conceptQName)
            if refRel.order != 1:
                entry["orders"][conceptQName] = refRel.order

    def extractReferences(self) -> None:
        """Write the top-level "references" list from what collectReferences()
        accumulated while walking the concepts, and "referenceRoles" for the
        roles those references use (see extractReferenceRoles())."""
        keyedReferences: list[tuple[tuple[str, tuple[tuple[str, str], ...]], dict]] = []
        for key, entry in self._references.items():
            orders: dict[QName, float] = entry["orders"]
            if orders:
                self.diagnostics.emit(
                    ArelleDiagnostic.info(
                        "Reference is cited with an arc order other than 1 by "
                        "one or more concepts; per-concept order is preserved",
                        concepts=sorted(orders, key=lambda q: q.clarkNotation),
                        role=entry["role"],
                        orders=sorted(set(orders.values())),
                    ),
                )

            jref: dict[str, Any] = {
                "role": entry["role"],
                "parts": list(entry["parts"]),
                "concepts": sorted(entry["concepts"], key=lambda q: q.clarkNotation),
            }
            if orders:
                jref["orders"] = orders
            keyedReferences.append((key, jref))

        keyedReferences.sort(key=lambda kv: kv[0])
        self.taxonomyJson["references"] = [jref for _, jref in keyedReferences]
        self.extractReferenceRoles()

    def extractReferenceRoles(self) -> None:
        """Write the top-level "referenceRoles" object: for each distinct role
        used by an extracted reference that the DTS declares a roleType for,
        that roleType's definition and generic labels -- the same two things
        extractPresentation() records for each presentation ELR, the same way,
        and in the same shape ({"definition": ..., "labels": {lang: ...}},
        "labels" only when there are any). A role with no roleType (XBRL 2.1's
        predefined reference roles need none) has no entry; a roleType with no
        link:definition has no "definition". Omitted entirely when no role has
        an entry, so a DTS citing only predefined roles bakes as before.

        Only roles that references actually use are looked at: this is not a
        listing of every roleType in the DTS."""
        roles = sorted({entry["role"] for entry in self._references.values()})
        referenceRoles: dict[str, dict[str, Any]] = {}
        for role in roles:
            if (roleType := self.model.declaredRoleType(role)) is None:
                continue
            jrole: dict[str, Any] = {}
            if (definition := roleType.definition) is not None:
                jrole["definition"] = definition
            if labels := self.getLabelsForRoleType(roleType):
                jrole["labels"] = labels
            referenceRoles[role] = jrole
        if referenceRoles:
            self.taxonomyJson["referenceRoles"] = referenceRoles

    def extractConceptsAndMetadata(self) -> None:
        self.cntlr.addToLog(
            "Processing concepts (including labels; collecting references)"
        )
        for qname, concept in self.model.itemConcepts():
            dataType, baseDataType = self.model.typeQNamesOf(concept)
            jconcept: dict[str, Any] = {
                "dataType": dataType,
                "baseDataType": baseDataType,
                "periodType": concept.periodType,
            }
            self.addConceptMetadata(concept, jconcept)
            self.addLabels(concept, jconcept)
            self.collectReferences(concept)

            if concept.isEnumeration and not concept.isEnumeration2Item:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Extensible enumerations other than 2.0 are not supported",
                        concepts=(qname,),
                    ),
                )
            if concept.isEnumeration2Item:
                headUsable = concept.isEnumDomainUsable
                linkrole = concept.enumLinkrole
                domainQName = concept.enumDomainQname
                if linkrole is None or domainQName is None:
                    raise ArelleModelInconsistency(
                        ArelleDiagnostic.error(
                            "Extensible enumeration has no enumeration domain or linkrole",
                            concepts=(qname,),
                        )
                    )
                jconcept.setdefault("other", {})["ee20DomainMembers"] = (
                    self.getDomainMembersForEnumeration(
                        linkrole,
                        headUsable,
                        self.model.concept(domainQName),
                        qname,
                    )
                )
            if concept.isTypedDimension:
                typedElement = self.model.typedDomainElementOf(concept)
                jconcept.setdefault("other", {})["typedElement"] = qnameOf(typedElement)
                self.extractTypedDomainWrapperElement(typedElement)
            self.taxonomyJson["concepts"][qname] = jconcept

    def extractTypedDomainWrapperElement(self, element: ModelConcept) -> None:
        """Add *element* -- a typed dimension's typed domain element -- to
        xs_elements, unless already added by an earlier typed dimension
        sharing the same element.

        This is a separate top-level JSON section, not another entry in
        "concepts", because a typed domain element is an xs:element, not an
        XBRL item (XBRL Dimensions 1.0 3.1.9.2 requires it NOT be one) --
        it has no periodType or balance and cannot appear in a linkbase
        arc the way a concept can, so folding it into "concepts" would
        misrepresent it as one. No labels: unlike a concept, a typed domain
        element is not something a report ever presents to a user by name.
        """
        elementQName = qnameOf(element)
        if elementQName in self.taxonomyJson["xs_elements"]:
            return
        # Not typeQNamesOf(): both ESRS's and VSME's typed dimensions declare
        # their domain element as type="xs:string" directly, which
        # typeQNamesOf() cannot resolve (Arelle never models a ModelType for
        # a bare XML Schema primitive) even though it is a genuine, named
        # type -- see typeQNamesOfTypedDomainElement() for why.
        dataType, baseDataType = self.model.typeQNamesOfTypedDomainElement(element)
        wrapper: dict[str, Any] = {
            "dataType": dataType,
            "baseDataType": baseDataType,
        }
        # Not addConceptMetadata(): abstract/dimension/hypercube/numeric are
        # all concept-only concerns that can never apply to a typed domain
        # element (XBRL Dimensions forbids it from being an item at all), so
        # writing it here would misleadingly suggest they could. nillable is
        # the only one of those flags that is actually meaningful for it.
        if element.isNillable:
            wrapper["nillable"] = True
        self.taxonomyJson["xs_elements"][elementQName] = wrapper

    def reportIsolatedConcepts(self) -> None:
        """Warn about concepts absent from the DTS's arc structure, at three
        exclusive levels (a concept is reported at its single most severe
        level, so the three lists partition the isolated concepts):

        - fully isolated: no relationship, incoming or outgoing, in any
          base set at all
        - documentation only: relationships exist, but only
          concept-label/concept-reference ones
        - not presented: has other relationships, but none in the
          presentation (parent-child) linkbase

        A concept is excluded from "not presented" when it is a domain
        member (or domain head) of an enum2 concept or explicit dimension
        that is itself presented -- such members are not normally presented
        directly, so flagging them would be noise rather than signal.
        """
        baseSets = self.model.baseSetsInDTS()
        documentationArcroles = frozenset(
            {XbrlConst.conceptLabel, XbrlConst.conceptReference}
        )

        presented: set[QName] = set()
        arcrolesByQName: dict[QName, set[str]] = {}
        for qname, concept in self.model.itemConcepts():
            touched: set[str] = set()
            for arcrole, linkrole in baseSets:
                relSet = self.model.conceptRelationshipSet(arcrole, linkrole)
                if relSet.hasRelationshipsFrom(concept) or relSet.hasRelationshipsTo(
                    concept
                ):
                    touched.add(arcrole)
            arcrolesByQName[qname] = touched
            if XbrlConst.parentChild in touched:
                presented.add(qname)

        excludedFromNotPresented = self._presentedDimensionalDomainMembers(presented)

        fullyIsolated: list[QName] = []
        documentationOnly: list[QName] = []
        notPresented: list[QName] = []
        for qname, touched in arcrolesByQName.items():
            if not touched:
                fullyIsolated.append(qname)
            elif touched <= documentationArcroles:
                documentationOnly.append(qname)
            elif (
                XbrlConst.parentChild not in touched
                and qname not in excludedFromNotPresented
            ):
                notPresented.append(qname)

        documentationOnlyText = (
            "concept(s) have only label/reference relationships "
            "(no presentation or definition)"
        )
        for text, qnames in (
            ("concept(s) have no relationship in any linkbase", fullyIsolated),
            (documentationOnlyText, documentationOnly),
            ("concept(s) are absent from the presentation linkbase", notPresented),
        ):
            if qnames:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        f"{len(qnames)} {text}",
                        concepts=sorted(qnames),
                    ),
                )

    def _presentedDimensionalDomainMembers(
        self, presented: Collection[QName]
    ) -> frozenset[QName]:
        """QNames that are a domain member (or domain head) of an enum2
        concept or explicit dimension which is itself presented."""
        excluded: set[QName] = set()

        for qname, concept in self.model.itemConcepts():
            if concept.isEnumeration2Item and qname in presented:
                # enumLinkrole/enumDomainQname are known good here:
                # extractConceptsAndMetadata() already raised if either was
                # missing for this concept.
                linkrole = concept.enumLinkrole
                domainQName = concept.enumDomainQname
                if linkrole is None or domainQName is None:
                    continue
                domainHead = self.model.concept(domainQName)
                excluded.add(domainQName)
                domainMemberRelSet = self.model.conceptRelationshipSet(
                    XbrlConst.domainMember, linkrole
                )
                excluded.update(
                    row.qname
                    for row in self.walkDefinitionChildren(
                        domainHead, domainMemberRelSet, 1
                    )
                )
            elif concept.isExplicitDimension and qname in presented:
                for linkrole in self.model.linkrolesFor(XbrlConst.dimensionDomain):
                    dimensionDomainRelSet = self.model.conceptRelationshipSet(
                        XbrlConst.dimensionDomain, linkrole
                    )
                    for rel in dimensionDomainRelSet.relationshipsFrom(concept):
                        excluded.add(rel.targetQName)
                        domainMemberRelSet = self.model.conceptRelationshipSet(
                            XbrlConst.domainMember, rel.consecutiveLinkrole
                        )
                        excluded.update(
                            row.qname
                            for row in self.walkDefinitionChildren(
                                rel.target, domainMemberRelSet, 1
                            )
                        )

        return frozenset(excluded)

    def extractDimensionDefinitions(self) -> None:
        self.cntlr.addToLog("Processing dimensions")
        # Get the hypercubes and primary items
        hypercubeArcRoles = (XbrlConst.all, XbrlConst.notAll)
        for elrUri in self.model.linkrolesFor(*hypercubeArcRoles):
            relSet = self.model.conceptRelationshipSet(hypercubeArcRoles, elrUri)
            roots = relSet.rootConcepts()
            primaryItemsByHypercube: dict[QName, set[QName]] = defaultdict(set)
            for root_concept in roots:
                for rel in relSet.relationshipsFrom(root_concept):
                    concept = rel.target
                    if not concept.isHypercubeItem:
                        raise ArelleModelInconsistency(
                            ArelleDiagnostic.error(
                                "Expected a hypercube as the target of an all/notAll relationship",
                                elr=elrUri,
                                concepts=(rel.targetQName,),
                            )
                        )
                    if rel.targetQName in self.taxonomyJson["dimensions"][elrUri]:
                        # Two different root primary items targeting the same
                        # hypercube in one ELR is a shape mireport doesn't
                        # understand (which root's primary items apply?), and
                        # would otherwise silently overwrite the first root's
                        # cube entry -- dimensions[elrUri][hypercube] is keyed
                        # by hypercube alone.
                        raise ArelleModelInconsistency(
                            ArelleDiagnostic.error(
                                "Hypercube is targeted by all/notAll relationships from more than one root primary item",
                                elr=elrUri,
                                concepts=(rel.targetQName,),
                            )
                        )
                    if not rel.isClosed:
                        self.diagnostics.emit(
                            ArelleDiagnostic.info(
                                "Hypercube is open",
                                elr=elrUri,
                                concepts=(rel.targetQName,),
                            ),
                        )
                    cube: dict[str, Any] = {
                        "primaryItems": self.getPrimaryItems(
                            rel.consecutiveLinkrole, root_concept
                        ),
                        "type": _hypercubeType(rel.arcrole, elrUri, rel.targetQName),
                        "xbrldt:contextElement": rel.contextElement,
                        "xbrldt:closed": rel.isClosed,
                    }
                    primaryItemsByHypercube[rel.targetQName].update(
                        q for _, q in cube["primaryItems"]
                    )
                    for dimensionRel in self.getDimensions(
                        rel.consecutiveLinkrole, concept, rel.isClosed
                    ):
                        dimension = dimensionRel.target
                        if dimension.isExplicitDimension:
                            cube.setdefault("explicitDimensions", {})[
                                dimensionRel.targetQName
                            ] = self.getDomainMembersForExplicitDimension(
                                dimension, dimensionRel.consecutiveLinkrole
                            )
                            cube.setdefault("explicitDimensionDomains", {})[
                                dimensionRel.targetQName
                            ] = self.getDomainTreesForExplicitDimension(
                                dimension, dimensionRel.consecutiveLinkrole
                            )
                        elif dimension.isTypedDimension:
                            cube.setdefault("typedDimensions", []).append(
                                dimensionRel.targetQName
                            )
                    self.taxonomyJson["dimensions"][elrUri][rel.targetQName] = cube

            self.reportHypercubesForLinkrole(elrUri, primaryItemsByHypercube)

    def reportHypercubesForLinkrole(
        self, elrUri: str, primaryItemsByHypercube: Mapping[QName, Collection[QName]]
    ) -> None:
        """Report a base set holding several hypercubes, warning if they share
        primary items.

        XDT conjoins a base set's hypercubes, so a primary item declared in
        more than one of them must satisfy all of them at once -- see
        Taxonomy.EffectiveHypercube in taxonomy.py, which builds exactly that
        conjunction.
        """
        if len(primaryItemsByHypercube) < 2:
            return
        hypercubes = sorted(primaryItemsByHypercube)
        if shared := _overlappingPrimaryItems(primaryItemsByHypercube):
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    f"Extended link role has {len(hypercubes)} hypercubes sharing primary items",
                    elr=elrUri,
                    concepts=hypercubes,
                    primaryItems=sorted(shared),
                    hint=(
                        "XDT conjoins a base set's hypercubes, so a primary item in "
                        "more than one of them must satisfy all of them at once."
                    ),
                ),
            )
        else:
            self.diagnostics.emit(
                ArelleDiagnostic.info(
                    f"Extended link role has {len(hypercubes)} hypercubes",
                    elr=elrUri,
                    concepts=hypercubes,
                ),
            )

    def reportDomainMemberOnlyLinkroleRoots(self) -> None:
        """Warn about an extended link role holding domain-member relationships
        but none of the dimensional arcroles (all/notAll/hypercube-dimension/
        dimension-domain), when its domain-member tree has more than one root.

        extractDimensionDefinitions() already checks all/notAll roots for the
        hypercube-bearing ELRs it walks; this covers the other ELRs that hold
        a domain-member tree by itself (e.g. an enumeration domain, or a
        general-purpose taxonomy hierarchy), where a second root is otherwise
        never noticed."""
        dimensionalArcroles = frozenset(
            {
                XbrlConst.all,
                XbrlConst.notAll,
                XbrlConst.hypercubeDimension,
                XbrlConst.dimensionDomain,
            }
        )
        arcrolesByLinkrole: dict[str, set[str]] = defaultdict(set)
        for arcrole, linkrole in self.model.baseSetsInDTS():
            arcrolesByLinkrole[linkrole].add(arcrole)

        for linkrole, arcroles in arcrolesByLinkrole.items():
            if XbrlConst.domainMember not in arcroles or arcroles & dimensionalArcroles:
                continue
            relSet = self.model.conceptRelationshipSet(XbrlConst.domainMember, linkrole)
            roots = relSet.rootConcepts()
            if len(roots) > 1:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        f"Domain-member-only extended link role has multiple ({len(roots)}) roots",
                        elr=linkrole,
                        # document order, deliberately not sorted
                        concepts=(qnameOf(root) for root in roots),
                    ),
                )

    def getLabelsForRoleType(self, roleType: ModelRoleType) -> dict[str, str]:
        labels: dict[str, str] = {}
        for labelRel in self.model.resourceRelationshipsFrom(
            roleType, XbrlConst.elementLabel
        ):
            label_resource = labelRel.resource
            if lang := label_resource.xmlLang:
                # BCP47 says that xml:lang is case insensitive
                lang = lang.lower()
                labels[lang] = self.keepLongerLabel(
                    labels.get(lang),
                    label_resource.stringValue.strip(),
                    elr=roleType.roleURI,
                    lang=lang,
                    definition=roleType.definition,
                )
        return labels

    def extractPresentation(self) -> None:
        self.cntlr.addToLog("Processing presentation network")
        for elrUri in self.model.linkrolesFor(XbrlConst.parentChild):
            self.cntlr.addToLog(f"Processing {elrUri}")
            roleType = self.model.roleType(elrUri)
            self.taxonomyJson["presentation"][elrUri] = {
                "definition": roleType.definition,
            }
            if labels := self.getLabelsForRoleType(roleType):
                self.taxonomyJson["presentation"][elrUri]["labels"] = labels
            relSet = self.model.conceptRelationshipSet(XbrlConst.parentChild, elrUri)
            roots = relSet.rootConcepts()
            match len(roots):
                case 0:
                    self.diagnostics.emit(
                        ArelleDiagnostic.warning("Presentation is empty", elr=elrUri),
                    )
                case 1:
                    pass
                case _:
                    self.diagnostics.emit(
                        ArelleDiagnostic.warning(
                            f"Presentation has multiple ({len(roots)}) roots so presentation order will be arbitrary",
                            elr=elrUri,
                            # document order, deliberately not sorted
                            concepts=(qnameOf(root) for root in roots),
                        ),
                    )
            rows: list[tuple[int, QName] | tuple[int, QName, str]] = []
            for root in roots:
                rows.append((0, qnameOf(root)))
                rows.extend(
                    (row.indent, row.qname)
                    if row.preferredLabel is None
                    else (row.indent, row.qname, row.preferredLabel)
                    for row in self.walkPresentationChildren(root, relSet, 1)
                )
            self.taxonomyJson["presentation"][elrUri]["rows"] = rows
        self.cntlr.addToLog("Processing presentation network [completed]")

    def extractCalculation(self) -> None:
        """Write the top-level "calculation" section: for each ELR holding
        summation-item arcs, every arc as a source (the total), target (the
        contributing item), weight and order.

        A flat edge list rather than a tree like "presentation": a
        calculation network need not have a single root, a concept can be
        both a total and an item, and summation-item permits cycles, so the
        arcs are kept exactly as declared and left for the reader to shape.
        Written only when at least one ELR has summation-item arcs.

        The XBRL 2.1 and Calculations 1.1 summation-item arcroles are read
        alike, as one relationship set per ELR -- as Arelle's own
        Calculations 1.1 validation reads them -- since they differ only in
        how a report is checked against them, not in what an arc says. Which
        one the DTS uses is written once, as "calculationArcrole", beside
        "calculation"; see _calculationArcrole() for a DTS that uses both."""
        self.cntlr.addToLog("Processing calculation network")
        calculation: dict[str, dict[str, Any]] = {}
        arcsByArcrole: Counter[str] = Counter()
        for elrUri in self.model.linkrolesFor(*XbrlConst.summationItems):
            relSet = self.model.conceptRelationshipSet(XbrlConst.summationItems, elrUri)
            relationships = []
            for source, rels in relSet.relationshipsBySource():
                for rel in rels:
                    arcsByArcrole[rel.arcrole] += 1
                    relationships.append(
                        {
                            "source": qnameOf(source),
                            "target": rel.targetQName,
                            "weight": self._summationWeight(elrUri, source, rel),
                            "order": rel.order,
                        }
                    )
            if relationships:
                calculation[elrUri] = {"relationships": relationships}
        if calculation:
            self.taxonomyJson["calculation"] = calculation
            self.taxonomyJson["calculationArcrole"] = self._calculationArcrole(
                arcsByArcrole
            )
        self.cntlr.addToLog("Processing calculation network [completed]")

    def _calculationArcrole(self, arcsByArcrole: Counter[str]) -> str:
        """The one summation-item arcrole to record for the whole DTS, given
        how many extracted arcs use each.

        A DTS using both is legal but unusual, so is warned about, and
        recorded as Calculations 1.1 however the arcs are split: a
        Calculations 1.1 processor treats arcs under either arcrole as
        calculation relationships, so is the only one that sees the whole
        network as extracted, where an XBRL 2.1 processor ignores the 2023
        arcs altogether. Writing every arc back under the 2003 arcrole would
        instead expose the 1.1 arcs to 2.1 semantics they were not authored
        for."""
        if len(arcsByArcrole) == 1:
            [arcrole] = arcsByArcrole
            return arcrole
        self.diagnostics.emit(
            ArelleDiagnostic.warning(
                f"Taxonomy mixes {XbrlConst.summationItem} and "
                f"{XbrlConst.summationItem11} summation-item relationships; "
                "all are extracted, recorded as Calculations 1.1",
                xbrl21Arcs=arcsByArcrole[XbrlConst.summationItem],
                calculations11Arcs=arcsByArcrole[XbrlConst.summationItem11],
                xbrl21Elrs=self.model.linkrolesFor(XbrlConst.summationItem),
                calculations11Elrs=self.model.linkrolesFor(XbrlConst.summationItem11),
            ),
        )
        return XbrlConst.summationItem11

    @staticmethod
    def _summationWeight(
        elrUri: str, source: ModelConcept, rel: ConceptRelationship
    ) -> float:
        """The arc's weight, which XBRL 2.1 section 5.2.5.2.1 requires be
        present and nonzero. Any value Arelle's own validation would have
        rejected (absent, not a number, zero) is a model inconsistency here
        rather than something to carry into the JSON -- NaN would not even
        serialise as valid JSON."""
        weight = rel.weight
        if weight is None or math.isnan(weight) or weight == 0:
            raise ArelleModelInconsistency(
                ArelleDiagnostic.error(
                    "Summation-item relationship has no valid (nonzero, numeric) weight",
                    elr=elrUri,
                    concepts=(qnameOf(source), rel.targetQName),
                    weight=weight,
                )
            )
        return weight
