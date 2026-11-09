"""A report package that carries the reporter's own extension taxonomy: Arelle, offline and given no
package for it, finds the report's schemaRef through the report package's own catalog."""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from mireport.arelle.report_info import ArelleReportProcessor
from mireport.conversionresults import Severity
from mireport.filesupport import FilelikeAndFileName
from mireport.report.reportpackage import buildReportPackage

pytestmark = pytest.mark.slow

SCHEMA = b"""<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xbrli="http://www.xbrl.org/2003/instance"
  xmlns:link="http://www.xbrl.org/2003/linkbase" targetNamespace="https://example.com/acme" elementFormDefault="qualified">
  <xsd:import namespace="http://www.xbrl.org/2003/instance" schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"/>
  <xsd:element name="Note" id="acme_Note" type="xbrli:stringItemType" substitutionGroup="xbrli:item"
    xbrli:periodType="duration" nillable="true"/>
</xsd:schema>
"""

REPORT = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
  xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:link="http://www.xbrl.org/2003/linkbase"
  xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:acme="https://example.com/acme">
<head><title>Acme</title></head>
<body>
<div style="display:none"><ix:header><ix:references>
  <link:schemaRef xlink:type="simple" xlink:href="https://example.com/acme/e.xsd"/>
</ix:references><ix:resources>
  <xbrli:context id="c"><xbrli:entity><xbrli:identifier scheme="https://example.com">acme</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
</ix:resources></ix:header></div>
<p><ix:nonNumeric name="acme:Note" contextRef="c" id="f1">Hello</ix:nonNumeric></p>
</body></html>
"""


def _extension() -> bytes:
    content = BytesIO()
    with zipfile.ZipFile(content, "w") as zf:
        zf.writestr(
            "acme/META-INF/catalog.xml",
            '<catalog xmlns="urn:oasis:names:tc:entity:xmlns:xml:catalog">'
            '<rewriteURI uriStartString="https://example.com/acme/" rewritePrefix="../example.com/acme/"/></catalog>',
        )
        zf.writestr(
            "acme/META-INF/taxonomyPackage.xml",
            '<tp:taxonomyPackage xmlns:tp="http://xbrl.org/2016/taxonomy-package" xml:lang="en">'
            "<tp:identifier>https://example.com/acme</tp:identifier>"
            '<tp:name xml:lang="en">Acme extension</tp:name>'
            '<tp:entryPoints><tp:entryPoint><tp:name xml:lang="en">Acme</tp:name>'
            '<tp:entryPointDocument href="https://example.com/acme/e.xsd"/></tp:entryPoint></tp:entryPoints>'
            "</tp:taxonomyPackage>",
        )
        zf.writestr("acme/example.com/acme/e.xsd", SCHEMA)
    return content.getvalue()


def test_the_extension_inside_the_package_is_found() -> None:
    package = buildReportPackage(
        FilelikeAndFileName(fileContent=REPORT, filename="acme.xhtml"),
        topLevel="acme",
        inline=True,
        taxonomyPackages=[_extension()],
    )
    result = ArelleReportProcessor(
        taxonomyPackages=[], workOffline=True
    ).validateReportPackage(package)
    problems = [
        m.messageText for m in result.messages if m.severity is not Severity.INFO
    ]
    assert not problems, problems
    assert result.factCount == 1
