"""Assembly of XBRL report packages (the ``.zip`` an Inline XBRL report ships in).

Kept separate from :mod:`mireport.report.inlinereport` so that attachments are an
argument rather than mutable state on ``InlineReport``: everything the zip needs
arrives at the one call that writes it.

The directory layout is load-bearing. Arelle decides what to load out of a report
package from the structure alone (``arelle.packages.report.ReportPackage``):

- a file directly under ``{top}/reports/`` is a report entry point in its own
  right, so a second one there would be validated as a second XBRL report;
- files sharing a *sub*directory of ``reports/`` collapse into a single
  multi-file entry — an Inline XBRL document set;
- subdirectory entries are discarded entirely whenever a top-level report also
  exists.

Hence: as soon as there are document set members, the tagged report moves down
into the document set directory with them, and nothing is left directly in
``reports/``. Content that is not part of the document set (the original PDFs)
goes to ``attachments/``, outside ``reports/`` altogether, where no discovery
path looks at it.

Resources (a report's images, fonts, stylesheets: files it links to by a relative
path) go beside the report, so those links resolve in the package as they did
outside it. They too move the report into a subdirectory of ``reports/``, so
they never sit directly under it. Discovery only takes files with a report
extension, at most one directory down, so a resource is never mistaken for a
report; one with a report extension is refused rather than risk it.

Every entry carries one timestamp (the report's creation time, else
SOURCE_DATE_EPOCH, else mireport's epoch), and entries are written in a fixed
order, so building the same package twice gives the same bytes.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from mireport.filesupport import FilelikeAndFileName, sourceDateEpoch, zipSafeString

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

UNCONSTRAINED_REPORT_PACKAGE_JSON = b"""{
    "documentInfo": {
        "documentType": "https://xbrl.org/report-package/2023"
    }
}"""

INLINE_REPORT_PACKAGE_JSON = b"""{
    "documentInfo": {
        "documentType": "https://xbrl.org/report-package/2023/xbri"
    }
}"""

METADATA_DIRECTORY = "META-INF"
REPORTS_DIRECTORY = "reports"
ATTACHMENTS_DIRECTORY = "attachments"

# What Arelle loads as a report from a report package (and so what a resource may not be)
REPORT_EXTENSIONS = frozenset(
    {".html", ".htm", ".xhtml", ".json", ".xbrl", ".xml", ".csv"}
)
INLINE_EXTENSIONS = frozenset({".html", ".htm", ".xhtml"})


class ReportPackageException(ValueError):
    """A report package that cannot be built as asked."""


def _resourcePath(path: str) -> str:
    """@path as a relative POSIX path that stays inside the report's directory."""
    p = PurePosixPath(path.replace("\\", "/"))
    if (
        p.is_absolute()
        or not p.parts
        or any(part in ("", ".", "..") for part in p.parts)
    ):
        raise ReportPackageException(
            f"resource path {path!r} must be relative and stay beside the report"
        )
    if p.suffix.lower() in REPORT_EXTENSIONS:
        raise ReportPackageException(
            f"resource {path!r} has a report extension, so Arelle would load it as a report"
        )
    return p.as_posix()


def _safeFilename(filename: str, *, fallback: str) -> str:
    """Reduce an arbitrary filename to a single zip-safe path segment.

    Directory parts are discarded (on either separator — Arelle rejects a
    package outright if any entry uses a backslash) before the remainder is run
    through the same sanitiser used for entity names.
    """
    lastSegment = filename.replace("\\", "/").rpartition("/")[2]
    return zipSafeString(lastSegment, fallback=fallback)


def _uniqueFilename(filename: str, taken: set[str]) -> str:
    """Return @filename, or the first ``{stem}_{n}{suffix}`` variant not in @taken.

    Colliding names are suffixed rather than rejected: attachments are additive
    and optional, so a name clash must never sink an otherwise-valid conversion.
    @taken is updated with whatever is returned.
    """
    candidate = filename
    if candidate in taken:
        path = PurePosixPath(filename)
        stem, suffix = path.stem, path.suffix
        n = 1
        while (candidate := f"{stem}_{n}{suffix}") in taken:
            n += 1
    taken.add(candidate)
    return candidate


def buildReportPackage(
    report: FilelikeAndFileName,
    *,
    topLevel: str,
    docsetMembers: Sequence[FilelikeAndFileName] = (),
    attachments: Sequence[FilelikeAndFileName] = (),
    resources: Sequence[tuple[str, bytes]] = (),
    inline: bool = False,
    timestamp: datetime | None = None,
) -> FilelikeAndFileName:
    """Build a report package containing @report: unconstrained (``.zip``), or with
    @inline an Inline XBRL report package (``.xbri``).

    :param topLevel: name of the package's single top-level directory.
    :param docsetMembers: further Inline XBRL documents to be loaded as one
        document set with @report. When non-empty the report moves into a
        subdirectory of ``reports/`` alongside them. Written in the order given,
        after the report, because a document set's order is its zip order.
    :param attachments: content to travel with the report without being part of
        it (the source PDFs). Written to ``attachments/``.
    :param resources: (relative path, content) pairs the report links to, such as
        ``images/page-1.webp``: written beside the report, so the links resolve.
    :param inline: an Inline XBRL report package: the ``xbri`` document type and
        extension, and only Inline XBRL documents in it.
    :param timestamp: every entry's time; else SOURCE_DATE_EPOCH, else mireport's
        epoch (:data:`mireport.filesupport.DEFAULT_EPOCH`).
    """
    top = zipSafeString(topLevel, fallback="report")
    stamp = (timestamp or sourceDateEpoch()).timetuple()[:6]
    if inline:
        for document in (report, *docsetMembers):
            if PurePosixPath(document.filename).suffix.lower() not in INLINE_EXTENSIONS:
                raise ReportPackageException(
                    f"an .xbri holds Inline XBRL only, not {document.filename!r}"
                )
    paths = [_resourcePath(path) for path, _ in resources]
    if len(set(paths)) != len(paths):
        raise ReportPackageException("two resources have the same path")
    content = BytesIO()
    with zipfile.ZipFile(
        content, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as zf:

        def write(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(name, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data, compresslevel=6)

        write(
            f"{top}/{METADATA_DIRECTORY}/reportPackage.json",
            INLINE_REPORT_PACKAGE_JSON if inline else UNCONSTRAINED_REPORT_PACKAGE_JSON,
        )

        reportFilename = _safeFilename(report.filename, fallback="report.html")
        if docsetMembers or resources:
            reportDir = (
                f"{top}/{REPORTS_DIRECTORY}/{PurePosixPath(reportFilename).stem}"
            )
        else:
            reportDir = f"{top}/{REPORTS_DIRECTORY}"

        # Names are tracked per directory: a member and an attachment sharing a
        # name are in different directories and so do not collide.
        usedInReportDir: set[str] = set()
        for file in (report, *docsetMembers):
            name = _uniqueFilename(
                _safeFilename(file.filename, fallback="report.html"), usedInReportDir
            )
            write(f"{reportDir}/{name}", file.fileContent)
        for path, data in sorted(zip(paths, (data for _, data in resources))):
            if path in usedInReportDir:
                raise ReportPackageException(
                    f"resource {path!r} has the name of a report document"
                )
            write(f"{reportDir}/{path}", data)

        usedInAttachmentDir: set[str] = set()
        for attachment in attachments:
            name = _uniqueFilename(
                _safeFilename(attachment.filename, fallback="attachment"),
                usedInAttachmentDir,
            )
            write(f"{top}/{ATTACHMENTS_DIRECTORY}/{name}", attachment.fileContent)

    return FilelikeAndFileName(
        fileContent=content.getvalue(),
        filename=f"{top}.xbri" if inline else f"{top}_XBRL_Report.zip",
    )
