"""What Arelle found wrong with a report's facts, which its xBRL-JSON does not say.

A fact whose value fails its datatype (a string that breaks a pattern facet, a number that is
not one) is written to xBRL-JSON with a null value, exactly as a nil fact is, and the lexical
value is lost. The plug-in sees the model, so it reports those facts here, by the id the
xBRL-JSON gives them, and `ArelleProcessingResult` hands them back as `InvalidFact`s.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from arelle import XmlValidateConst

from mireport.arelle.diagnostics import ArelleDiagnostic

if TYPE_CHECKING:
    from arelle.ModelXbrl import ModelXbrl

INVALID_FACT_KIND = "invalid-fact-value"
"""The `kind` detail of the diagnostic that carries an invalid fact."""


@dataclass(frozen=True)
class InvalidFact:
    """An item fact whose value is not valid for its datatype."""

    id: str
    """The fact's id in the xBRL-JSON: its own id, else `f` and its object index."""
    concept: str
    value: str
    """The lexical value, as the filing wrote it."""
    dataType: str | None


def invalid_fact_diagnostics(modelXbrl: ModelXbrl) -> Iterator[ArelleDiagnostic]:
    """One warning for each item fact, not nil, whose value failed validation, in the
    order of the document."""
    for fact in sorted(modelXbrl.factsInInstance, key=lambda f: f.objectIndex):
        if not fact.isItem or fact.isNil or fact.xValid != XmlValidateConst.INVALID:
            continue
        concept = fact.concept
        dataType = getattr(concept, "typeQname", None) if concept is not None else None
        yield ArelleDiagnostic.warning(
            "A fact's value is not valid for its datatype",
            concepts=(fact.qname,),
            kind=INVALID_FACT_KIND,
            factId=fact.id or f"f{fact.objectIndex}",
            value=fact.value,
            dataType=None if dataType is None else str(dataType),
        )


def as_invalid_fact(diagnostic: ArelleDiagnostic) -> InvalidFact | None:
    """The fact an `invalid_fact_diagnostics` diagnostic carries; None for any other."""
    details = diagnostic.details
    if details.get("kind") != INVALID_FACT_KIND:
        return None
    (concept,) = diagnostic.concepts
    return InvalidFact(
        id=str(details["factId"]),
        concept=str(concept),
        value=str(details["value"]),
        dataType=None if details["dataType"] is None else str(details["dataType"]),
    )
