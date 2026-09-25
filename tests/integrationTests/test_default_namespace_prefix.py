"""A namespace whose schema binds it as the default namespace first, then to a
prefix, bakes with that prefix rather than a generated ns0-style one.

Runs real Arelle over tiny schemas written to disk: the behaviour under test
is how Arelle itself assigns concept QName prefixes (the first nsmap binding
of the targetNamespace on the schema root, "" for the default one), which a
stub cannot faithfully stand in for. Assertions are on the baked JSON's
"namespaces" and "concepts", which is what Taxonomy.namespacePrefixesMap and
the concept QNames are built from; these DTSs have no presentation linkbase,
which Taxonomy.fromJSON() requires.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from mireport.arelle.taxonomy_info import callArelleForTaxonomyInfo
from mireport.conversionresults import Severity

NS = "https://example.com/2026/default-first"

SCHEMA = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns="{ns}" {bindings}
           xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:xbrli="http://www.xbrl.org/2003/instance"
           targetNamespace="{ns}" elementFormDefault="qualified">
  <xs:import namespace="http://www.xbrl.org/2003/instance"
             schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"/>
  {include}
  <xs:element name="{name}" id="{name}" type="xbrli:stringItemType"
              substitutionGroup="xbrli:item" nillable="true"
              xbrli:periodType="duration"/>
</xs:schema>
"""


def writeSchema(
    path: Path, name: str, prefix: str, *, include: str | None = None
) -> Path:
    # The default binding is declared first on purpose: that order is what
    # gives Arelle an empty concept QName prefix.
    path.write_text(
        SCHEMA.format(
            ns=NS,
            bindings=f'xmlns:{prefix}="{NS}"',
            name=name,
            include=f'<xs:include schemaLocation="{include}"/>' if include else "",
        ),
        "utf-8",
    )
    return path


def bake(tmp_path: Path, entry_point: Path) -> dict[str, Any]:
    output = tmp_path / "taxonomy.json"
    result = callArelleForTaxonomyInfo(str(entry_point), [], output)
    assert output.exists(), "\n".join(result.log_lines)
    errors = [m.messageText for m in result.messages if m.severity is Severity.ERROR]
    assert errors == []
    baked: dict[str, Any] = json.loads(output.read_text("utf-8"))
    return baked


def prefixesFor(baked: dict[str, Any], namespace: str) -> list[str]:
    return [p for p, ns in baked["namespaces"].items() if ns == namespace]


@pytest.mark.integration
def test_default_first_schema_keeps_its_explicit_prefix(tmp_path: Path) -> None:
    baked = bake(tmp_path, writeSchema(tmp_path / "foo.xsd", "Thing", "foo"))
    assert prefixesFor(baked, NS) == ["foo"]
    assert list(baked["concepts"]) == ["foo:Thing"]


@pytest.mark.integration
def test_two_documents_disagreeing_on_the_prefix_get_a_generated_one(
    tmp_path: Path,
) -> None:
    # One namespace split over two schemas (joined by xs:include, as XML
    # Schema requires), each binding it as its own default and to its own
    # explicit prefix: there is no single real prefix to recover, so neither
    # is guessed at and the existing generated-prefix fallback applies.
    writeSchema(tmp_path / "b.xsd", "Two", "b")
    entry = writeSchema(tmp_path / "a.xsd", "One", "a", include="b.xsd")
    baked = bake(tmp_path, entry)
    [prefix] = prefixesFor(baked, NS)
    assert prefix == "ns0"
    assert sorted(baked["concepts"]) == ["ns0:One", "ns0:Two"]
