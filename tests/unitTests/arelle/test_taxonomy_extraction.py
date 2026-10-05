"""Unit tests for the taxonomy extraction logic.

Success paths that require genuine Arelle lxml-backed objects are covered
end-to-end by tests/integrationTests/test_taxonomy_info_regeneration.py;
these tests cover extraction logic using lightweight stubs (see
test_model_access.py for the same approach).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from arelle import XbrlConst
from arelle.Cntlr import Cntlr
from arelle.ModelDtsObject import ModelConcept
from arelle.ModelValue import QName
from arelle.ModelXbrl import ModelXbrl
from arelle.RuntimeOptions import RuntimeOptions

from mireport.arelle.diagnostics import ArelleDiagnostic, DiagnosticCollector
from mireport.arelle.model_access import (
    ConceptRelationship,
    ConceptRelationshipSet,
    ResourceRelationship,
    ValidatedModel,
)
from mireport.arelle.support import ArelleModelInconsistency
from mireport.arelle.taxonomy_extraction import (
    DefinitionRelationship,
    DefinitionRow,
    PresentationRow,
    TaxonomyInfoExtractor,
    writeDataFile,
)
from mireport.taxonomy import CalculationArcrole, Concept, Taxonomy


def qn(local: str = "Thing", ns: str = "https://example.com/vsme") -> QName:
    return QName("vsme", ns, local)


class StubCntlr:
    def __init__(self) -> None:
        self.logMessages: list[str] = []

    def addToLog(self, message: str, **kwargs: Any) -> None:
        self.logMessages.append(message)


class StubConcept:
    def __init__(
        self,
        qname: QName,
        *,
        isEnumeration2Item: bool = False,
        enumLinkrole: str | None = None,
        enumDomainQname: QName | None = None,
        isExplicitDimension: bool = False,
        isHypercubeItem: bool = False,
        isAbstract: bool = False,
        isDimensionItem: bool = False,
        isNillable: bool = False,
        isNumeric: bool = False,
        balance: str | None = None,
    ) -> None:
        self.qname = qname
        self.isEnumeration2Item = isEnumeration2Item
        self.enumLinkrole = enumLinkrole
        self.enumDomainQname = enumDomainQname
        self.isExplicitDimension = isExplicitDimension
        self.isHypercubeItem = isHypercubeItem
        self.isAbstract = isAbstract
        self.isDimensionItem = isDimensionItem
        self.isNillable = isNillable
        self.isNumeric = isNumeric
        self.balance = balance


class StubLabelResource:
    def __init__(self, role: str | None, lang: str | None, value: str) -> None:
        self.role = role
        self.xmlLang = lang
        self.stringValue = value


class StubRoleType:
    def __init__(
        self,
        roleURI: str = "https://example.com/role",
        definition: str | None = "A role",
    ) -> None:
        self.roleURI = roleURI
        self.definition = definition


class StubValidatedModel:
    """Stands in for ValidatedModel; serves canned ResourceRelationships."""

    def __init__(
        self,
        relsByArcrole: dict[str, list[ResourceRelationship]],
        conceptRelSets: dict[Any, Any] | None = None,
        linkrolesByArcrole: dict[str, list[str]] | None = None,
        baseSets: list[tuple[str, str]] | None = None,
        items: list[tuple[QName, Any]] | None = None,
        typeQNamesByQName: dict[QName, tuple[QName, QName]] | None = None,
    ) -> None:
        self._relsByArcrole = relsByArcrole
        self._conceptRelSets = conceptRelSets or {}
        self._linkrolesByArcrole = linkrolesByArcrole or {}
        self._baseSets = baseSets or []
        self._items = items or []
        self._conceptsByQName = dict(self._items)
        self._typeQNamesByQName = typeQNamesByQName or {}
        # roleURI -> StubRoleType, for declaredRoleType(); set per test.
        self._roleTypes: dict[str, StubRoleType] = {}

    def declaredRoleType(self, roleUri: str) -> StubRoleType | None:
        return self._roleTypes.get(roleUri)

    def resourceRelationshipsFrom(
        self, source: Any, arcrole: str
    ) -> list[ResourceRelationship]:
        return self._relsByArcrole[arcrole]

    def balanceOf(self, concept: Any) -> str | None:
        # The real narrowing is ValidatedModel's concern, tested in
        # test_model_access.py; here it only needs to pass the value through.
        return concept.balance

    def typeQNamesOfTypedDomainElement(self, concept: Any) -> tuple[QName, QName]:
        return self._typeQNamesByQName[concept.qname]

    def conceptRelationshipSet(self, arcroles: Any, linkrole: str) -> Any:
        # New tests key conceptRelSets by (arcroles, linkrole) to disambiguate
        # several arcroles sharing one linkrole; older tests key by linkrole
        # alone since they only ever look up one arcrole per linkrole.
        key = (arcroles, linkrole)
        if key in self._conceptRelSets:
            return self._conceptRelSets[key]
        return self._conceptRelSets[linkrole]

    def linkrolesFor(self, *arcroles: str) -> list[str]:
        # Deduplicated, as ValidatedModel.linkrolesFor() is: a linkrole with
        # base sets for several of arcroles is listed once.
        return list(
            dict.fromkeys(
                linkrole
                for arcrole in arcroles
                for linkrole in self._linkrolesByArcrole.get(arcrole, [])
            )
        )

    def baseSetsInDTS(self) -> list[tuple[str, str]]:
        return list(self._baseSets)

    def itemConcepts(self) -> Iterator[tuple[QName, Any]]:
        yield from self._items

    def concept(self, qname: QName) -> Any:
        return self._conceptsByQName[qname]


def labelRel(resource: StubLabelResource) -> ResourceRelationship:
    return ResourceRelationship(
        resource=cast(Any, resource), role=resource.role, order=1.0
    )


class StubReferencePart:
    def __init__(self, qname: QName, value: str) -> None:
        self.qname = qname
        self.stringValue = value


class StubReferenceResource:
    def __init__(self, role: str | None, parts: list[StubReferencePart]) -> None:
        self.role = role
        self._parts = parts

    def iterchildren(self) -> list[StubReferencePart]:
        return self._parts


REF_NS = "http://www.xbrl.org/2006/ref"


def refPart(local: str, value: str, ns: str = REF_NS) -> StubReferencePart:
    return StubReferencePart(QName("ref", ns, local), value)


def refRel(
    resource: StubReferenceResource, *, order: float = 1.0
) -> ResourceRelationship:
    return ResourceRelationship(
        resource=cast(Any, resource), role=resource.role, order=order
    )


def makeExtractor(
    relsByArcrole: dict[str, list[ResourceRelationship]],
    conceptRelSets: dict[Any, Any] | None = None,
    linkrolesByArcrole: dict[str, list[str]] | None = None,
    baseSets: list[tuple[str, str]] | None = None,
    items: list[tuple[QName, Any]] | None = None,
    typeQNamesByQName: dict[QName, tuple[QName, QName]] | None = None,
) -> tuple[TaxonomyInfoExtractor, str]:
    """Build an extractor over stubs, with a diagnostics collector attached."""
    token = DiagnosticCollector.open()
    stubModel = SimpleNamespace(qnameConcepts={}, qnameTypes={})
    options = SimpleNamespace(diagnosticsToken=token)
    extractor = TaxonomyInfoExtractor(
        cast(Cntlr, StubCntlr()),
        cast(RuntimeOptions, options),
        cast(ModelXbrl, stubModel),
    )
    extractor.model = cast(
        ValidatedModel,
        StubValidatedModel(
            relsByArcrole,
            conceptRelSets,
            linkrolesByArcrole,
            baseSets,
            items,
            typeQNamesByQName,
        ),
    )
    return extractor, token


def collectedDiagnostics(token: str) -> list[ArelleDiagnostic]:
    return DiagnosticCollector.close(token)


class TestWriteDataFile:
    def write(self, tmp_path: Path, data: dict, **kwargs: Any) -> str:
        jsonPath = tmp_path / "out.json"
        writeDataFile(cast(Cntlr, StubCntlr()), jsonPath, "Test", data, **kwargs)
        return jsonPath.read_text(encoding="UTF-8")

    def test_str_path_also_accepted(self, tmp_path: Path) -> None:
        # The plugin receives the path as a str via Arelle RuntimeOptions
        # (RuntimeOptionValue does not admit Path).
        jsonPath = tmp_path / "out.json"
        writeDataFile(cast(Cntlr, StubCntlr()), str(jsonPath), "Test", {"a": 1})
        assert jsonPath.exists()

    def test_output_is_pretty_printed_with_sorted_keys(self, tmp_path: Path) -> None:
        data = {"b": [1, 2], "a": {"y": 1, "x": 2}}
        written = self.write(tmp_path, data)
        assert written == json.dumps(data, indent=2, sort_keys=True)

    def test_arelle_qname_value_serialises_to_string(self, tmp_path: Path) -> None:
        written = self.write(tmp_path, {"concept": qn("Thing")})
        assert json.loads(written) == {"concept": "vsme:Thing"}

    def test_qname_key_raises(self, tmp_path: Path) -> None:
        # The Taxonomy payload has its QName keys stringified by
        # convertRecursive before it gets here; anything else slipping
        # through is a bug and must fail loudly, not be silently tidied.
        with pytest.raises(TypeError):
            self.write(tmp_path, {qn("Thing"): "value"})

    def test_empty_data_writes_no_file(self, tmp_path: Path) -> None:
        jsonPath = tmp_path / "out.json"
        cntlr = StubCntlr()
        writeDataFile(cast(Cntlr, cntlr), jsonPath, "Test", {})
        assert not jsonPath.exists()
        assert cntlr.logMessages == ["No Test data to write"]


class TestAddLabels:
    def addLabels(
        self, labelRels: list[ResourceRelationship]
    ) -> tuple[dict, list[ArelleDiagnostic]]:
        extractor, token = makeExtractor({XbrlConst.conceptLabel: labelRels})
        jconcept: dict[str, Any] = {}
        extractor.addLabels(cast(ModelConcept, StubConcept(qn())), jconcept)
        return jconcept, collectedDiagnostics(token)

    def test_label_with_role_is_stored_under_that_role(self) -> None:
        role = "http://www.xbrl.org/2003/role/terseLabel"
        jconcept, diagnostics = self.addLabels(
            [labelRel(StubLabelResource(role, "en", "Terse"))]
        )
        assert jconcept["labels"]["en"] == {role: "Terse"}
        assert diagnostics == []

    def test_label_without_role_falls_back_to_standard_label_role(self) -> None:
        jconcept, diagnostics = self.addLabels(
            [labelRel(StubLabelResource(None, "en", "Assets"))]
        )
        assert jconcept["labels"]["en"] == {XbrlConst.standardLabel: "Assets"}
        assert diagnostics == []

    def test_lang_is_normalised_to_lower_case(self) -> None:
        jconcept, _ = self.addLabels(
            [labelRel(StubLabelResource(XbrlConst.standardLabel, "en-GB", "Assets"))]
        )
        assert list(jconcept["labels"].keys()) == ["en-gb"]

    def test_label_value_is_stripped(self) -> None:
        jconcept, _ = self.addLabels(
            [labelRel(StubLabelResource(XbrlConst.standardLabel, "en", "  Assets\n"))]
        )
        assert jconcept["labels"]["en"] == {XbrlConst.standardLabel: "Assets"}

    def test_label_without_lang_is_ignored_with_diagnostic(self) -> None:
        jconcept, diagnostics = self.addLabels(
            [labelRel(StubLabelResource(XbrlConst.standardLabel, None, "Assets"))]
        )
        assert not jconcept["labels"]
        assert len(diagnostics) == 1
        assert "no xml:lang" in diagnostics[0].text

    def test_inconsistent_duplicate_labels_keep_longer_with_diagnostic(self) -> None:
        role = XbrlConst.standardLabel
        jconcept, diagnostics = self.addLabels(
            [
                labelRel(StubLabelResource(role, "en", "Assets, total")),
                labelRel(StubLabelResource(role, "en", "Assets")),
            ]
        )
        assert jconcept["labels"]["en"] == {role: "Assets, total"}
        assert len(diagnostics) == 1
        assert "duplicate labels" in diagnostics[0].text

    def test_consistent_duplicate_labels_are_silent(self) -> None:
        role = XbrlConst.standardLabel
        jconcept, diagnostics = self.addLabels(
            [
                labelRel(StubLabelResource(role, "en", "Assets")),
                labelRel(StubLabelResource(role, "en", "Assets")),
            ]
        )
        assert jconcept["labels"]["en"] == {role: "Assets"}
        assert diagnostics == []


REFERENCE_ROLE = "http://www.xbrl.org/2003/role/reference"
EXAMPLE_ROLE = "http://www.xbrl.org/2003/role/example"


class TestReferences:
    def collect(
        self,
        extractor: TaxonomyInfoExtractor,
        concept: QName,
        rels: list[ResourceRelationship],
    ) -> None:
        # StubValidatedModel.resourceRelationshipsFrom() ignores its
        # `source` argument and serves one canned list per arcrole, so a
        # per-concept scenario has to swap that list in before each call.
        cast(Any, extractor.model)._relsByArcrole[XbrlConst.conceptReference] = rels
        extractor.collectReferences(cast(ModelConcept, StubConcept(concept)))

    def test_identical_references_on_two_concepts_fold_into_one(self) -> None:
        extractor, token = makeExtractor({XbrlConst.conceptReference: []})
        resource = StubReferenceResource(
            REFERENCE_ROLE, [refPart("Name", "ISO"), refPart("Number", "3166-1")]
        )
        self.collect(extractor, qn("A"), [refRel(resource)])
        self.collect(extractor, qn("B"), [refRel(resource)])

        extractor.extractReferences()

        refs = extractor.taxonomyJson["references"]
        assert len(refs) == 1
        assert set(refs[0]["concepts"]) == {qn("A"), qn("B")}
        assert "orders" not in refs[0]
        assert collectedDiagnostics(token) == []

    def test_same_parts_different_role_are_two_references(self) -> None:
        extractor, _ = makeExtractor({XbrlConst.conceptReference: []})
        parts = [refPart("Name", "ISO")]
        self.collect(
            extractor, qn("A"), [refRel(StubReferenceResource(REFERENCE_ROLE, parts))]
        )
        self.collect(
            extractor, qn("B"), [refRel(StubReferenceResource(EXAMPLE_ROLE, parts))]
        )

        extractor.extractReferences()

        refs = extractor.taxonomyJson["references"]
        assert len(refs) == 2
        assert {r["role"] for r in refs} == {REFERENCE_ROLE, EXAMPLE_ROLE}

    def test_order_other_than_1_is_recorded_per_concept_with_diagnostic(self) -> None:
        extractor, token = makeExtractor({XbrlConst.conceptReference: []})
        resource = StubReferenceResource(REFERENCE_ROLE, [refPart("Name", "ISO")])
        self.collect(extractor, qn("A"), [refRel(resource, order=1.0)])
        self.collect(extractor, qn("B"), [refRel(resource, order=2.0)])

        extractor.extractReferences()

        refs = extractor.taxonomyJson["references"]
        assert len(refs) == 1
        assert refs[0]["orders"] == {qn("B"): 2.0}
        diagnostics = collectedDiagnostics(token)
        assert len(diagnostics) == 1
        assert "order" in diagnostics[0].text

    def test_all_order_1_has_no_orders_key(self) -> None:
        extractor, token = makeExtractor({XbrlConst.conceptReference: []})
        resource = StubReferenceResource(REFERENCE_ROLE, [refPart("Name", "ISO")])
        self.collect(extractor, qn("A"), [refRel(resource, order=1.0)])

        extractor.extractReferences()

        assert "orders" not in extractor.taxonomyJson["references"][0]
        assert collectedDiagnostics(token) == []

    def test_empty_parts_are_dropped(self) -> None:
        extractor, _ = makeExtractor({XbrlConst.conceptReference: []})
        resource = StubReferenceResource(
            REFERENCE_ROLE, [refPart("Name", "  "), refPart("Number", "3166-1")]
        )
        self.collect(extractor, qn("A"), [refRel(resource)])

        extractor.extractReferences()

        refs = extractor.taxonomyJson["references"]
        assert len(refs) == 1
        assert refs[0]["parts"] == [(QName("ref", REF_NS, "Number"), "3166-1")]

    def test_reference_with_only_empty_parts_is_dropped_entirely(self) -> None:
        extractor, _ = makeExtractor({XbrlConst.conceptReference: []})
        resource = StubReferenceResource(REFERENCE_ROLE, [refPart("Name", "   ")])
        self.collect(extractor, qn("A"), [refRel(resource)])

        extractor.extractReferences()

        assert extractor.taxonomyJson["references"] == []

    def test_missing_role_raises(self) -> None:
        extractor, _ = makeExtractor({XbrlConst.conceptReference: []})
        resource = StubReferenceResource(None, [refPart("Name", "ISO")])
        with pytest.raises(ArelleModelInconsistency, match="no role"):
            self.collect(extractor, qn("A"), [refRel(resource)])

    def test_output_is_sorted_by_role_then_parts(self) -> None:
        extractor, _ = makeExtractor({XbrlConst.conceptReference: []})
        self.collect(
            extractor,
            qn("A"),
            [refRel(StubReferenceResource(REFERENCE_ROLE, [refPart("Name", "Z")]))],
        )
        self.collect(
            extractor,
            qn("B"),
            [refRel(StubReferenceResource(EXAMPLE_ROLE, [refPart("Name", "A")]))],
        )

        extractor.extractReferences()

        refs = extractor.taxonomyJson["references"]
        assert [r["role"] for r in refs] == [EXAMPLE_ROLE, REFERENCE_ROLE]

    def test_no_references_gives_empty_list(self) -> None:
        extractor, _ = makeExtractor({XbrlConst.conceptReference: []})
        extractor.extractReferences()
        assert extractor.taxonomyJson["references"] == []


CUSTOM_ROLE = "https://example.com/role/disclosure-framework"


class TestReferenceRoles:
    """extractReferenceRoles(), reached through extractReferences(): each
    distinct role a reference uses, if the DTS declares a roleType for it,
    gets that roleType's definition and generic labels -- as
    extractPresentation() records them for a presentation ELR."""

    def extract(
        self,
        roles: list[str],
        roleTypes: dict[str, StubRoleType],
        labelRels: list[ResourceRelationship] | None = None,
    ) -> tuple[dict[str, Any], list[ArelleDiagnostic]]:
        extractor, token = makeExtractor(
            {XbrlConst.conceptReference: [], XbrlConst.elementLabel: labelRels or []}
        )
        cast(Any, extractor.model)._roleTypes = roleTypes
        for i, role in enumerate(roles):
            resource = StubReferenceResource(role, [refPart("Name", f"N{i}")])
            TestReferences().collect(extractor, qn(f"C{i}"), [refRel(resource)])
        extractor.extractReferences()
        return extractor.taxonomyJson, collectedDiagnostics(token)

    def test_declared_role_gets_definition_and_labels(self) -> None:
        taxonomyJson, diagnostics = self.extract(
            [CUSTOM_ROLE],
            {CUSTOM_ROLE: StubRoleType(CUSTOM_ROLE, "Disclosure Framework")},
            [
                labelRel(StubLabelResource(XbrlConst.genStandardLabel, "en", "DF")),
                labelRel(StubLabelResource(XbrlConst.genStandardLabel, "fr", "CD")),
            ],
        )
        assert taxonomyJson["referenceRoles"] == {
            CUSTOM_ROLE: {
                "definition": "Disclosure Framework",
                "labels": {"en": "DF", "fr": "CD"},
            }
        }
        assert diagnostics == []

    def test_no_labels_key_without_labels(self) -> None:
        taxonomyJson, _ = self.extract(
            [CUSTOM_ROLE], {CUSTOM_ROLE: StubRoleType(CUSTOM_ROLE, "Disclosure")}
        )
        assert taxonomyJson["referenceRoles"] == {
            CUSTOM_ROLE: {"definition": "Disclosure"}
        }

    def test_no_definition_key_without_definition(self) -> None:
        taxonomyJson, _ = self.extract(
            [CUSTOM_ROLE], {CUSTOM_ROLE: StubRoleType(CUSTOM_ROLE, None)}
        )
        assert taxonomyJson["referenceRoles"] == {CUSTOM_ROLE: {}}

    def test_role_with_no_role_type_has_no_entry(self) -> None:
        taxonomyJson, _ = self.extract(
            [REFERENCE_ROLE, CUSTOM_ROLE],
            {CUSTOM_ROLE: StubRoleType(CUSTOM_ROLE, "Disclosure")},
        )
        assert set(taxonomyJson["referenceRoles"]) == {CUSTOM_ROLE}

    def test_only_predefined_roles_omits_the_section(self) -> None:
        # So a DTS citing only XBRL 2.1's predefined roles (VSME) bakes to
        # exactly the JSON it did before this section existed.
        taxonomyJson, _ = self.extract([REFERENCE_ROLE, EXAMPLE_ROLE], {})
        assert "referenceRoles" not in taxonomyJson
        assert len(taxonomyJson["references"]) == 2

    def test_role_shared_by_several_references_is_listed_once(self) -> None:
        taxonomyJson, _ = self.extract(
            [CUSTOM_ROLE, CUSTOM_ROLE],
            {CUSTOM_ROLE: StubRoleType(CUSTOM_ROLE, "Disclosure")},
        )
        assert len(taxonomyJson["references"]) == 2
        assert list(taxonomyJson["referenceRoles"]) == [CUSTOM_ROLE]

    def test_role_type_used_by_no_reference_is_not_looked_at(self) -> None:
        unused = "https://example.com/role/unused"
        taxonomyJson, _ = self.extract(
            [CUSTOM_ROLE],
            {
                CUSTOM_ROLE: StubRoleType(CUSTOM_ROLE, "Disclosure"),
                unused: StubRoleType(unused, "Unused"),
            },
        )
        assert list(taxonomyJson["referenceRoles"]) == [CUSTOM_ROLE]

    def test_role_of_a_reference_with_only_empty_parts_is_not_listed(self) -> None:
        extractor, _ = makeExtractor(
            {XbrlConst.conceptReference: [], XbrlConst.elementLabel: []}
        )
        cast(Any, extractor.model)._roleTypes = {
            CUSTOM_ROLE: StubRoleType(CUSTOM_ROLE, "Disclosure")
        }
        resource = StubReferenceResource(CUSTOM_ROLE, [refPart("Name", "  ")])
        TestReferences().collect(extractor, qn("A"), [refRel(resource)])
        extractor.extractReferences()
        assert "referenceRoles" not in extractor.taxonomyJson


class TestExtractTypedDomainWrapperElement:
    def test_adds_wrapper_entry_with_type_info(self) -> None:
        elementQName = qn("SiteIdentifierDomain")
        dataType, baseType = qn("SiteIdentifierType"), qn("string", ns=XbrlConst.xsd)
        extractor, _ = makeExtractor(
            {},
            typeQNamesByQName={elementQName: (dataType, baseType)},
        )
        element = StubConcept(elementQName, isNillable=True)

        extractor.extractTypedDomainWrapperElement(cast(ModelConcept, element))

        wrapper = extractor.taxonomyJson["xs_elements"][elementQName]
        assert wrapper["dataType"] is dataType
        assert wrapper["baseDataType"] is baseType
        assert wrapper["nillable"] is True
        assert "labels" not in wrapper

    def test_metadata_flags_omitted_when_false(self) -> None:
        elementQName = qn("PlainDomain")
        extractor, _ = makeExtractor(
            {},
            typeQNamesByQName={elementQName: (qn("string"), qn("string"))},
        )
        element = StubConcept(elementQName)

        extractor.extractTypedDomainWrapperElement(cast(ModelConcept, element))

        wrapper = extractor.taxonomyJson["xs_elements"][elementQName]
        assert "nillable" not in wrapper
        assert "abstract" not in wrapper
        assert "dimension" not in wrapper
        assert "hypercube" not in wrapper
        assert "numeric" not in wrapper

    def test_second_call_for_the_same_element_is_a_no_op(self) -> None:
        elementQName = qn("SharedDomain")
        extractor, _ = makeExtractor(
            {}, typeQNamesByQName={elementQName: (qn("string"), qn("string"))}
        )
        firstElement = StubConcept(elementQName, isNillable=True)
        extractor.extractTypedDomainWrapperElement(cast(ModelConcept, firstElement))
        firstWrapper = extractor.taxonomyJson["xs_elements"][elementQName]

        # A second typed dimension sharing the same typed domain element
        # would see a different (canned) nillable flag if re-processed --
        # this proves it is not.
        secondElement = StubConcept(elementQName, isNillable=False)
        extractor.extractTypedDomainWrapperElement(cast(ModelConcept, secondElement))

        assert extractor.taxonomyJson["xs_elements"][elementQName] is firstWrapper
        assert firstWrapper["nillable"] is True


class TestAddConceptMetadata:
    def test_true_flags_are_written(self) -> None:
        extractor, _ = makeExtractor({})
        concept = StubConcept(
            qn(),
            isAbstract=True,
            isDimensionItem=True,
            isHypercubeItem=True,
            isNillable=True,
            isNumeric=True,
        )
        jconcept: dict[str, Any] = {}

        extractor.addConceptMetadata(cast(ModelConcept, concept), jconcept)

        assert jconcept == {
            "abstract": True,
            "dimension": True,
            "hypercube": True,
            "nillable": True,
            "numeric": True,
        }

    def test_nothing_written_for_a_plain_concept(self) -> None:
        extractor, _ = makeExtractor({})
        jconcept: dict[str, Any] = {}

        extractor.addConceptMetadata(cast(ModelConcept, StubConcept(qn())), jconcept)

        assert jconcept == {}

    @pytest.mark.parametrize("balance", ["debit", "credit"])
    def test_balance_is_written_when_declared(self, balance: str) -> None:
        extractor, _ = makeExtractor({})
        concept = StubConcept(qn("Revenue"), isNumeric=True, balance=balance)
        jconcept: dict[str, Any] = {}

        extractor.addConceptMetadata(cast(ModelConcept, concept), jconcept)

        assert jconcept == {"numeric": True, "balance": balance}

    def test_balance_is_omitted_when_undeclared(self) -> None:
        extractor, _ = makeExtractor({})
        concept = StubConcept(qn("Headcount"), isNumeric=True)
        jconcept: dict[str, Any] = {}

        extractor.addConceptMetadata(cast(ModelConcept, concept), jconcept)

        assert "balance" not in jconcept


def conceptRel(
    target: StubConcept,
    *,
    isUsable: bool = True,
    preferredLabel: str | None = None,
    arcrole: str = XbrlConst.all,
    order: float = 1.0,
    weight: float | None = None,
    consecutiveLinkrole: str = "https://example.com/elr",
) -> ConceptRelationship:
    assert target.qname is not None
    return ConceptRelationship(
        target=cast(Any, target),
        targetQName=target.qname,
        arcrole=arcrole,
        consecutiveLinkrole=consecutiveLinkrole,
        isUsable=isUsable,
        preferredLabel=preferredLabel,
        contextElement=None,
        isClosed=False,
        order=order,
        weight=weight,
    )


class StubConceptRelationshipSet:
    """Serves canned ConceptRelationships; consecutiveSet stays in this set."""

    def __init__(self, relsFrom: dict[int, list[ConceptRelationship]]) -> None:
        self._relsFrom = relsFrom

    def relationshipsFrom(self, concept: Any) -> list[ConceptRelationship]:
        return self._relsFrom.get(id(concept), [])

    def consecutiveSet(self, rel: ConceptRelationship) -> StubConceptRelationshipSet:
        return self


class StubDomainMemberRelSet:
    """Serves canned domain-member relationships; consecutiveSet stays in
    this set, and hasRelationshipsFrom/To are exposed like the real
    ConceptRelationshipSet."""

    def __init__(
        self,
        relsFrom: dict[int, list[ConceptRelationship]],
        targets: set[int] | None = None,
    ) -> None:
        self._relsFrom = relsFrom
        self._targets = targets or set()

    def hasRelationshipsFrom(self, concept: Any) -> bool:
        return bool(self._relsFrom.get(id(concept)))

    def hasRelationshipsTo(self, concept: Any) -> bool:
        return id(concept) in self._targets

    def relationshipsFrom(self, concept: Any) -> list[ConceptRelationship]:
        return self._relsFrom.get(id(concept), [])

    def consecutiveSet(self, rel: ConceptRelationship) -> StubDomainMemberRelSet:
        return self


class TestTreeWalks:
    def makeWalker(self) -> TaxonomyInfoExtractor:
        extractor, token = makeExtractor({})
        collectedDiagnostics(token)
        return extractor

    def test_definition_walk_is_depth_first_with_usability(self) -> None:
        a = StubConcept(qn("A"))
        b = StubConcept(qn("B"))
        c = StubConcept(qn("C"))
        d = StubConcept(qn("D"))
        relSet = StubConceptRelationshipSet(
            {
                id(a): [conceptRel(b), conceptRel(d, isUsable=False)],
                id(b): [conceptRel(c)],
            }
        )
        rows = list(
            self.makeWalker().walkDefinitionChildren(
                cast(ModelConcept, a), cast(ConceptRelationshipSet, relSet), 1
            )
        )
        assert rows == [
            DefinitionRow(1, qn("B"), True),
            DefinitionRow(2, qn("C"), True),
            DefinitionRow(1, qn("D"), False),
        ]

    def test_definition_walk_of_leaf_is_empty(self) -> None:
        leaf = StubConcept(qn("Leaf"))
        relSet = StubConceptRelationshipSet({})
        rows = list(
            self.makeWalker().walkDefinitionChildren(
                cast(ModelConcept, leaf), cast(ConceptRelationshipSet, relSet), 1
            )
        )
        assert rows == []

    def test_presentation_walk_carries_preferred_labels(self) -> None:
        root = StubConcept(qn("Root"))
        child = StubConcept(qn("Child"))
        grandchild = StubConcept(qn("Grandchild"))
        terse = "http://www.xbrl.org/2003/role/terseLabel"
        relSet = StubConceptRelationshipSet(
            {
                id(root): [conceptRel(child, preferredLabel=terse)],
                id(child): [conceptRel(grandchild)],
            }
        )
        rows = list(
            self.makeWalker().walkPresentationChildren(
                cast(ModelConcept, root), cast(ConceptRelationshipSet, relSet), 1
            )
        )
        assert rows == [
            PresentationRow(1, qn("Child"), terse),
            PresentationRow(2, qn("Grandchild"), None),
        ]


class TestGetDomainMembersForEnumeration:
    ELR = "https://example.com/elr"
    ENUM_CONCEPT = qn("Choice")

    def getDomainMembers(
        self,
        headUsable: bool,
        domainHeadConcept: StubConcept,
        relSet: StubDomainMemberRelSet,
        *,
        linkroleHasDomainMember: bool = True,
    ) -> tuple[list[QName], list[ArelleDiagnostic]]:
        linkrolesByArcrole = (
            {XbrlConst.domainMember: [self.ELR]} if linkroleHasDomainMember else {}
        )
        extractor, token = makeExtractor(
            {}, {self.ELR: relSet}, linkrolesByArcrole=linkrolesByArcrole
        )
        result = extractor.getDomainMembersForEnumeration(
            self.ELR,
            headUsable,
            cast(ModelConcept, domainHeadConcept),
            self.ENUM_CONCEPT,
        )
        return result, collectedDiagnostics(token)

    def test_undeclared_linkrole_warns_and_resolves_no_members(self) -> None:
        head = StubConcept(qn("Domain"))
        relSet = StubDomainMemberRelSet({})
        result, diagnostics = self.getDomainMembers(
            False, head, relSet, linkroleHasDomainMember=False
        )
        assert result == []
        assert len(diagnostics) == 2
        assert "no domain-member relationships" in diagnostics[0].text
        assert "no usable domain members" in diagnostics[1].text

    def test_head_with_no_outgoing_relationships_warns(self) -> None:
        head = StubConcept(qn("Domain"))
        relSet = StubDomainMemberRelSet({})
        result, diagnostics = self.getDomainMembers(True, head, relSet)
        assert result == [qn("Domain")]
        assert len(diagnostics) == 1
        assert diagnostics[0].level == logging.WARNING
        assert "no outgoing domain-member relationships" in diagnostics[0].text

    def test_head_not_a_root_is_informational(self) -> None:
        # Unlike having no outgoing relationships, this doesn't stop the
        # domain from resolving -- it's a curiosity, not a defect.
        head = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        relSet = StubDomainMemberRelSet(
            {id(head): [conceptRel(member)]}, targets={id(head)}
        )
        result, diagnostics = self.getDomainMembers(True, head, relSet)
        assert result == [qn("Domain"), qn("Member")]
        assert len(diagnostics) == 1
        assert diagnostics[0].level == logging.INFO
        assert "not a root" in diagnostics[0].text

    def test_well_formed_domain_is_silent(self) -> None:
        head = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        relSet = StubDomainMemberRelSet({id(head): [conceptRel(member)]})
        result, diagnostics = self.getDomainMembers(False, head, relSet)
        assert result == [qn("Member")]
        assert diagnostics == []

    def test_no_usable_members_warns(self) -> None:
        head = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        relSet = StubDomainMemberRelSet(
            {id(head): [conceptRel(member, isUsable=False)]}
        )
        result, diagnostics = self.getDomainMembers(False, head, relSet)
        assert result == []
        assert len(diagnostics) == 1
        assert diagnostics[0].level == logging.WARNING
        assert "no usable domain members" in diagnostics[0].text


class TestGetLabelsForRoleType:
    def getLabels(
        self, labelRels: list[ResourceRelationship]
    ) -> tuple[dict[str, str], list[ArelleDiagnostic]]:
        extractor, token = makeExtractor({XbrlConst.elementLabel: labelRels})
        labels = extractor.getLabelsForRoleType(cast(Any, StubRoleType()))
        return labels, collectedDiagnostics(token)

    def test_labels_keyed_by_lower_cased_lang(self) -> None:
        labels, diagnostics = self.getLabels(
            [
                labelRel(StubLabelResource(XbrlConst.standardLabel, "en-GB", "Energy")),
                labelRel(StubLabelResource(XbrlConst.standardLabel, "fr", "Énergie")),
            ]
        )
        assert labels == {"en-gb": "Energy", "fr": "Énergie"}
        assert diagnostics == []

    def test_label_without_lang_is_skipped(self) -> None:
        labels, _diagnostics = self.getLabels(
            [labelRel(StubLabelResource(XbrlConst.standardLabel, None, "Energy"))]
        )
        assert labels == {}

    def test_inconsistent_duplicate_labels_keep_longer_with_diagnostic(self) -> None:
        labels, diagnostics = self.getLabels(
            [
                labelRel(StubLabelResource(XbrlConst.standardLabel, "en", "Energy")),
                labelRel(
                    StubLabelResource(XbrlConst.standardLabel, "en", "Energy usage")
                ),
            ]
        )
        assert labels == {"en": "Energy usage"}
        assert len(diagnostics) == 1
        assert "duplicate labels" in diagnostics[0].text


class StubHypercubeDimensionRelSet:
    """Serves canned roots/relationships for the hypercube-dimension arcrole."""

    def __init__(
        self,
        roots: list[StubConcept],
        relsFrom: dict[int, list[ConceptRelationship]],
        targets: set[int] | None = None,
    ) -> None:
        self._roots = roots
        self._relsFrom = relsFrom
        self._targets = targets or set()

    def rootConcepts(self) -> list[StubConcept]:
        return self._roots

    def hasRelationshipsFrom(self, concept: Any) -> bool:
        return bool(self._relsFrom.get(id(concept)))

    def hasRelationshipsTo(self, concept: Any) -> bool:
        return id(concept) in self._targets

    def relationshipsFrom(self, concept: Any) -> list[ConceptRelationship]:
        return self._relsFrom.get(id(concept), [])


class TestBakedSectionsAlwaysPresent:
    def test_presentation_is_there_even_when_the_dts_has_none(self) -> None:
        # ESEF's own taxonomy has no presentation linkbase; Taxonomy.fromJSON
        # indexes bits["presentation"] unconditionally.
        extractor, token = makeExtractor({})
        try:
            assert extractor.taxonomyJson["presentation"] == {}
            assert extractor.taxonomyJson["dimensions"] == {}
        finally:
            collectedDiagnostics(token)


class TestGetDomainMembersForExplicitDimensionWithoutDomain:
    """ESEF's esma_technical:NullDimension (a hypercube's only dimension, used to block
    default use of line items) has no dimension-domain arcs at all: legal, and so
    absent from rootConcepts() -- unlike a dimension that is a domain's target."""

    ELR = "https://example.com/elr"

    def members(
        self, dimension: StubConcept, relSet: StubHypercubeDimensionRelSet
    ) -> tuple[list[Any], list[ArelleDiagnostic]]:
        extractor, token = makeExtractor({}, {self.ELR: relSet})
        extractor.dimensionDefaults = {}
        result = extractor.getDomainMembersForExplicitDimension(
            cast(ModelConcept, dimension), self.ELR
        )
        return result, collectedDiagnostics(token)

    def test_dimension_with_no_dimension_domain_arcs_has_no_members(self) -> None:
        dimension = StubConcept(qn("NullDimension"), isExplicitDimension=True)
        other = StubConcept(qn("OtherDimension"), isExplicitDimension=True)
        domain = StubConcept(qn("Domain"))
        relSet = StubHypercubeDimensionRelSet(
            roots=[other], relsFrom={id(other): [conceptRel(domain)]}
        )
        result, diagnostics = self.members(dimension, relSet)
        assert result == []
        assert [d.level for d in diagnostics] == [logging.WARNING]
        assert "no domain relationships" in diagnostics[0].text

    def test_dimension_that_is_a_domain_target_is_still_an_error(self) -> None:
        dimension = StubConcept(qn("Misused"), isExplicitDimension=True)
        relSet = StubHypercubeDimensionRelSet(
            roots=[], relsFrom={}, targets={id(dimension)}
        )
        with pytest.raises(ArelleModelInconsistency, match="not a root"):
            self.members(dimension, relSet)


class TestGetDimensions:
    ELR = "https://example.com/elr"

    def getDimensions(
        self,
        hypercube: StubConcept,
        hypercubeIsClosed: bool,
        relSet: StubHypercubeDimensionRelSet,
    ) -> tuple[list[ConceptRelationship], list[ArelleDiagnostic]]:
        extractor, token = makeExtractor({}, {self.ELR: relSet})
        result = extractor.getDimensions(
            self.ELR, cast(ModelConcept, hypercube), hypercubeIsClosed
        )
        return result, collectedDiagnostics(token)

    def test_hypercube_with_no_dimensions_returns_empty(self) -> None:
        # A table with zero dimensions is unusual but valid: it just has no
        # outgoing hypercube-dimension relationships, and so is absent from
        # rootConcepts() entirely (whether or not other, dimensioned,
        # hypercubes share the same ELR).
        table = StubConcept(qn("EmptyTable"))
        other = StubConcept(qn("OtherTable"))
        dimension = StubConcept(qn("SomeDimension"))
        relSet = StubHypercubeDimensionRelSet(
            roots=[other],
            relsFrom={id(other): [conceptRel(dimension)]},
        )
        result, diagnostics = self.getDimensions(table, False, relSet)
        assert result == []
        assert diagnostics == []

    def test_closed_hypercube_with_no_dimensions_is_informational(self) -> None:
        # A closed, dimensionless hypercube is the WGN section 3.4 way to give
        # an otherwise-undimensioned concept full dimensional validity, so
        # this must not read as something to fix.
        table = StubConcept(qn("EmptyTable"))
        relSet = StubHypercubeDimensionRelSet(roots=[], relsFrom={})
        result, diagnostics = self.getDimensions(table, True, relSet)
        assert result == []
        assert len(diagnostics) == 1
        assert diagnostics[0].level == logging.INFO
        assert "no dimensions" in diagnostics[0].text

    def test_hypercube_with_dimensions_returns_relationships(self) -> None:
        # A second hypercube-dimension root sharing the ELR does not, by
        # itself, cause getDimensions() to emit anything: reporting on an
        # ELR having multiple hypercubes is TaxonomyInfoExtractor's job (see
        # TestReportHypercubesForLinkrole), not this method's.
        table = StubConcept(qn("Table"))
        other = StubConcept(qn("OtherTable"))
        dimension = StubConcept(qn("Dimension"))
        rel = conceptRel(dimension)
        relSet = StubHypercubeDimensionRelSet(
            roots=[table, other], relsFrom={id(table): [rel]}
        )
        result, diagnostics = self.getDimensions(table, True, relSet)
        assert result == [rel]
        assert diagnostics == []

    def test_hypercube_not_a_root_but_with_relationships_is_inconsistent(self) -> None:
        # A concept that has outgoing hypercube-dimension relationships but
        # is also the target of one (i.e. used as a dimension itself)
        # indicates real model corruption, distinct from simply having no
        # dimensions.
        table = StubConcept(qn("Table"))
        rel = conceptRel(StubConcept(qn("Dimension")))
        relSet = StubHypercubeDimensionRelSet(
            roots=[],
            relsFrom={id(table): [rel]},
            targets={id(table)},
        )
        with pytest.raises(ArelleModelInconsistency):
            self.getDimensions(table, True, relSet)


class TestReportHypercubesForLinkrole:
    ELR = "https://example.com/elr"

    def report(
        self, primaryItemsByHypercube: dict[QName, set[QName]]
    ) -> list[ArelleDiagnostic]:
        extractor, token = makeExtractor({})
        extractor.reportHypercubesForLinkrole(self.ELR, primaryItemsByHypercube)
        return collectedDiagnostics(token)

    def test_single_hypercube_is_silent(self) -> None:
        diagnostics = self.report({qn("Table"): {qn("Item")}})
        assert diagnostics == []

    def test_no_hypercubes_is_silent(self) -> None:
        diagnostics = self.report({})
        assert diagnostics == []

    def test_disjoint_primary_items_is_informational(self) -> None:
        diagnostics = self.report(
            {
                qn("TableA"): {qn("ItemA")},
                qn("TableB"): {qn("ItemB")},
            }
        )
        assert len(diagnostics) == 1
        [diagnostic] = diagnostics
        assert diagnostic.level == logging.INFO
        assert "2 hypercubes" in diagnostic.text
        assert diagnostic.concepts == (qn("TableA"), qn("TableB"))
        assert "primaryItems" not in diagnostic.details

    def test_shared_primary_item_warns(self) -> None:
        diagnostics = self.report(
            {
                qn("TableA"): {qn("Item"), qn("ItemA")},
                qn("TableB"): {qn("Item"), qn("ItemB")},
            }
        )
        assert len(diagnostics) == 1
        [diagnostic] = diagnostics
        assert diagnostic.level == logging.WARNING
        assert "sharing primary items" in diagnostic.text
        assert diagnostic.concepts == (qn("TableA"), qn("TableB"))
        assert diagnostic.details["primaryItems"] == [qn("Item")]

    def test_only_overlapping_item_is_named_among_three_hypercubes(self) -> None:
        diagnostics = self.report(
            {
                qn("TableA"): {qn("Shared"), qn("ItemA")},
                qn("TableB"): {qn("Shared"), qn("ItemB")},
                qn("TableC"): {qn("ItemC")},
            }
        )
        assert len(diagnostics) == 1
        [diagnostic] = diagnostics
        assert diagnostic.level == logging.WARNING
        assert diagnostic.concepts == (qn("TableA"), qn("TableB"), qn("TableC"))
        assert diagnostic.details["primaryItems"] == [qn("Shared")]

    def test_within_hypercube_repeats_do_not_count_as_overlap(self) -> None:
        # A primary item appearing at multiple depths within the *same*
        # hypercube's tree is not the conjoined-hypercubes problem: the caller
        # already de-duplicates into a set per hypercube, but this pins that
        # collapsing a single hypercube's own repeats never trips the warning.
        diagnostics = self.report(
            {
                qn("TableA"): {qn("Item"), qn("ItemA")},
                qn("TableB"): {qn("ItemB")},
            }
        )
        assert len(diagnostics) == 1
        [diagnostic] = diagnostics
        assert diagnostic.level == logging.INFO


class TestExtractDimensionDefinitionsHypercubeCollision:
    """Several arcs onto the same hypercube in one ELR (from several root primary
    items, or ESEF's LineItemsNotDimensionallyQualified, repeated by a company) are
    one cube over all their primary items, if the arcs agree on what else defines the
    cube; if they do not, there is no one cube to record, and
    extractDimensionDefinitions() must give up rather than guess."""

    ELR = "https://example.com/elr"

    def extract(
        self, arcs: list[tuple[StubConcept, list[ConceptRelationship]]]
    ) -> dict[str, Any]:
        allNotAllRelSet = StubHypercubeDimensionRelSet(
            roots=[root for root, _ in arcs],
            relsFrom={id(root): rels for root, rels in arcs},
        )
        extractor, token = makeExtractor(
            {},
            {
                ((XbrlConst.all, XbrlConst.notAll), self.ELR): allNotAllRelSet,
                (XbrlConst.domainMember, self.ELR): StubDomainMemberRelSet({}),
                (XbrlConst.hypercubeDimension, self.ELR): (
                    StubHypercubeDimensionRelSet(roots=[], relsFrom={})
                ),
            },
            linkrolesByArcrole={XbrlConst.all: [self.ELR]},
        )
        try:
            extractor.extractDimensionDefinitions()
            return extractor.taxonomyJson["dimensions"][self.ELR]
        finally:
            collectedDiagnostics(token)

    def test_two_roots_with_the_same_arcs_are_one_cube_over_both(self) -> None:
        rootA = StubConcept(qn("RootA"))
        rootB = StubConcept(qn("RootB"))
        table = StubConcept(qn("Table"), isHypercubeItem=True)
        cubes = self.extract(
            [(rootA, [conceptRel(table)]), (rootB, [conceptRel(table)])]
        )
        assert list(cubes) == [qn("Table")]
        assert cubes[qn("Table")]["primaryItems"] == [
            (0, qn("RootA")),
            (0, qn("RootB")),
        ]

    def test_arcs_differing_only_in_closed_are_one_closed_cube(self) -> None:
        # ESEF's own placeholder arc is open; a company repeats it with
        # xbrldt:closed="true". Both are relationships (closed is not exempt from
        # equivalence), both apply, so the cube is closed.
        root = StubConcept(qn("Placeholder"))
        table = StubConcept(qn("Table"), isHypercubeItem=True)
        closed = replace(conceptRel(table), isClosed=True)
        cubes = self.extract([(root, [conceptRel(table), closed])])
        assert cubes[qn("Table")]["xbrldt:closed"] is True
        assert cubes[qn("Table")]["primaryItems"] == [(0, qn("Placeholder"))]

    def test_roots_whose_arcs_differ_otherwise_are_inconsistent(self) -> None:
        rootA = StubConcept(qn("RootA"))
        rootB = StubConcept(qn("RootB"))
        table = StubConcept(qn("Table"), isHypercubeItem=True)
        other = replace(conceptRel(table), contextElement="scenario")
        with pytest.raises(ArelleModelInconsistency, match="differ"):
            self.extract([(rootA, [conceptRel(table)]), (rootB, [other])])


class TestExtractDimensionDefinitionsType:
    """extractDimensionDefinitions() must record which of the all/notAll
    arcroles produced each cube, since Taxonomy silently modelled a notAll
    cube as positive when this was unrecorded."""

    ELR = "https://example.com/elr"

    def extractCube(
        self, root: StubConcept, rel: ConceptRelationship
    ) -> tuple[dict[str, Any], list[ArelleDiagnostic]]:
        allNotAllRelSet = StubHypercubeDimensionRelSet(
            roots=[root], relsFrom={id(root): [rel]}
        )
        extractor, token = makeExtractor(
            {},
            {
                ((XbrlConst.all, XbrlConst.notAll), self.ELR): allNotAllRelSet,
                (XbrlConst.domainMember, self.ELR): StubDomainMemberRelSet({}),
                (XbrlConst.hypercubeDimension, self.ELR): (
                    StubHypercubeDimensionRelSet(roots=[], relsFrom={})
                ),
            },
            linkrolesByArcrole={XbrlConst.all: [self.ELR]},
        )
        extractor.extractDimensionDefinitions()
        diagnostics = collectedDiagnostics(token)
        cube = extractor.taxonomyJson["dimensions"][self.ELR][rel.targetQName]
        return cube, diagnostics

    def test_all_arc_records_positive_type(self) -> None:
        root = StubConcept(qn("Root"))
        table = StubConcept(qn("Table"), isHypercubeItem=True)
        cube, _ = self.extractCube(root, conceptRel(table, arcrole=XbrlConst.all))
        assert cube["type"] == "positive"

    def test_notall_arc_records_negative_type(self) -> None:
        root = StubConcept(qn("Root"))
        table = StubConcept(qn("Table"), isHypercubeItem=True)
        cube, _ = self.extractCube(root, conceptRel(table, arcrole=XbrlConst.notAll))
        assert cube["type"] == "negative"

    def test_unexpected_arcrole_is_inconsistent(self) -> None:
        # Positive must never be inferred by elimination: an arcrole that is
        # neither all nor notAll should be impossible to reach here (the
        # relSet is keyed by exactly those two), but if it ever happened,
        # defaulting to "positive" would be exactly the wrong failure mode.
        root = StubConcept(qn("Root"))
        table = StubConcept(qn("Table"), isHypercubeItem=True)
        rel = conceptRel(table, arcrole=XbrlConst.hypercubeDimension)
        with pytest.raises(ArelleModelInconsistency):
            self.extractCube(root, rel)

    def test_cube_dict_has_exactly_the_legacy_keys_plus_type(self) -> None:
        # A guard against silently growing the cube dict's shape: this is the
        # entire dimensional vocabulary Taxonomy.__init__ understands.
        root = StubConcept(qn("Root"))
        table = StubConcept(qn("Table"), isHypercubeItem=True)
        cube, _ = self.extractCube(root, conceptRel(table, arcrole=XbrlConst.all))
        assert set(cube.keys()) == {
            "primaryItems",
            "type",
            "xbrldt:contextElement",
            "xbrldt:closed",
        }

    def test_open_hypercube_still_emits_the_info_diagnostic(self) -> None:
        root = StubConcept(qn("Root"))
        table = StubConcept(qn("Table"), isHypercubeItem=True)
        rel = conceptRel(table, arcrole=XbrlConst.all)
        rel = replace(rel, isClosed=False)
        _, diagnostics = self.extractCube(root, rel)
        # root has no outgoing domain-member relationships in this fixture, so
        # getPrimaryItems() also warns -- irrelevant to this test, which is
        # only about the "Hypercube is open" diagnostic surviving A2's change.
        [openDiagnostic] = [d for d in diagnostics if "open" in d.text]
        assert openDiagnostic.level == logging.INFO


class TestReportDomainMemberOnlyLinkroleRoots:
    ELR = "https://example.com/elr"

    def report(
        self, baseSets: list[tuple[str, str]], conceptRelSets: dict[Any, Any]
    ) -> list[ArelleDiagnostic]:
        extractor, token = makeExtractor({}, conceptRelSets, baseSets=baseSets)
        extractor.reportDomainMemberOnlyLinkroleRoots()
        return collectedDiagnostics(token)

    def test_single_root_is_silent(self) -> None:
        relSet = StubHypercubeDimensionRelSet(
            roots=[StubConcept(qn("Root"))], relsFrom={}
        )
        diagnostics = self.report(
            [(XbrlConst.domainMember, self.ELR)], {self.ELR: relSet}
        )
        assert diagnostics == []

    def test_multiple_roots_warns(self) -> None:
        roots = [StubConcept(qn("RootA")), StubConcept(qn("RootB"))]
        relSet = StubHypercubeDimensionRelSet(roots=roots, relsFrom={})
        diagnostics = self.report(
            [(XbrlConst.domainMember, self.ELR)], {self.ELR: relSet}
        )
        assert len(diagnostics) == 1
        [diagnostic] = diagnostics
        assert "Domain-member-only" in diagnostic.text
        assert "multiple (2) roots" in diagnostic.text
        assert diagnostic.elr == self.ELR
        assert diagnostic.concepts == (qn("RootA"), qn("RootB"))

    def test_elr_with_dimensional_arcrole_is_skipped(self) -> None:
        # A domain-member set that also holds a dimension-domain arc (i.e.
        # it's the domain of an explicit dimension, not a standalone
        # hierarchy) is not this check's concern.
        roots = [StubConcept(qn("RootA")), StubConcept(qn("RootB"))]
        relSet = StubHypercubeDimensionRelSet(roots=roots, relsFrom={})
        diagnostics = self.report(
            [
                (XbrlConst.domainMember, self.ELR),
                (XbrlConst.dimensionDomain, self.ELR),
            ],
            {self.ELR: relSet},
        )
        assert diagnostics == []

    def test_elr_without_domain_member_is_skipped(self) -> None:
        diagnostics = self.report([(XbrlConst.parentChild, self.ELR)], {})
        assert diagnostics == []


class TestPresentedDimensionalDomainMembers:
    ELR = "https://example.com/elr"

    def excluded(
        self,
        items: list[tuple[QName, Any]],
        conceptRelSets: dict[Any, Any],
        presented: set[QName],
        linkrolesByArcrole: dict[str, list[str]] | None = None,
    ) -> frozenset[QName]:
        extractor, token = makeExtractor(
            {}, conceptRelSets, linkrolesByArcrole=linkrolesByArcrole, items=items
        )
        result = extractor._presentedDimensionalDomainMembers(presented)
        collectedDiagnostics(token)
        return result

    def test_enum2_domain_excluded_when_presented(self) -> None:
        enumConcept = StubConcept(
            qn("Choice"),
            isEnumeration2Item=True,
            enumLinkrole=self.ELR,
            enumDomainQname=qn("Domain"),
        )
        domainHead = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        domainMemberRelSet = StubDomainMemberRelSet(
            {id(domainHead): [conceptRel(member)]}
        )
        result = self.excluded(
            items=[
                (qn("Choice"), enumConcept),
                (qn("Domain"), domainHead),
                (qn("Member"), member),
            ],
            conceptRelSets={(XbrlConst.domainMember, self.ELR): domainMemberRelSet},
            presented={qn("Choice")},
        )
        assert result == frozenset({qn("Domain"), qn("Member")})

    def test_enum2_domain_not_excluded_when_not_presented(self) -> None:
        enumConcept = StubConcept(
            qn("Choice"),
            isEnumeration2Item=True,
            enumLinkrole=self.ELR,
            enumDomainQname=qn("Domain"),
        )
        domainHead = StubConcept(qn("Domain"))
        domainMemberRelSet = StubDomainMemberRelSet({})
        result = self.excluded(
            items=[(qn("Choice"), enumConcept), (qn("Domain"), domainHead)],
            conceptRelSets={(XbrlConst.domainMember, self.ELR): domainMemberRelSet},
            presented=set(),
        )
        assert result == frozenset()

    def test_explicit_dimension_domain_excluded_when_presented(self) -> None:
        dimension = StubConcept(qn("Axis"), isExplicitDimension=True)
        domainHead = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        dimDomainRel = conceptRel(domainHead)
        dimensionDomainRelSet = StubDomainMemberRelSet({id(dimension): [dimDomainRel]})
        domainMemberRelSet = StubDomainMemberRelSet(
            {id(domainHead): [conceptRel(member)]}
        )
        result = self.excluded(
            items=[
                (qn("Axis"), dimension),
                (qn("Domain"), domainHead),
                (qn("Member"), member),
            ],
            conceptRelSets={
                (XbrlConst.dimensionDomain, self.ELR): dimensionDomainRelSet,
                (
                    XbrlConst.domainMember,
                    dimDomainRel.consecutiveLinkrole,
                ): domainMemberRelSet,
            },
            presented={qn("Axis")},
            linkrolesByArcrole={XbrlConst.dimensionDomain: [self.ELR]},
        )
        assert result == frozenset({qn("Domain"), qn("Member")})

    def test_explicit_dimension_domain_not_excluded_when_not_presented(self) -> None:
        dimension = StubConcept(qn("Axis"), isExplicitDimension=True)
        domainHead = StubConcept(qn("Domain"))
        dimDomainRel = conceptRel(domainHead)
        dimensionDomainRelSet = StubDomainMemberRelSet({id(dimension): [dimDomainRel]})
        result = self.excluded(
            items=[(qn("Axis"), dimension), (qn("Domain"), domainHead)],
            conceptRelSets={
                (XbrlConst.dimensionDomain, self.ELR): dimensionDomainRelSet,
            },
            presented=set(),
            linkrolesByArcrole={XbrlConst.dimensionDomain: [self.ELR]},
        )
        assert result == frozenset()


class TestReportIsolatedConcepts:
    ELR = "https://example.com/elr"

    def report(
        self,
        items: list[tuple[QName, Any]],
        baseSets: list[tuple[str, str]],
        conceptRelSets: dict[Any, Any],
        linkrolesByArcrole: dict[str, list[str]] | None = None,
    ) -> list[ArelleDiagnostic]:
        extractor, token = makeExtractor(
            {},
            conceptRelSets,
            linkrolesByArcrole=linkrolesByArcrole,
            baseSets=baseSets,
            items=items,
        )
        extractor.reportIsolatedConcepts()
        return collectedDiagnostics(token)

    def test_fully_isolated_concept_is_reported(self) -> None:
        orphan = StubConcept(qn("Orphan"))
        relSet = StubDomainMemberRelSet({})
        diagnostics = self.report(
            items=[(qn("Orphan"), orphan)],
            baseSets=[(XbrlConst.parentChild, self.ELR)],
            conceptRelSets={(XbrlConst.parentChild, self.ELR): relSet},
        )
        assert len(diagnostics) == 1
        assert "no relationship in any linkbase" in diagnostics[0].text
        assert diagnostics[0].concepts == (qn("Orphan"),)

    def test_documentation_only_concept_is_reported(self) -> None:
        concept = StubConcept(qn("Documented"))
        labelRelSet = StubDomainMemberRelSet(
            {id(concept): [conceptRel(StubConcept(qn("Resource")))]}
        )
        diagnostics = self.report(
            items=[(qn("Documented"), concept)],
            baseSets=[(XbrlConst.conceptLabel, self.ELR)],
            conceptRelSets={(XbrlConst.conceptLabel, self.ELR): labelRelSet},
        )
        assert len(diagnostics) == 1
        assert "only label/reference relationships" in diagnostics[0].text
        assert diagnostics[0].concepts == (qn("Documented"),)

    def test_not_presented_concept_is_reported(self) -> None:
        concept = StubConcept(qn("NotPresented"))
        relSet = StubDomainMemberRelSet(
            {id(concept): [conceptRel(StubConcept(qn("Other")))]}
        )
        diagnostics = self.report(
            items=[(qn("NotPresented"), concept)],
            baseSets=[(XbrlConst.dimensionDefault, self.ELR)],
            conceptRelSets={(XbrlConst.dimensionDefault, self.ELR): relSet},
        )
        assert len(diagnostics) == 1
        assert "absent from the presentation linkbase" in diagnostics[0].text
        assert diagnostics[0].concepts == (qn("NotPresented"),)

    def test_presented_concept_is_silent(self) -> None:
        concept = StubConcept(qn("Presented"))
        relSet = StubDomainMemberRelSet(
            {id(concept): [conceptRel(StubConcept(qn("Child")))]}
        )
        diagnostics = self.report(
            items=[(qn("Presented"), concept)],
            baseSets=[(XbrlConst.parentChild, self.ELR)],
            conceptRelSets={(XbrlConst.parentChild, self.ELR): relSet},
        )
        assert diagnostics == []

    def test_presented_dimensions_domain_members_are_excluded(self) -> None:
        # A presented explicit dimension's domain head and members are not
        # normally presented directly; they must not show up as "not
        # presented" noise.
        dimension = StubConcept(qn("Axis"), isExplicitDimension=True)
        domainHead = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        presentationRelSet = StubDomainMemberRelSet(
            {id(dimension): [conceptRel(StubConcept(qn("Child")))]}
        )
        dimensionDomainRelSet = StubDomainMemberRelSet(
            {id(dimension): [conceptRel(domainHead)]}, targets={id(domainHead)}
        )
        domainMemberRelSet = StubDomainMemberRelSet(
            {id(domainHead): [conceptRel(member)]}, targets={id(member)}
        )
        diagnostics = self.report(
            items=[
                (qn("Axis"), dimension),
                (qn("Domain"), domainHead),
                (qn("Member"), member),
            ],
            baseSets=[
                (XbrlConst.parentChild, self.ELR),
                (XbrlConst.dimensionDomain, self.ELR),
                (XbrlConst.domainMember, self.ELR),
            ],
            conceptRelSets={
                (XbrlConst.parentChild, self.ELR): presentationRelSet,
                (XbrlConst.dimensionDomain, self.ELR): dimensionDomainRelSet,
                (XbrlConst.domainMember, self.ELR): domainMemberRelSet,
            },
            linkrolesByArcrole={XbrlConst.dimensionDomain: [self.ELR]},
        )
        assert diagnostics == []


class StubLinkroleRelSet:
    """A relationship set with a linkrole, whose consecutiveSet() follows an
    arc's consecutive linkrole into another of these (xbrldt:targetRole),
    like the real ConceptRelationshipSet."""

    def __init__(
        self,
        linkrole: str,
        relsFrom: dict[int, list[ConceptRelationship]],
        *,
        roots: list[StubConcept] | None = None,
        others: dict[str, StubLinkroleRelSet] | None = None,
    ) -> None:
        self.linkrole = linkrole
        self._relsFrom = relsFrom
        self._roots = roots or []
        self.others = others if others is not None else {}

    def rootConcepts(self) -> list[StubConcept]:
        return self._roots

    def hasRelationshipsFrom(self, concept: Any) -> bool:
        return bool(self._relsFrom.get(id(concept)))

    def hasRelationshipsTo(self, concept: Any) -> bool:
        return any(
            rel.target is concept for rels in self._relsFrom.values() for rel in rels
        )

    def relationshipsFrom(self, concept: Any) -> list[ConceptRelationship]:
        return self._relsFrom.get(id(concept), [])

    def consecutiveSet(self, rel: ConceptRelationship) -> StubLinkroleRelSet:
        if rel.consecutiveLinkrole == self.linkrole:
            return self
        return self.others[rel.consecutiveLinkrole]


class TestWalkDefinitionRelationships:
    ELR = "https://example.com/elr"
    OTHER_ELR = "https://example.com/other-elr"

    def walk(
        self, root: StubConcept, relSet: StubLinkroleRelSet
    ) -> list[DefinitionRelationship]:
        extractor, token = makeExtractor({})
        collectedDiagnostics(token)
        return list(
            extractor.walkDefinitionRelationships(
                cast(ModelConcept, root), cast(ConceptRelationshipSet, relSet)
            )
        )

    def test_multi_level_tree_keeps_parent_and_arc_order(self) -> None:
        domain, europe, france, germany, asia = (
            StubConcept(qn(n))
            for n in ("Domain", "Europe", "France", "Germany", "Asia")
        )
        relSet = StubLinkroleRelSet(
            self.ELR,
            {
                id(domain): [
                    conceptRel(europe, order=0.5, isUsable=False),
                    conceptRel(asia, order=3.0),
                ],
                id(europe): [
                    conceptRel(france, order=1.0),
                    conceptRel(germany, order=2.0),
                ],
            },
        )
        assert self.walk(domain, relSet) == [
            DefinitionRelationship(self.ELR, qn("Domain"), qn("Europe"), 0.5, False),
            DefinitionRelationship(self.ELR, qn("Europe"), qn("France"), 1.0, True),
            DefinitionRelationship(self.ELR, qn("Europe"), qn("Germany"), 2.0, True),
            DefinitionRelationship(self.ELR, qn("Domain"), qn("Asia"), 3.0, True),
        ]

    def test_member_with_two_parents_has_its_own_arcs_once(self) -> None:
        domain, a, b, shared, leaf = (
            StubConcept(qn(n)) for n in ("Domain", "A", "B", "Shared", "Leaf")
        )
        relSet = StubLinkroleRelSet(
            self.ELR,
            {
                id(domain): [conceptRel(a), conceptRel(b, order=2.0)],
                id(a): [conceptRel(shared)],
                id(b): [conceptRel(shared)],
                id(shared): [conceptRel(leaf)],
            },
        )
        assert [(r.parent, r.member) for r in self.walk(domain, relSet)] == [
            (qn("Domain"), qn("A")),
            (qn("A"), qn("Shared")),
            (qn("Shared"), qn("Leaf")),
            (qn("Domain"), qn("B")),
            (qn("B"), qn("Shared")),
        ]

    def test_directed_cycle_terminates(self) -> None:
        a, b = StubConcept(qn("A")), StubConcept(qn("B"))
        relSet = StubLinkroleRelSet(
            self.ELR, {id(a): [conceptRel(b)], id(b): [conceptRel(a)]}
        )
        assert [(r.parent, r.member) for r in self.walk(a, relSet)] == [
            (qn("A"), qn("B")),
            (qn("B"), qn("A")),
        ]

    def test_target_role_arcs_carry_their_own_elr(self) -> None:
        domain, europe, france = (
            StubConcept(qn(n)) for n in ("Domain", "Europe", "France")
        )
        other = StubLinkroleRelSet(
            self.OTHER_ELR,
            {id(europe): [conceptRel(france, consecutiveLinkrole=self.OTHER_ELR)]},
        )
        relSet = StubLinkroleRelSet(
            self.ELR,
            {id(domain): [conceptRel(europe, consecutiveLinkrole=self.OTHER_ELR)]},
            others={self.OTHER_ELR: other},
        )
        assert [(r.elr, r.member) for r in self.walk(domain, relSet)] == [
            (self.ELR, qn("Europe")),
            (self.OTHER_ELR, qn("France")),
        ]


class TestExtractDimensionDefinitionsDomainTrees:
    """extractDimensionDefinitions() writes each explicit dimension's declared
    domain tree(s) under "explicitDimensionDomains", alongside -- and without
    changing -- the flat usable "explicitDimensions" list."""

    ELR = "https://example.com/elr"
    DOMAIN_ELR = "https://example.com/domain-elr"

    def extract(
        self, domainMembers: dict[str, list[ConceptRelationship]] | None = None
    ) -> tuple[TaxonomyInfoExtractor, dict[str, Any]]:
        root = StubConcept(qn("Root"))
        table = StubConcept(qn("Table"), isHypercubeItem=True)
        axis = StubConcept(qn("RegionAxis"), isExplicitDimension=True)
        c = self.concepts

        def dm(target: StubConcept, **kwargs: Any) -> ConceptRelationship:
            # An arc's consecutive linkrole is its own ELR (no targetRole).
            return conceptRel(target, consecutiveLinkrole=self.DOMAIN_ELR, **kwargs)

        # Domain
        #   Europe (0.5, not usable)
        #     France (1)
        #     Germany (2)
        #   Asia (3)
        domainMemberRelSet = StubLinkroleRelSet(
            self.DOMAIN_ELR,
            {
                id(c["Domain"]): [
                    dm(c["Europe"], order=0.5, isUsable=False),
                    dm(c["Asia"], order=3.0),
                ],
                id(c["Europe"]): [
                    dm(c["France"], order=1.0),
                    dm(c["Germany"], order=2.0),
                ],
            },
        )
        extractor, token = makeExtractor(
            {},
            {
                ((XbrlConst.all, XbrlConst.notAll), self.ELR): (
                    StubHypercubeDimensionRelSet(
                        roots=[root],
                        relsFrom={
                            id(root): [
                                replace(conceptRel(table), contextElement="scenario")
                            ]
                        },
                    )
                ),
                (XbrlConst.domainMember, self.ELR): StubDomainMemberRelSet({}),
                (XbrlConst.hypercubeDimension, self.ELR): StubHypercubeDimensionRelSet(
                    roots=[table],
                    relsFrom={
                        id(table): [
                            conceptRel(axis, arcrole=XbrlConst.hypercubeDimension)
                        ]
                    },
                ),
                (XbrlConst.dimensionDomain, self.ELR): StubLinkroleRelSet(
                    self.ELR,
                    {
                        id(axis): [
                            conceptRel(
                                c["Domain"],
                                arcrole=XbrlConst.dimensionDomain,
                                consecutiveLinkrole=self.DOMAIN_ELR,
                            )
                        ]
                    },
                    roots=[axis],
                ),
                (XbrlConst.domainMember, self.DOMAIN_ELR): domainMemberRelSet,
            },
            linkrolesByArcrole={XbrlConst.all: [self.ELR]},
        )
        extractor.extractDimensionDefinitions()
        collectedDiagnostics(token)
        cube = extractor.taxonomyJson["dimensions"][self.ELR][qn("Table")]
        return extractor, cube

    def setup_method(self) -> None:
        self.concepts = {
            n: StubConcept(qn(n))
            for n in ("Domain", "Europe", "France", "Germany", "Asia")
        }

    def test_flat_usable_list_is_unchanged(self) -> None:
        _, cube = self.extract()
        assert cube["explicitDimensions"] == {
            qn("RegionAxis"): [qn("Domain"), qn("France"), qn("Germany"), qn("Asia")]
        }

    def test_cube_gains_only_the_domain_trees_key(self) -> None:
        _, cube = self.extract()
        assert set(cube.keys()) == {
            "primaryItems",
            "type",
            "xbrldt:contextElement",
            "xbrldt:closed",
            "explicitDimensions",
            "explicitDimensionDomains",
        }

    def test_tree_keeps_nesting_and_arc_order(self) -> None:
        _, cube = self.extract()

        def arc(parent: str, member: str, order: float, usable: bool = True) -> Any:
            return {
                "elr": self.DOMAIN_ELR,
                "parent": qn(parent),
                "member": qn(member),
                "order": order,
                "usable": usable,
            }

        assert cube["explicitDimensionDomains"] == {
            qn("RegionAxis"): [
                {
                    "elr": self.ELR,
                    "domain": qn("Domain"),
                    "order": 1.0,
                    "usable": True,
                    "members": [
                        arc("Domain", "Europe", 0.5, False),
                        arc("Europe", "France", 1.0),
                        arc("Europe", "Germany", 2.0),
                        arc("Domain", "Asia", 3.0),
                    ],
                }
            ]
        }

    def test_trees_survive_into_json_and_load_back(self, tmp_path: Path) -> None:
        # The writer/reader contract: what the extractor bakes is exactly
        # what Taxonomy.fromJSON() reads back into DimensionDomainTree.
        extractor, _ = self.extract()
        dimensions = extractor.qnameConverter.convertRecursive(
            extractor.taxonomyJson["dimensions"]
        )
        names = ("Root", "Table", "RegionAxis", *self.concepts)
        conceptKeys = extractor.qnameConverter.convertRecursive([qn(n) for n in names])
        flags: dict[str, dict[str, bool]] = {
            "Table": {"abstract": True, "hypercube": True},
            "RegionAxis": {"abstract": True, "dimension": True},
        }
        bits = {
            "entryPoint": "test://extracted-domain-trees",
            "namespaces": extractor.qnameConverter.getNamespacePrefixMap(),
            "presentation": {},
            "dimensions": dimensions,
            "concepts": {
                key: {
                    "labels": {},
                    "dataType": "xbrli:stringItemType",
                    "baseDataType": "xbrli:stringItemType",
                    "periodType": "duration",
                    **flags.get(name, {}),
                }
                for name, key in zip(names, conceptKeys, strict=True)
            },
        }
        path = tmp_path / "taxonomy.json"
        writeDataFile(cast(Cntlr, StubCntlr()), path, "taxonomy", bits)
        taxonomy = Taxonomy.fromJSON(json.loads(path.read_text()))

        byName = dict(zip(names, conceptKeys, strict=True))

        def c(name: str) -> Concept:
            return taxonomy.getConcept(byName[name])

        axis = c("RegionAxis")
        assert taxonomy.getDomainHeadsForExplicitDimension(axis) == {c("Domain")}
        [tree] = taxonomy.getDomainTreesForExplicitDimension(axis)
        assert [
            (r.parent, r.member, r.order, r.usable) for r in tree.relationships
        ] == [
            (c("Domain"), c("Europe"), 0.5, False),
            (c("Europe"), c("France"), 1.0, True),
            (c("Europe"), c("Germany"), 2.0, True),
            (c("Domain"), c("Asia"), 3.0, True),
        ]
        assert tree.members == taxonomy.getDomainMembersForExplicitDimension(axis)


class TestEnumerationDomainTree:
    """extractConceptsAndMetadata() writes each enum2 concept's declared domain
    (enum2:domain head, enum2:linkrole, enum2:headUsable and the domain-member
    tree) under "other"."ee20Domain", alongside -- and without changing -- the
    flat usable "ee20DomainMembers" list."""

    ELR = "https://example.com/enum-elr"
    TARGET_ELR = "https://example.com/enum-target-elr"

    def setup_method(self) -> None:
        self.concepts = {
            n: StubConcept(qn(n))
            for n in ("Domain", "Europe", "France", "Germany", "Asia")
        }
        self.enumConcept = StubConcept(
            qn("Choice"),
            isEnumeration2Item=True,
            enumLinkrole=self.ELR,
            enumDomainQname=qn("Domain"),
        )
        # Arelle's ModelConcept attributes the enum branch reads that the
        # shared StubConcept does not carry.
        self.enumConcept.isEnumeration = True  # type: ignore[attr-defined]
        self.enumConcept.isEnumDomainUsable = False  # type: ignore[attr-defined]
        self.enumConcept.isTypedDimension = False  # type: ignore[attr-defined]
        self.enumConcept.periodType = "duration"  # type: ignore[attr-defined]

    def relSets(self) -> dict[Any, Any]:
        c = self.concepts

        def dm(target: StubConcept, **kwargs: Any) -> ConceptRelationship:
            return conceptRel(target, consecutiveLinkrole=self.ELR, **kwargs)

        # Domain (enum2:headUsable="false")
        #   Europe (0.5, not usable; xbrldt:targetRole TARGET_ELR)
        #     France (1)
        #     Germany (2)
        #   Asia (3)
        target = StubLinkroleRelSet(
            self.TARGET_ELR,
            {
                id(c["Europe"]): [
                    conceptRel(
                        c["France"], order=1.0, consecutiveLinkrole=self.TARGET_ELR
                    ),
                    conceptRel(
                        c["Germany"], order=2.0, consecutiveLinkrole=self.TARGET_ELR
                    ),
                ]
            },
        )
        main = StubLinkroleRelSet(
            self.ELR,
            {
                id(c["Domain"]): [
                    conceptRel(
                        c["Europe"],
                        order=0.5,
                        isUsable=False,
                        consecutiveLinkrole=self.TARGET_ELR,
                    ),
                    dm(c["Asia"], order=3.0),
                ],
            },
            others={self.TARGET_ELR: target},
        )
        return {(XbrlConst.domainMember, self.ELR): main}

    def makeExtractor(self) -> tuple[TaxonomyInfoExtractor, str]:
        items = [(qn("Choice"), self.enumConcept)] + [
            (concept.qname, concept) for concept in self.concepts.values()
        ]
        extractor, token = makeExtractor(
            {},
            self.relSets(),
            linkrolesByArcrole={XbrlConst.domainMember: [self.ELR]},
            items=items,
        )
        return extractor, token

    def extractConcepts(self) -> tuple[TaxonomyInfoExtractor, dict[str, Any]]:
        extractor, token = self.makeExtractor()
        extractor.model.typeQNamesOf = lambda concept: (  # type: ignore[method-assign]
            QName("enum2", "http://xbrl.org/2020/extensible-enumerations-2.0", "x"),
            QName("xbrli", "http://www.xbrl.org/2003/instance", "tokenItemType"),
        )
        extractor.addConceptMetadata = lambda concept, jconcept: None  # type: ignore[method-assign]
        extractor.addLabels = lambda concept, jconcept: None  # type: ignore[method-assign]
        extractor.collectReferences = lambda concept: None  # type: ignore[method-assign]
        extractor.taxonomyJson["concepts"] = {}
        extractor.model._items = [(qn("Choice"), self.enumConcept)]  # type: ignore[attr-defined]
        extractor.extractConceptsAndMetadata()
        assert collectedDiagnostics(token) == []
        return extractor, extractor.taxonomyJson["concepts"][qn("Choice")]["other"]

    def arc(
        self, parent: str, member: str, order: float, usable: bool = True, **kw: str
    ) -> dict[str, Any]:
        return {
            "elr": kw.get("elr", self.ELR),
            "parent": qn(parent),
            "member": qn(member),
            "order": order,
            "usable": usable,
        }

    def test_tree_keeps_head_linkrole_nesting_and_arc_order(self) -> None:
        extractor, token = self.makeExtractor()
        tree = extractor.getDomainTreeForEnumeration(
            self.ELR, False, cast(ModelConcept, self.concepts["Domain"])
        )
        assert collectedDiagnostics(token) == []
        assert tree == {
            "elr": self.ELR,
            "domain": qn("Domain"),
            "usable": False,
            "members": [
                self.arc("Domain", "Europe", 0.5, False),
                self.arc("Europe", "France", 1.0, elr=self.TARGET_ELR),
                self.arc("Europe", "Germany", 2.0, elr=self.TARGET_ELR),
                self.arc("Domain", "Asia", 3.0),
            ],
        }

    def test_concept_gains_the_tree_and_keeps_the_flat_list(self) -> None:
        _, other = self.extractConcepts()
        assert set(other) == {"ee20DomainMembers", "ee20Domain"}
        assert other["ee20DomainMembers"] == [qn("France"), qn("Germany"), qn("Asia")]
        assert other["ee20Domain"]["domain"] == qn("Domain")
        assert other["ee20Domain"]["elr"] == self.ELR
        assert other["ee20Domain"]["usable"] is False

    def test_tree_survives_into_json_and_loads_back(self, tmp_path: Path) -> None:
        # The writer/reader contract: what the extractor bakes is exactly
        # what Taxonomy.fromJSON() reads back into EnumerationDomainTree.
        extractor, other = self.extractConcepts()
        conv = extractor.qnameConverter.convertRecursive
        names = ("Choice", *self.concepts)
        conceptKeys = conv([qn(n) for n in names])
        byName = dict(zip(names, conceptKeys, strict=True))
        bits = {
            "entryPoint": "test://extracted-enumeration-domain",
            "namespaces": extractor.qnameConverter.getNamespacePrefixMap(),
            "presentation": {},
            "dimensions": {},
            "concepts": {
                key: {
                    "labels": {},
                    "dataType": "xbrli:stringItemType",
                    "baseDataType": "xbrli:stringItemType",
                    "periodType": "duration",
                    **({"other": conv(other)} if name == "Choice" else {}),
                }
                for name, key in byName.items()
            },
        }
        path = tmp_path / "taxonomy.json"
        writeDataFile(cast(Cntlr, StubCntlr()), path, "taxonomy", bits)
        taxonomy = Taxonomy.fromJSON(json.loads(path.read_text()))

        def c(name: str) -> Concept:
            return taxonomy.getConcept(byName[name])

        choice = c("Choice")
        assert choice.getEEDomainHead() == c("Domain")
        tree = choice.getEEDomainTree()
        assert tree is not None
        assert tree.enumeration == choice
        assert tree.roleUri == self.ELR
        assert tree.usable is False
        assert [
            (r.roleUri, r.parent, r.member, r.order, r.usable)
            for r in tree.relationships
        ] == [
            (self.ELR, c("Domain"), c("Europe"), 0.5, False),
            (self.TARGET_ELR, c("Europe"), c("France"), 1.0, True),
            (self.TARGET_ELR, c("Europe"), c("Germany"), 2.0, True),
            (self.ELR, c("Domain"), c("Asia"), 3.0, True),
        ]
        assert tree.members == frozenset(choice.getEEDomain())


class StubCalculationRelSet:
    """Serves canned (source, relationships) groups, like the real
    ConceptRelationshipSet.relationshipsBySource()."""

    def __init__(self, groups: list[tuple[StubConcept, list[ConceptRelationship]]]):
        self._groups = groups

    def relationshipsBySource(
        self,
    ) -> list[tuple[StubConcept, list[ConceptRelationship]]]:
        return self._groups


class TestExtractCalculation:
    ELR = "https://example.com/role/income"
    OTHER_ELR = "https://example.com/role/revenue"
    NAMES = ("Profit", "Revenue", "Costs", "ProductSales", "ServiceSales")
    XBRL21 = XbrlConst.summationItem
    CALC11 = XbrlConst.summationItem11

    def setup_method(self) -> None:
        self.c = {n: StubConcept(qn(n), isNumeric=True) for n in self.NAMES}

    def calc(
        self,
        target: str,
        weight: float | None,
        order: float,
        arcrole: str = XbrlConst.summationItem,
    ) -> ConceptRelationship:
        return conceptRel(
            self.c[target],
            arcrole=arcrole,
            weight=weight,
            order=order,
        )

    def networks(
        self, arcrole: str = XbrlConst.summationItem, otherArcrole: str | None = None
    ) -> dict[str, StubCalculationRelSet]:
        """ELR:        Profit = Revenue - Costs
        OTHER_ELR:  Revenue = ProductSales + 0.5 * ServiceSales

        ELR's arcs under arcrole, OTHER_ELR's under otherArcrole (default:
        the same one)."""
        c = self.c
        otherArcrole = otherArcrole or arcrole
        return {
            self.ELR: StubCalculationRelSet(
                [
                    (
                        c["Profit"],
                        [
                            self.calc("Revenue", 1.0, 1.0, arcrole),
                            self.calc("Costs", -1.0, 2.0, arcrole),
                        ],
                    )
                ]
            ),
            self.OTHER_ELR: StubCalculationRelSet(
                [
                    (
                        c["Revenue"],
                        [
                            self.calc("ProductSales", 1.0, 1.0, otherArcrole),
                            self.calc("ServiceSales", 0.5, 2.0, otherArcrole),
                        ],
                    )
                ]
            ),
        }

    def extract(
        self, networks: dict[str, StubCalculationRelSet]
    ) -> tuple[TaxonomyInfoExtractor, list[ArelleDiagnostic]]:
        # Each ELR is served as the one combined relationship set over both
        # summation-item arcroles, and listed under whichever arcroles its
        # arcs use (the 2003 one for an ELR with none).
        linkrolesByArcrole: dict[str, list[str]] = {}
        for elr, relSet in networks.items():
            arcroles = {
                rel.arcrole
                for _, rels in relSet.relationshipsBySource()
                for rel in rels
            } or {self.XBRL21}
            for arcrole in sorted(arcroles):
                linkrolesByArcrole.setdefault(arcrole, []).append(elr)
        extractor, token = makeExtractor(
            {},
            {(XbrlConst.summationItems, elr): rs for elr, rs in networks.items()},
            linkrolesByArcrole=linkrolesByArcrole,
        )
        extractor.extractCalculation()
        return extractor, collectedDiagnostics(token)

    @staticmethod
    def arc(source: str, target: str, weight: float, order: float) -> Any:
        return {
            "source": qn(source),
            "target": qn(target),
            "weight": weight,
            "order": order,
        }

    def expectedCalculation(self) -> dict[str, Any]:
        arc = self.arc
        return {
            self.ELR: {
                "relationships": [
                    arc("Profit", "Revenue", 1.0, 1.0),
                    arc("Profit", "Costs", -1.0, 2.0),
                ]
            },
            self.OTHER_ELR: {
                "relationships": [
                    arc("Revenue", "ProductSales", 1.0, 1.0),
                    arc("Revenue", "ServiceSales", 0.5, 2.0),
                ]
            },
        }

    def test_arcs_keep_elr_weight_and_order(self) -> None:
        extractor, diagnostics = self.extract(self.networks())
        assert diagnostics == []
        assert extractor.taxonomyJson["calculation"] == self.expectedCalculation()

    def test_xbrl_2_1_arcrole_is_recorded_for_the_model(self) -> None:
        extractor, _ = self.extract(self.networks())
        assert extractor.taxonomyJson["calculationArcrole"] == self.XBRL21

    def test_calculations_1_1_arcs_are_extracted_the_same_way(self) -> None:
        extractor, diagnostics = self.extract(self.networks(self.CALC11))
        assert diagnostics == []
        assert extractor.taxonomyJson["calculation"] == self.expectedCalculation()
        assert extractor.taxonomyJson["calculationArcrole"] == self.CALC11

    def test_no_calculation_linkbase_writes_no_section(self) -> None:
        extractor, diagnostics = self.extract({})
        assert "calculation" not in extractor.taxonomyJson
        assert "calculationArcrole" not in extractor.taxonomyJson
        assert diagnostics == []

    def test_elr_with_no_arcs_writes_no_section(self) -> None:
        extractor, _ = self.extract({self.ELR: StubCalculationRelSet([])})
        assert "calculation" not in extractor.taxonomyJson
        assert "calculationArcrole" not in extractor.taxonomyJson

    @pytest.mark.parametrize("weight", [None, float("nan"), 0.0])
    def test_invalid_weight_is_inconsistent(self, weight: float | None) -> None:
        networks = {
            self.ELR: StubCalculationRelSet(
                [(self.c["Profit"], [self.calc("Revenue", weight, 1.0)])]
            )
        }
        with pytest.raises(ArelleModelInconsistency, match="weight"):
            self.extract(networks)

    @pytest.mark.parametrize(
        ("arcrole", "otherArcrole"),
        [
            (XbrlConst.summationItem, XbrlConst.summationItem11),
            (XbrlConst.summationItem11, XbrlConst.summationItem),
        ],
    )
    def test_mixed_arcroles_extract_every_arc_and_warn(
        self, arcrole: str, otherArcrole: str
    ) -> None:
        extractor, diagnostics = self.extract(self.networks(arcrole, otherArcrole))
        assert extractor.taxonomyJson["calculation"] == self.expectedCalculation()
        [diagnostic] = diagnostics
        assert diagnostic.level == logging.WARNING
        assert self.XBRL21 in diagnostic.text
        assert self.CALC11 in diagnostic.text
        byArcrole = {arcrole: self.ELR, otherArcrole: self.OTHER_ELR}
        assert diagnostic.details == {
            "xbrl21Arcs": 2,
            "calculations11Arcs": 2,
            "xbrl21Elrs": [byArcrole[self.XBRL21]],
            "calculations11Elrs": [byArcrole[self.CALC11]],
        }

    @pytest.mark.parametrize("calc11Arcs", [1, 2, 3])
    def test_mixed_arcroles_record_calculations_1_1_whatever_the_arc_counts(
        self, calc11Arcs: int
    ) -> None:
        # Three 2003 arcs against one, two or three 1.1 ones in the same ELR:
        # 1.1 is recorded however the arcs are split, not by majority.
        c = self.c
        items = ["Revenue", "Costs", "ProductSales"]
        rels = [
            self.calc(name, 1.0, float(i), self.XBRL21)
            for i, name in enumerate(items, 1)
        ] + [
            self.calc("ServiceSales", 1.0, float(10 + i), self.CALC11)
            for i in range(calc11Arcs)
        ]
        networks = {self.ELR: StubCalculationRelSet([(c["Profit"], rels)])}
        extractor, diagnostics = self.extract(networks)
        assert len(
            extractor.taxonomyJson["calculation"][self.ELR]["relationships"]
        ) == (3 + calc11Arcs)
        assert extractor.taxonomyJson["calculationArcrole"] == self.CALC11
        [diagnostic] = diagnostics
        assert diagnostic.level == logging.WARNING
        assert diagnostic.details["xbrl21Arcs"] == 3
        assert diagnostic.details["calculations11Arcs"] == calc11Arcs

    def test_arcroles_are_arelle_s(self) -> None:
        # The extractor writes Arelle's constants; Taxonomy reads them back
        # into CalculationArcrole without importing Arelle.
        assert CalculationArcrole.Xbrl21 == XbrlConst.summationItem
        assert CalculationArcrole.Calculations11 == XbrlConst.summationItem11

    @pytest.mark.parametrize(
        ("arcrole", "expected"),
        [
            (XbrlConst.summationItem, CalculationArcrole.Xbrl21),
            (XbrlConst.summationItem11, CalculationArcrole.Calculations11),
        ],
    )
    def test_calculation_survives_into_json_and_loads_back(
        self, tmp_path: Path, arcrole: str, expected: CalculationArcrole
    ) -> None:
        # The writer/reader contract: what the extractor bakes is exactly
        # what Taxonomy.fromJSON() reads back into CalculationGroup.
        extractor, _ = self.extract(self.networks(arcrole))
        calculation = extractor.qnameConverter.convertRecursive(
            extractor.taxonomyJson["calculation"]
        )
        conceptKeys = extractor.qnameConverter.convertRecursive(
            [qn(n) for n in self.NAMES]
        )
        bits = {
            "entryPoint": "test://extracted-calculation",
            "namespaces": extractor.qnameConverter.getNamespacePrefixMap(),
            "presentation": {},
            "dimensions": {},
            "calculation": calculation,
            "calculationArcrole": extractor.taxonomyJson["calculationArcrole"],
            "concepts": {
                key: {
                    "labels": {},
                    "dataType": "xbrli:monetaryItemType",
                    "baseDataType": "xbrli:monetaryItemType",
                    "periodType": "duration",
                    "numeric": True,
                }
                for key in conceptKeys
            },
        }
        path = tmp_path / "taxonomy.json"
        writeDataFile(cast(Cntlr, StubCntlr()), path, "taxonomy", bits)
        taxonomy = Taxonomy.fromJSON(json.loads(path.read_text()))

        byName = dict(zip(self.NAMES, conceptKeys, strict=True))

        def c(name: str) -> Concept:
            return taxonomy.getConcept(byName[name])

        assert [
            (
                group.roleUri,
                [(r.source, r.target, r.weight, r.order) for r in group.relationships],
            )
            for group in taxonomy.calculation
        ] == [
            (
                self.ELR,
                [
                    (c("Profit"), c("Revenue"), 1.0, 1.0),
                    (c("Profit"), c("Costs"), -1.0, 2.0),
                ],
            ),
            (
                self.OTHER_ELR,
                [
                    (c("Revenue"), c("ProductSales"), 1.0, 1.0),
                    (c("Revenue"), c("ServiceSales"), 0.5, 2.0),
                ],
            ),
        ]
        assert taxonomy.calculationArcrole is expected
