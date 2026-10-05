"""Unit tests for the optional top-level "roles" section (see
mireport.arelle.taxonomy_extraction.extractRoles(), which writes it),
exposed through Taxonomy.roles and Taxonomy.getRole()."""

from __future__ import annotations

from typing import Any

from mireport.taxonomy import RoleInfo, Taxonomy

_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"
_ROLE = "https://example.com/role/Anchoring"
_OTHER_ROLE = "https://example.com/role/AAnchoring"
_BARE_ROLE = "https://example.com/role/Bare"


def _bits(roles: dict[str, Any] | None) -> dict[str, Any]:
    bits: dict[str, Any] = {
        "entryPoint": "test://roles",
        "namespaces": {"ext": "https://example.com/ext"},
        "concepts": {
            "ext:A": {
                "labels": {"en": {_STANDARD_LABEL: "Label"}},
                "dataType": "xbrli:monetaryItemType",
                "baseDataType": "xbrli:monetaryItemType",
                "periodType": "duration",
                "numeric": True,
            }
        },
        "presentation": {},
        "dimensions": {},
    }
    if roles is not None:
        bits["roles"] = roles
    return bits


_ROLES = {
    _ROLE: {"definition": " Anchoring ", "labels": {"en": "Anchor", "fr": "Ancrage"}},
    _OTHER_ROLE: {"definition": "Other"},
    _BARE_ROLE: {},
}


class TestRoles:
    def test_roles_sorted_by_uri_and_loaded(self) -> None:
        taxonomy = Taxonomy.fromJSON(_bits(_ROLES))
        assert [r.roleUri for r in taxonomy.roles] == sorted(_ROLES)
        role = taxonomy.getRole(_ROLE)
        assert role is not None
        assert role.definition == "Anchoring"
        assert dict(role.labels) == {"en": "Anchor", "fr": "Ancrage"}
        bare = taxonomy.getRole(_BARE_ROLE)
        assert bare == RoleInfo(_BARE_ROLE, None, {})

    def test_absent_key_gives_empty(self) -> None:
        taxonomy = Taxonomy.fromJSON(_bits(None))
        assert taxonomy.roles == ()
        assert taxonomy.getRole(_ROLE) is None

    def test_unknown_role_is_none(self) -> None:
        assert Taxonomy.fromJSON(_bits(_ROLES)).getRole("https://x/y") is None

    def test_get_label_fallbacks(self) -> None:
        taxonomy = Taxonomy.fromJSON(_bits(_ROLES))
        role = taxonomy.getRole(_ROLE)
        assert role is not None
        assert role.getLabel("fr") == "Ancrage"
        # unknown language falls back to the taxonomy default ("en")
        assert role.getLabel("de") == "Anchor"
        assert role.getLabel() == "Anchor"
        # no default-language fallback: use the definition
        assert role.getLabel("de", fallbackToDefaultLanguage=False) == "Anchoring"
        assert (
            role.getLabel(
                "de", fallbackToDefaultLanguage=False, fallbackToDefinition=False
            )
            is None
        )
        # no labels, no definition
        bare = taxonomy.getRole(_BARE_ROLE)
        assert bare is not None
        assert bare.getLabel("en") is None
        other = taxonomy.getRole(_OTHER_ROLE)
        assert other is not None
        assert other.getLabel("en") == "Other"

    def test_json_is_not_modified(self) -> None:
        bits = _bits(_ROLES)
        assert Taxonomy.fromJSON(bits).roles
        assert bits["roles"] == _ROLES
