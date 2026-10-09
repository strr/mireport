"""What a report needs from Arelle, named semantically.

Callers deal in `ArelleCapability` values. Only this module knows which Arelle
plug-in provides each one. `detect_capabilities` reads the report package itself,
so no caller needs a table of regimes or publishers.
"""

from __future__ import annotations

import os
import zipfile
from enum import StrEnum
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from mireport.arelle.support import ArelleRelatedException
from mireport.filesupport import FilelikeAndFileName

if TYPE_CHECKING:
    from collections.abc import Iterable

PLUGIN_DIRECTORY_ENVIRONMENT_VARIABLE = "MIREPORT_ARELLE_PLUGINS"

# Inline XBRL 1.1, and 1.0 which EDINET still uses.
_INLINE_XBRL_NAMESPACES = (
    b"http://www.xbrl.org/2013/inlineXBRL",
    b"http://www.xbrl.org/2008/inlineXBRL",
)
_SEC_TRANSFORMATION_NAMESPACE = b"http://www.sec.gov/inlineXBRL/transformation/"
_INLINE_DOCUMENT_SUFFIXES = frozenset({".htm", ".html", ".xhtml", ".xht"})
# Namespace declarations are on the root element, so the start of a document is enough.
_HEAD_BYTES = 64 * 1024


class ArelleCapability(StrEnum):
    """A feature of a report that Arelle needs a plug-in to read."""

    # A report made of several inline XBRL documents (for example EDINET's PublicDoc).
    INLINE_DOCUMENT_SET = "inline-document-set"
    # Inline transformations in the SEC's registry, which Arelle itself does not define.
    SEC_INLINE_TRANSFORMS = "sec-inline-transforms"


class MissingPluginError(ArelleRelatedException):
    """A report needs a plug-in that is not available."""


def default_plugin_directory() -> Path | None:
    """The directory holding plug-ins Arelle does not bundle, if one is configured."""
    configured = os.environ.get(PLUGIN_DIRECTORY_ENVIRONMENT_VARIABLE)
    return Path(configured) if configured else None


def _inline_document_heads(package: FilelikeAndFileName) -> list[bytes]:
    """The start of each inline XBRL document in the package's reports."""
    stream = BytesIO(package.fileContent)
    if not zipfile.is_zipfile(stream):
        return []
    heads: list[bytes] = []
    with zipfile.ZipFile(stream) as zf:
        for info in zf.infolist():
            path = PurePosixPath(info.filename)
            if info.is_dir() or "reports" not in path.parts[:-1]:
                continue
            if path.suffix.lower() not in _INLINE_DOCUMENT_SUFFIXES:
                continue
            with zf.open(info) as f:
                head = f.read(_HEAD_BYTES)
            if any(namespace in head for namespace in _INLINE_XBRL_NAMESPACES):
                heads.append(head)
    return heads


def detect_capabilities(package: FilelikeAndFileName) -> frozenset[ArelleCapability]:
    """What reading this report package needs, found from the package's own content."""
    heads = _inline_document_heads(package)
    found: set[ArelleCapability] = set()
    if len(heads) > 1:
        found.add(ArelleCapability.INLINE_DOCUMENT_SET)
    if any(_SEC_TRANSFORMATION_NAMESPACE in head for head in heads):
        found.add(ArelleCapability.SEC_INLINE_TRANSFORMS)
    return frozenset(found)


def plugins_for(
    capabilities: Iterable[ArelleCapability], pluginDirectory: Path | None
) -> list[str]:
    """The Arelle plug-in specifications that provide the capabilities, in a stable order."""
    wanted = set(capabilities)
    plugins: list[str] = []
    for capability in ArelleCapability:
        if capability not in wanted:
            continue
        if capability is ArelleCapability.INLINE_DOCUMENT_SET:
            plugins.append("inlineXbrlDocumentSet")
        elif capability is ArelleCapability.SEC_INLINE_TRANSFORMS:
            plugins.append(_edgar_transform_plugin(pluginDirectory))
    return plugins


def _edgar_transform_plugin(pluginDirectory: Path | None) -> str:
    what = (
        "SEC inline transformations need the 'transform' plug-in of Arelle's EDGAR "
        "plug-in (https://github.com/Arelle/EDGAR), which Arelle does not bundle"
    )
    if pluginDirectory is None:
        raise MissingPluginError(
            f"{what}; no plug-in directory is configured "
            f"(set {PLUGIN_DIRECTORY_ENVIRONMENT_VARIABLE})."
        )
    transform = pluginDirectory / "EDGAR" / "transform"
    if not (transform / "__init__.py").is_file():
        raise MissingPluginError(f"{what}; it was not found at {transform}.")
    return str(transform)
