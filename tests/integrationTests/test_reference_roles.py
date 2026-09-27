"""The roleType a DTS declares for a custom reference role -- its
link:definition and its generic labels -- bakes into "referenceRoles" and comes
back through Taxonomy.getReferenceRole(), the same way a presentation ELR's does
through PresentationGroup.

Runs real Arelle over a tiny hand-written DTS: what is under test is that the
roleType Arelle resolves for a reference resource's role, and the element-label
generic arcs from it, are the ones extracted -- which a stub cannot faithfully
stand in for. The DTS cites three roles:

    framework:  declared, with a definition and en/fr generic labels
    bare:       declared, with no link:definition and no labels
    reference:  XBRL 2.1's predefined http://www.xbrl.org/2003/role/reference,
                which has (and needs) no roleType at all
"""

import json
from typing import Any

import pytest
from arelle import XbrlConst

from mireport.arelle.taxonomy_info import callArelleForTaxonomyInfo
from mireport.conversionresults import Severity
from mireport.taxonomy import Taxonomy

NS = "https://example.com/2026/refroles"
FRAMEWORK = f"{NS}/reference/disclosure-framework"
BARE = f"{NS}/reference/bare"
PREDEFINED = "http://www.xbrl.org/2003/role/reference"

SCHEMA = f"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:xbrli="http://www.xbrl.org/2003/instance"
           xmlns:link="http://www.xbrl.org/2003/linkbase"
           xmlns:xlink="http://www.w3.org/1999/xlink"
           xmlns:rr="{NS}"
           targetNamespace="{NS}" elementFormDefault="qualified">
  <xs:annotation>
    <xs:appinfo>
      <link:roleType roleURI="{FRAMEWORK}" id="framework">
        <link:definition>Disclosure Framework Reference</link:definition>
        <link:usedOn>link:reference</link:usedOn>
      </link:roleType>
      <link:roleType roleURI="{BARE}" id="bare">
        <link:usedOn>link:reference</link:usedOn>
      </link:roleType>
      <link:linkbaseRef xlink:type="simple" xlink:href="ref.xml"
          xlink:role="http://www.xbrl.org/2003/role/referenceLinkbaseRef"
          xlink:arcrole="http://www.w3.org/1999/xlink/properties/linkbase"/>
      <link:linkbaseRef xlink:type="simple" xlink:href="gla.xml"
          xlink:arcrole="http://www.w3.org/1999/xlink/properties/linkbase"/>
    </xs:appinfo>
  </xs:annotation>
  <xs:import namespace="http://www.xbrl.org/2003/instance"
             schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"/>
  <xs:element name="A" id="A" type="xbrli:stringItemType"
      substitutionGroup="xbrli:item" nillable="true" xbrli:periodType="duration"/>
  <xs:element name="B" id="B" type="xbrli:stringItemType"
      substitutionGroup="xbrli:item" nillable="true" xbrli:periodType="duration"/>
</xs:schema>
"""


def _reference(concept: str, role: str, name: str) -> str:
    return (
        f'<link:loc xlink:type="locator" xlink:href="rr.xsd#{concept}"'
        f' xlink:label="loc_{concept}_{name}"/>'
        f'<link:referenceArc xlink:type="arc"'
        f' xlink:arcrole="http://www.xbrl.org/2003/arcrole/concept-reference"'
        f' xlink:from="loc_{concept}_{name}" xlink:to="ref_{concept}_{name}"/>'
        f'<link:reference xlink:type="resource" xlink:label="ref_{concept}_{name}"'
        f' xlink:role="{role}"><ref:Name>{name}</ref:Name></link:reference>'
    )


REFERENCE_LINKBASE = f"""<?xml version="1.0" encoding="UTF-8"?>
<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
               xmlns:xlink="http://www.w3.org/1999/xlink"
               xmlns:ref="http://www.xbrl.org/2006/ref"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xsi:schemaLocation="http://www.xbrl.org/2006/ref http://www.xbrl.org/2006/ref-2006-02-27.xsd">
  <link:roleRef roleURI="{FRAMEWORK}" xlink:type="simple" xlink:href="rr.xsd#framework"/>
  <link:roleRef roleURI="{BARE}" xlink:type="simple" xlink:href="rr.xsd#bare"/>
  <link:referenceLink xlink:type="extended" xlink:role="http://www.xbrl.org/2003/role/link">
    {_reference("A", FRAMEWORK, "DF")}
    {_reference("B", FRAMEWORK, "DF2")}
    {_reference("A", BARE, "Bare")}
    {_reference("B", PREDEFINED, "Standard")}
  </link:referenceLink>
</link:linkbase>
"""

GENERIC_LABEL_LINKBASE = f"""<?xml version="1.0" encoding="UTF-8"?>
<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
               xmlns:xlink="http://www.w3.org/1999/xlink"
               xmlns:gen="http://xbrl.org/2008/generic"
               xmlns:label="http://xbrl.org/2008/label"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xsi:schemaLocation="http://xbrl.org/2008/generic http://www.xbrl.org/2008/generic-link.xsd http://xbrl.org/2008/label http://www.xbrl.org/2008/generic-label.xsd">
  <link:roleRef roleURI="{XbrlConst.genStandardLabel}" xlink:type="simple"
      xlink:href="http://www.xbrl.org/2008/generic-label.xsd#standard-label"/>
  <link:arcroleRef arcroleURI="{XbrlConst.elementLabel}" xlink:type="simple"
      xlink:href="http://www.xbrl.org/2008/generic-label.xsd#element-label"/>
  <gen:link xlink:type="extended" xlink:role="http://www.xbrl.org/2003/role/link">
    <link:loc xlink:type="locator" xlink:href="rr.xsd#framework" xlink:label="role"/>
    <gen:arc xlink:type="arc" xlink:arcrole="{XbrlConst.elementLabel}"
        xlink:from="role" xlink:to="label"/>
    <label:label xlink:type="resource" xlink:label="label"
        xlink:role="{XbrlConst.genStandardLabel}" xml:lang="en">Disclosure framework</label:label>
    <label:label xlink:type="resource" xlink:label="label"
        xlink:role="{XbrlConst.genStandardLabel}" xml:lang="fr">Cadre de publication</label:label>
  </gen:link>
</link:linkbase>
"""


@pytest.fixture(scope="module")
def baked(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp_path = tmp_path_factory.mktemp("refroles")
    (tmp_path / "ref.xml").write_text(REFERENCE_LINKBASE, "utf-8")
    (tmp_path / "gla.xml").write_text(GENERIC_LABEL_LINKBASE, "utf-8")
    entry = tmp_path / "rr.xsd"
    entry.write_text(SCHEMA, "utf-8")
    output = tmp_path / "taxonomy.json"
    result = callArelleForTaxonomyInfo(str(entry), [], output)
    assert output.exists(), "\n".join(result.log_lines)
    errors = [m.messageText for m in result.messages if m.severity is Severity.ERROR]
    assert errors == []
    data: dict[str, Any] = json.loads(output.read_text("utf-8"))
    return data


@pytest.mark.integration
def test_reference_roles_section(baked: dict[str, Any]) -> None:
    assert baked["referenceRoles"] == {
        BARE: {},
        FRAMEWORK: {
            "definition": "Disclosure Framework Reference",
            "labels": {"en": "Disclosure framework", "fr": "Cadre de publication"},
        },
    }
    # The references themselves are unchanged: still one flat list.
    assert sorted(r["role"] for r in baked["references"]) == sorted(
        [BARE, PREDEFINED, FRAMEWORK, FRAMEWORK]
    )


@pytest.mark.integration
def test_get_reference_role(baked: dict[str, Any]) -> None:
    # This DTS has no presentation linkbase, which Taxonomy.fromJSON()
    # requires.
    taxonomy = Taxonomy.fromJSON({"presentation": {}, **baked})
    framework = taxonomy.getReferenceRole(FRAMEWORK)
    assert framework is not None
    assert framework.definition == "Disclosure Framework Reference"
    assert framework.getLabel("fr") == "Cadre de publication"
    bare = taxonomy.getReferenceRole(BARE)
    assert bare is not None and bare.definition is None and bare.labels == {}
    assert taxonomy.getReferenceRole(PREDEFINED) is None
    assert {r.role for r in taxonomy.references} == {FRAMEWORK, BARE, PREDEFINED}
    assert len(taxonomy.references) == 4
