"""Turn an xBRL-JSON document into an InlineReport via the FactBuilder API.

The xlsx reader builds facts from workbook cells; this one builds the same facts from OIM
fact objects, so the two share everything downstream (layout, theming, aoix, Arelle).

Deliberately narrow: one entity, duration periods, and facts that carry a value. Anything else
is reported as an error rather than guessed at.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from lxml import etree
from markupsafe import Markup

from mireport.conversionresults import ConversionResultsBuilder, MessageType, Severity
from mireport.exceptions import InlineReportException
from mireport.report import InlineReport
from mireport.report.factbuilder import FactBuilder
from mireport.taxonomy import Concept, QName, Taxonomy, getTaxonomy, listTaxonomies
from mireport.typealiases import DecimalPlaces
from mireport.xml import ISO4217_NS, XBRLI_NS

L = logging.getLogger(__name__)

XHTML_NS = "http://www.w3.org/1999/xhtml"
XBRL_JSON_DOCUMENT_TYPE = "https://xbrl.org/2021/xbrl-json"
# The core aspects of an OIM fact; every other key under "dimensions" is a taxonomy dimension.
# What the xlsx reader shows for an empty enumeration set (EE_SET_DESIRED_EMPTY_PLACEHOLDER_VALUE).
EMPTY_SET_TEXT = "None"
_CORE_ASPECTS = frozenset({"concept", "entity", "period", "unit", "language"})


class XbrlJsonException(Exception):
    """The document cannot be turned into a report at all."""


class XbrlJsonProcessor:
    def __init__(
        self,
        document: Mapping[str, Any],
        results: ConversionResultsBuilder,
        *,
        strict: bool = False,
        entityNameConcept: str | None = None,
    ) -> None:
        self._document = document
        self._entityNameConcept = entityNameConcept
        self._results = results
        self._strict = strict
        self._report: InlineReport | None = None
        self._namespaces: dict[str, str] = {}
        self._periodNames: dict[str, str] = {}
        self._periodEnds: dict[date, str] = {}
        self.factsAdded = 0
        self.factsSkipped = 0

    @classmethod
    def from_file(
        cls,
        path: Path | str,
        results: ConversionResultsBuilder,
        *,
        strict: bool = False,
        entityNameConcept: str | None = None,
    ) -> XbrlJsonProcessor:
        with open(path, encoding="utf-8") as f:
            return cls(
                json.load(f),
                results,
                strict=strict,
                entityNameConcept=entityNameConcept,
            )

    @property
    def taxonomy(self) -> Taxonomy:
        assert self._report is not None
        return self._report.taxonomy

    def createReport(self) -> InlineReport:
        self._report = self._startReport()
        facts = self._document.get("facts", {})
        if not isinstance(facts, Mapping) or not facts:
            raise XbrlJsonException("The document has no facts.")
        self._setEntityAndPeriods(facts)
        self._setEntityName(facts)
        self._setDefaultCurrency(facts)
        for factId, fact in facts.items():
            if self._addFact(str(factId), fact):
                self.factsAdded += 1
            else:
                self.factsSkipped += 1
        if self.factsSkipped and self._strict:
            raise XbrlJsonException(
                f"{self.factsSkipped} of {len(facts)} facts could not be added; see the messages."
            )
        return self._report

    # -- report level ---------------------------------------------------------------------------

    def _startReport(self) -> InlineReport:
        info = self._document.get("documentInfo", {})
        if info.get("documentType") != XBRL_JSON_DOCUMENT_TYPE:
            raise XbrlJsonException(
                f"documentType must be {XBRL_JSON_DOCUMENT_TYPE}, not {info.get('documentType')!r}."
            )
        self._namespaces = dict(info.get("namespaces", {}))
        entryPoints = info.get("taxonomy", [])
        if len(entryPoints) != 1:
            raise XbrlJsonException(
                f"Exactly one taxonomy entry point is supported; the document names {len(entryPoints)}."
            )
        entryPoint = entryPoints[0]
        if entryPoint not in set(listTaxonomies()):
            raise XbrlJsonException(
                f"No baked taxonomy for entry point {entryPoint}. Bake it with "
                f"scripts/update-taxonomy.py and load it with loadTaxonomyJSON(). "
                f"Available: {sorted(listTaxonomies())}"
            )
        report = InlineReport(getTaxonomy(entryPoint))
        report.requireAllFactsRendered = self._strict
        report.addSchemaRef(entryPoint)
        return report

    def _setEntityAndPeriods(self, facts: Mapping[str, Any]) -> None:
        assert self._report is not None
        entities = {f["dimensions"].get("entity") for f in facts.values()}
        if len(entities) != 1 or None in entities:
            raise XbrlJsonException(
                f"Exactly one reporting entity is supported; found {sorted(map(str, entities))}."
            )
        prefix, _, identifier = next(iter(entities)).partition(":")
        if prefix not in self._namespaces or not identifier:
            raise XbrlJsonException(
                f"Entity {next(iter(entities))!r} has no declared scheme prefix."
            )
        self._report.setDefaultAspect("entity-identifier", identifier)
        self._report.setDefaultAspect("entity-scheme", self._namespaces[prefix])

        durations = Counter(
            text
            for f in facts.values()
            if "/" in (text := str(f["dimensions"].get("period")))
        )
        if not durations:
            raise XbrlJsonException("The report has no duration period to anchor on.")
        for n, (text, _) in enumerate(durations.most_common(), start=1):
            start, end = self._parseDuration(text)
            name = f"period{n}"
            self._report.addDurationPeriod(name, start, end)
            self._periodNames[text] = name
            self._periodEnds.setdefault(end, name)
            if n == 1:
                self._report.setDefaultPeriodName(name)

    def _periodName(self, text: str, concept: Concept) -> str:
        """The named period for a fact. An instant is only representable as the end of a
        reported duration (aoix derives it from the concept's period type)."""
        if name := self._periodNames.get(text):
            return name
        if "/" not in text and concept.periodType.value == "instant":
            instant = datetime.fromisoformat(text.replace("T24:00:00", "T00:00:00"))
            day = instant.date()
            # xBRL-JSON may write the end of day 31st as either T24:00:00 or T00:00:00 the next day
            if text.endswith("T24:00:00") is False:
                day -= timedelta(days=1)
            if name := self._periodEnds.get(day):
                return name
        raise InlineReportException(
            f"Period {text!r} is not a reported duration, or the end of one."
        )

    def _setEntityName(self, facts: Mapping[str, Any]) -> None:
        """The report title block wants a name: take it from the fact named by
        entityNameConcept (a local name), else fall back to the entity identifier."""
        assert self._report is not None
        name = None
        if self._entityNameConcept:
            for fact in facts.values():
                local = fact["dimensions"]["concept"].partition(":")[2]
                if local == self._entityNameConcept and isinstance(
                    fact.get("value"), str
                ):
                    name = fact["value"]
                    break
        self._report.setEntityName(
            name or self._report.defaultAspects["entity-identifier"]
        )

    def _setDefaultCurrency(self, facts: Mapping[str, Any]) -> None:
        """If the facts use exactly one currency, make it the report currency."""
        assert self._report is not None
        currencies = {
            unit.partition(":")[2]
            for fact in facts.values()
            if (unit := fact["dimensions"].get("unit"))
            and "/" not in unit
            and self._namespaces.get(unit.partition(":")[0]) == ISO4217_NS
        }
        if len(currencies) == 1:
            self._report.setDefaultAspect("monetary-units", next(iter(currencies)))

    @staticmethod
    def _parseDuration(text: str | None) -> tuple[date, date]:
        if not text or "/" not in text:
            raise XbrlJsonException(
                f"Only duration periods are supported; got {text!r}."
            )
        startText, endText = text.split("/", 1)
        start, end = datetime.fromisoformat(startText), datetime.fromisoformat(endText)
        # xBRL-JSON writes the instant the period ends at; a date-only report period includes that day.
        endDate = (
            end.date() - timedelta(days=1)
            if end.time() == datetime.min.time()
            else end.date()
        )
        return start.date(), endDate

    # -- facts ----------------------------------------------------------------------------------

    def _qname(self, text: str) -> QName:
        prefix, _, local = text.partition(":")
        if not local or prefix not in self._namespaces:
            raise InlineReportException(
                f"{text!r} is not a QName with a declared prefix."
            )
        return self.taxonomy.QNameMaker.fromNamespaceAndLocalName(
            self._namespaces[prefix], local
        )

    def _concept(self, text: str) -> Concept:
        try:
            return self.taxonomy.getConcept(self._qname(text))
        except KeyError as e:
            raise InlineReportException(f"{text!r} is not in the taxonomy.") from e

    def _addFact(self, factId: str, fact: Mapping[str, Any]) -> bool:
        assert self._report is not None
        dims = fact["dimensions"]
        concept: Concept | None = None
        try:
            concept = self._concept(dims["concept"])
            if (value := fact.get("value")) is None:
                raise InlineReportException("Nil facts are not supported yet.")
            fb = self._report.getFactBuilder().setConcept(concept)
            fb.setNamedPeriod(self._periodName(dims["period"], concept))
            self._setValue(fb, concept, value, fact, dims.get("unit"))
            for name, member in dims.items():
                if name not in _CORE_ASPECTS:
                    self._setDimension(fb, name, member)
            self._report.addFact(fb.buildFact())
            return True
        except (InlineReportException, KeyError, ValueError, InvalidOperation) as e:
            self._results.addMessage(
                f"Fact {factId} not added: {e}",
                Severity.WARNING,
                MessageType.Conversion,
                taxonomy_concept=concept,
            )
            return False

    def _setValue(
        self,
        fb: FactBuilder,
        concept: Concept,
        value: Any,
        fact: Mapping[str, Any],
        unit: str | None,
    ) -> None:
        if concept.isEnumerationSingle:
            member = self._concept(str(value))
            fb.setHiddenValue(member.expandedName).setValue(member.getStandardLabel())
        elif concept.isEnumerationSet:
            members = [self._concept(m) for m in str(value).split()]
            fb.setHiddenValue(" ".join(sorted(m.expandedName for m in members)))
            # An empty set is a valid value, but a fact needs something human readable.
            fb.setValue(
                "\n".join(m.getStandardLabel() for m in members) or EMPTY_SET_TEXT
            )
        elif concept.isBoolean:
            fb.setValue(
                value if isinstance(value, bool) else str(value).lower() == "true"
            )
        elif concept.isNumeric:
            self._setNumeric(fb, concept, value, fact.get("decimals"), unit)
        elif concept.isTextblock:
            fb.setValue(self._xhtml(str(value)))
        else:
            fb.setValue(str(value))

    @staticmethod
    def _xhtml(text: str) -> Markup:
        """A text block's value is XHTML. It is emitted as markup, not escaped text, so it must be
        well-formed: a broken fragment would make the whole report unreadable."""
        try:
            etree.fromstring(f"<div xmlns='{XHTML_NS}'>{text}</div>")
        except etree.XMLSyntaxError as e:
            raise InlineReportException(
                f"Text block is not well-formed XHTML: {e}"
            ) from e
        return Markup(text)

    def _setNumeric(
        self,
        fb: FactBuilder,
        concept: Concept,
        value: Any,
        decimals: Any,
        unit: str | None,
    ) -> None:
        # OIM convention: a dimensionless value omits the unit, and xbrli:pure is implied.
        self._setUnit(fb, unit or "xbrli:pure")
        number = Decimal(str(value))
        asNumber: int | float = (
            int(number) if number == number.to_integral() else float(number)
        )
        # No decimals means no accuracy was claimed; INF keeps every digit as written.
        places: DecimalPlaces = (
            "INF"
            if decimals is None or str(decimals).upper() == "INF"
            else int(decimals)
        )
        if concept.dataType.localName == "percentItemType":
            # Shown as a percentage (x100, with ix:scale -2) but stored as given. Not
            # setPercentageValue(): it takes display decimals and adds 2, and the decimals of an
            # xBRL-JSON fact are already the XBRL ones, so the report would claim more precision.
            shown = number * 100
            fb.setValue(
                int(shown) if shown == shown.to_integral() else float(shown)
            ).setScale(-2).setDecimals(places)
        else:
            fb.setValue(asNumber).setDecimals(places)

    def _setUnit(self, fb: FactBuilder, unit: str) -> None:
        if unit == "xbrli:pure":
            fb.setSimpleUnit(
                self.taxonomy.QNameMaker.fromNamespaceAndLocalName(XBRLI_NS, "pure")
            )
            return
        numerator, _, denominator = unit.partition("/")
        if denominator:
            fb.setComplexUnit(self._qname(numerator), self._qname(denominator))
            return
        measure = self._qname(numerator)
        if self.taxonomy.UTR.validCurrency(measure):
            fb.setCurrency(measure)
        else:
            fb.setSimpleUnit(measure)

    def _setDimension(self, fb: FactBuilder, name: str, member: Any) -> None:
        dimension = self._concept(name)
        if dimension.isTypedDimension:
            fb.setTypedDimension(dimension, str(member))
        elif dimension.isExplicitDimension:
            fb.setExplicitDimension(dimension, self._concept(str(member)))
        else:
            raise InlineReportException(f"{name} is not a dimension.")
