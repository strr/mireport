"""Tests for report package zip assembly.

The layout assertions here are not cosmetic. Arelle decides what to load out of
a report package purely from the zip's directory structure:

- every file directly under ``{top}/reports/`` with a report extension becomes
  its own entry point (``arelle.packages.report.ReportPackage.getAllReportEntries``);
- files in a *sub*directory of ``reports/`` collapse into a single multi-file
  entry — an inline document set;
- but subdirectory entries are discarded outright whenever a top-level report
  also exists.

So "the report moves into the docset directory once there are members" is a
correctness requirement, not a preference.
"""

import zipfile
from datetime import UTC, datetime
from io import BytesIO

import pytest

from mireport.filesupport import DEFAULT_EPOCH, FilelikeAndFileName
from mireport.report.reportpackage import (
    INLINE_REPORT_PACKAGE_JSON,
    UNCONSTRAINED_REPORT_PACKAGE_JSON,
    ReportPackageException,
    buildReportPackage,
)

TOP = "Acme_2024"


def makeFile(filename: str, content: bytes = b"<html/>") -> FilelikeAndFileName:
    return FilelikeAndFileName(fileContent=content, filename=filename)


REPORT = makeFile("Acme_2024_XBRL_Report.html", b"<html>tagged</html>")


def namelist(package: FilelikeAndFileName) -> list[str]:
    with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
        return zf.namelist()


def readEntry(package: FilelikeAndFileName, path: str) -> bytes:
    with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
        return zf.read(path)


class TestPlainPackage:
    def test_layout(self) -> None:
        package = buildReportPackage(REPORT, topLevel=TOP)
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{TOP}/reports/Acme_2024_XBRL_Report.html",
        ]

    def test_package_json_is_unconstrained(self) -> None:
        package = buildReportPackage(REPORT, topLevel=TOP)
        assert (
            readEntry(package, f"{TOP}/META-INF/reportPackage.json")
            == UNCONSTRAINED_REPORT_PACKAGE_JSON
        )

    def test_report_content_survives(self) -> None:
        package = buildReportPackage(REPORT, topLevel=TOP)
        assert (
            readEntry(package, f"{TOP}/reports/Acme_2024_XBRL_Report.html")
            == REPORT.fileContent
        )

    def test_package_filename(self) -> None:
        package = buildReportPackage(REPORT, topLevel=TOP)
        assert package.filename == f"{TOP}_XBRL_Report.zip"

    def test_empty_sequences_are_the_same_as_omitting_them(self) -> None:
        assert namelist(
            buildReportPackage(REPORT, topLevel=TOP, docsetMembers=[], attachments=[])
        ) == namelist(buildReportPackage(REPORT, topLevel=TOP))

    def test_is_a_readable_deflated_zip(self) -> None:
        package = buildReportPackage(REPORT, topLevel=TOP)
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            assert zf.testzip() is None
            assert all(
                info.compress_type == zipfile.ZIP_DEFLATED for info in zf.infolist()
            )


class TestDocumentSet:
    def test_report_moves_into_the_docset_directory(self) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, docsetMembers=[makeFile("annex1.html")]
        )
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{TOP}/reports/Acme_2024_XBRL_Report/Acme_2024_XBRL_Report.html",
            f"{TOP}/reports/Acme_2024_XBRL_Report/annex1.html",
        ]

    def test_nothing_is_left_directly_in_reports(self) -> None:
        """A top-level report would make Arelle discard the whole docset."""
        package = buildReportPackage(
            REPORT, topLevel=TOP, docsetMembers=[makeFile("annex1.html")]
        )
        directlyInReports = [
            name
            for name in namelist(package)
            if name.startswith(f"{TOP}/reports/")
            and "/" not in name[len(f"{TOP}/reports/") :]
        ]
        assert directlyInReports == []

    def test_report_is_written_first(self) -> None:
        """Docset order is zip write order, and the tagged report must lead."""
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("annex1.html"), makeFile("annex2.html")],
        )
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            inDocset = [
                info.filename
                for info in zf.infolist()
                if info.filename.startswith(f"{TOP}/reports/Acme_2024_XBRL_Report/")
            ]
        assert inDocset[0].endswith("/Acme_2024_XBRL_Report.html")

    def test_member_order_is_preserved(self) -> None:
        members = [makeFile(f"annex{i}.html") for i in (3, 1, 2)]
        package = buildReportPackage(REPORT, topLevel=TOP, docsetMembers=members)
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            names = [info.filename.rsplit("/", 1)[-1] for info in zf.infolist()]
        assert names[-3:] == ["annex3.html", "annex1.html", "annex2.html"]

    def test_member_content_survives(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("annex1.html", b"<html>from pdf</html>")],
        )
        assert (
            readEntry(package, f"{TOP}/reports/Acme_2024_XBRL_Report/annex1.html")
            == b"<html>from pdf</html>"
        )

    def test_docset_directory_is_named_for_the_report_stem(self) -> None:
        report = makeFile("Report.With.Dots.html")
        package = buildReportPackage(
            report, topLevel=TOP, docsetMembers=[makeFile("annex1.html")]
        )
        assert f"{TOP}/reports/Report.With.Dots/Report.With.Dots.html" in namelist(
            package
        )


class TestAttachments:
    def test_attachments_live_outside_reports(self) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, attachments=[makeFile("annex1.pdf", b"%PDF-1.7")]
        )
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{TOP}/reports/Acme_2024_XBRL_Report.html",
            f"{TOP}/attachments/annex1.pdf",
        ]

    def test_attachments_alone_do_not_move_the_report(self) -> None:
        """Only docset members trigger the subdirectory layout."""
        package = buildReportPackage(
            REPORT, topLevel=TOP, attachments=[makeFile("annex1.pdf", b"%PDF-1.7")]
        )
        assert f"{TOP}/reports/Acme_2024_XBRL_Report.html" in namelist(package)

    def test_attachment_content_survives(self) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, attachments=[makeFile("annex1.pdf", b"%PDF-1.7 body")]
        )
        assert readEntry(package, f"{TOP}/attachments/annex1.pdf") == b"%PDF-1.7 body"

    def test_docset_and_attachments_together(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("annex1.html")],
            attachments=[makeFile("annex1.pdf", b"%PDF-1.7")],
        )
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{TOP}/reports/Acme_2024_XBRL_Report/Acme_2024_XBRL_Report.html",
            f"{TOP}/reports/Acme_2024_XBRL_Report/annex1.html",
            f"{TOP}/attachments/annex1.pdf",
        ]


class TestCollisions:
    def test_duplicate_members_are_suffixed(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[
                makeFile("annex.html", b"one"),
                makeFile("annex.html", b"two"),
            ],
        )
        docset = f"{TOP}/reports/Acme_2024_XBRL_Report"
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{docset}/Acme_2024_XBRL_Report.html",
            f"{docset}/annex.html",
            f"{docset}/annex_1.html",
        ]
        assert readEntry(package, f"{docset}/annex.html") == b"one"
        assert readEntry(package, f"{docset}/annex_1.html") == b"two"

    def test_member_colliding_with_the_report_is_suffixed(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("Acme_2024_XBRL_Report.html", b"from pdf")],
        )
        docset = f"{TOP}/reports/Acme_2024_XBRL_Report"
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{docset}/Acme_2024_XBRL_Report.html",
            f"{docset}/Acme_2024_XBRL_Report_1.html",
        ]
        assert (
            readEntry(package, f"{docset}/Acme_2024_XBRL_Report.html")
            == REPORT.fileContent
        )

    def test_duplicate_attachments_are_suffixed(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            attachments=[makeFile("a.pdf", b"one"), makeFile("a.pdf", b"two")],
        )
        assert f"{TOP}/attachments/a.pdf" in namelist(package)
        assert f"{TOP}/attachments/a_1.pdf" in namelist(package)

    def test_collisions_are_counted_per_directory(self) -> None:
        """An attachment never collides with a docset member — different dirs."""
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("annex.html")],
            attachments=[makeFile("annex.html")],
        )
        assert f"{TOP}/reports/Acme_2024_XBRL_Report/annex.html" in namelist(package)
        assert f"{TOP}/attachments/annex.html" in namelist(package)

    def test_three_way_collision(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("a.html", bytes([i])) for i in range(3)],
        )
        docset = f"{TOP}/reports/Acme_2024_XBRL_Report"
        assert namelist(package)[1:] == [
            f"{docset}/Acme_2024_XBRL_Report.html",
            f"{docset}/a.html",
            f"{docset}/a_1.html",
            f"{docset}/a_2.html",
        ]

    def test_a_collision_never_raises(self) -> None:
        """PDFs are additive; a name clash must not sink a valid conversion."""
        buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("a.html")] * 5,
            attachments=[makeFile("a.pdf")] * 5,
        )


class TestZipSafety:
    @pytest.mark.parametrize(
        "filename",
        [
            "../escape.html",
            "sub/dir.html",
            "back\\slash.html",
            "  spaces  .html",
            "weiřd–dash.html",
        ],
        ids=["dotdot", "slash", "backslash", "spaces", "non-ascii"],
    )
    def test_hostile_member_names_are_sanitised(self, filename: str) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, docsetMembers=[makeFile(filename)]
        )
        docset = f"{TOP}/reports/Acme_2024_XBRL_Report/"
        members = [n for n in namelist(package) if n.startswith(docset)]
        assert len(members) == 2
        for name in members:
            tail = name[len(docset) :]
            assert "/" not in tail
            assert "\\" not in tail
            assert ".." not in tail.split(".")

    @pytest.mark.parametrize(
        "filename",
        ["../escape.pdf", "sub/dir.pdf", "back\\slash.pdf"],
        ids=["dotdot", "slash", "backslash"],
    )
    def test_hostile_attachment_names_are_sanitised(self, filename: str) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, attachments=[makeFile(filename)]
        )
        prefix = f"{TOP}/attachments/"
        attachments = [n for n in namelist(package) if n.startswith(prefix)]
        assert len(attachments) == 1
        tail = attachments[0][len(prefix) :]
        assert "/" not in tail and "\\" not in tail and ".." not in tail.split(".")

    def test_no_entry_uses_a_backslash_separator(self) -> None:
        """Arelle rejects the whole package on a single backslash separator."""
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("a\\b.html")],
            attachments=[makeFile("c\\d.pdf")],
        )
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            assert all("\\" not in info.orig_filename for info in zf.infolist())

    def test_exactly_one_top_level_directory(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("annex1.html")],
            attachments=[makeFile("annex1.pdf")],
        )
        assert {name.split("/")[0] for name in namelist(package)} == {TOP}

    def test_no_duplicate_entries(self) -> None:
        package = buildReportPackage(
            REPORT,
            topLevel=TOP,
            docsetMembers=[makeFile("a.html")] * 3,
            attachments=[makeFile("a.pdf")] * 3,
        )
        names = namelist(package)
        assert len(names) == len(set(names))


class TestInlinePackage:
    """An .xbri: the Inline XBRL report package type, with the files a report links to."""

    RESOURCES = (("images/page-1.webp", b"RIFF"), ("fonts/a.woff", b"wOFF"))

    def test_marker_and_name(self) -> None:
        package = buildReportPackage(REPORT, topLevel=TOP, inline=True)
        assert package.filename == f"{TOP}.xbri"
        assert (
            readEntry(package, f"{TOP}/META-INF/reportPackage.json")
            == INLINE_REPORT_PACKAGE_JSON
        )

    def test_resources_sit_beside_the_report_in_its_own_directory(self) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, inline=True, resources=self.RESOURCES
        )
        here = f"{TOP}/reports/Acme_2024_XBRL_Report"
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{here}/Acme_2024_XBRL_Report.html",
            f"{here}/fonts/a.woff",
            f"{here}/images/page-1.webp",
        ]
        assert readEntry(package, f"{here}/images/page-1.webp") == b"RIFF"

    def test_only_inline_xbrl(self) -> None:
        with pytest.raises(ReportPackageException):
            buildReportPackage(
                makeFile("report.json", b"{}"), topLevel=TOP, inline=True
            )

    @pytest.mark.parametrize(
        "path", ["../x.png", "/x.png", "images/../../x.png", "page.html", "data.json"]
    )
    def test_a_resource_stays_beside_the_report_and_is_not_a_report(
        self, path: str
    ) -> None:
        with pytest.raises(ReportPackageException):
            buildReportPackage(REPORT, topLevel=TOP, resources=[(path, b"x")])

    def test_resources_with_one_path(self) -> None:
        with pytest.raises(ReportPackageException):
            buildReportPackage(
                REPORT, topLevel=TOP, resources=[("a.png", b"1"), ("a.png", b"2")]
            )


class TestTimestamp:
    """One time on every entry, so a package can be rebuilt byte for byte."""

    def times(self, package: FilelikeAndFileName) -> set[tuple[int, ...]]:
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            return {info.date_time for info in zf.infolist()}

    def test_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
        package = buildReportPackage(
            REPORT, topLevel=TOP, timestamp=datetime(2023, 10, 9, tzinfo=UTC)
        )
        assert self.times(package) == {(2023, 10, 9, 0, 0, 0)}

    def test_source_date_epoch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
        assert self.times(buildReportPackage(REPORT, topLevel=TOP)) == {
            (2026, 1, 1, 0, 0, 0)
        }

    def test_mireport_epoch_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
        assert self.times(buildReportPackage(REPORT, topLevel=TOP)) == {
            (2003, 12, 31, 0, 0, 0)
        }
        assert DEFAULT_EPOCH == datetime(2003, 12, 31, tzinfo=UTC)

    def test_two_builds_are_the_same_bytes(self) -> None:
        def build() -> bytes:
            return buildReportPackage(
                REPORT, topLevel=TOP, inline=True, resources=TestInlinePackage.RESOURCES
            ).fileContent

        assert build() == build()


def taxonomyPackage(name: str, start: str, files: dict[str, bytes]) -> bytes:
    """A small taxonomy package zip: one rewrite, one entry point, @files."""
    content = BytesIO()
    with zipfile.ZipFile(content, "w") as zf:
        zf.writestr(
            f"{name}/META-INF/catalog.xml",
            '<catalog xmlns="urn:oasis:names:tc:entity:xmlns:xml:catalog">'
            f'<rewriteURI uriStartString="https://example.com/{start}/" rewritePrefix="../example.com/{start}/"/>'
            "</catalog>",
        )
        zf.writestr(
            f"{name}/META-INF/taxonomyPackage.xml",
            '<tp:taxonomyPackage xmlns:tp="http://xbrl.org/2016/taxonomy-package">'
            f"<tp:identifier>https://example.com/{start}</tp:identifier>"
            f'<tp:entryPoints><tp:entryPoint><tp:entryPointDocument href="https://example.com/{start}/e.xsd"/>'
            "</tp:entryPoint></tp:entryPoints></tp:taxonomyPackage>",
        )
        for path, data in files.items():
            zf.writestr(f"{name}/{path}", data)
    return content.getvalue()


class TestExtensionTaxonomy:
    """A reporter's own extension travels in its report package, which is then a taxonomy package too."""

    EXT = taxonomyPackage("acme-ext", "acme", {"example.com/acme/e.xsd": b"<schema/>"})

    def test_layout(self) -> None:
        package = buildReportPackage(
            REPORT, topLevel=TOP, inline=True, taxonomyPackages=[self.EXT]
        )
        assert namelist(package) == [
            f"{TOP}/META-INF/reportPackage.json",
            f"{TOP}/META-INF/catalog.xml",
            f"{TOP}/META-INF/taxonomyPackage.xml",
            f"{TOP}/example.com/acme/e.xsd",
            f"{TOP}/reports/Acme_2024_XBRL_Report.html",
        ]
        assert b'uriStartString="https://example.com/acme/"' in readEntry(
            package, f"{TOP}/META-INF/catalog.xml"
        )

    def test_two_packages_merge(self) -> None:
        other = taxonomyPackage(
            "beta", "beta", {"example.com/beta/b.xsd": b"<schema/>"}
        )
        package = buildReportPackage(
            REPORT, topLevel=TOP, taxonomyPackages=[self.EXT, other]
        )
        catalog = readEntry(package, f"{TOP}/META-INF/catalog.xml")
        assert (
            b"https://example.com/acme/" in catalog
            and b"https://example.com/beta/" in catalog
        )
        metadata = readEntry(package, f"{TOP}/META-INF/taxonomyPackage.xml")
        assert metadata.count(b"entryPointDocument") == 2

    def test_a_clash_is_refused(self) -> None:
        same = taxonomyPackage(
            "again", "other", {"example.com/acme/e.xsd": b"<different/>"}
        )
        with pytest.raises(ReportPackageException):
            buildReportPackage(REPORT, topLevel=TOP, taxonomyPackages=[self.EXT, same])

    def test_a_package_without_metadata_is_refused(self) -> None:
        bare = BytesIO()
        with zipfile.ZipFile(bare, "w") as zf:
            zf.writestr("x/example.com/x.xsd", b"<schema/>")
        with pytest.raises(ReportPackageException):
            buildReportPackage(REPORT, topLevel=TOP, taxonomyPackages=[bare.getvalue()])

    def test_two_builds_are_the_same_bytes(self) -> None:
        def build() -> bytes:
            return buildReportPackage(
                REPORT, topLevel=TOP, inline=True, taxonomyPackages=[self.EXT]
            ).fileContent

        assert build() == build()
