# Scope: tight `(indent, qname, ...)` rows for domain-member trees and calculation

Status: **scoping only — nothing here is implemented.** Queue item M10. Complete draft for the user's go-ahead; §5 lists the decisions needed before dispatch.

Goal: move mireport's baked-JSON encoding of domain-member trees (`explicitDimensionDomains`,
`ee20Domain`) and calculation (`calculation`) off today's per-arc dicts / flat edge list onto the
same compact indented-rows shape that presentation already uses (`PresentationRow`), with a true
calculation cycle treated as a hard error rather than accommodated.

## 1. Real-data cycle check

**Verdict: the premise holds. No summation-item cycle occurs in any real or test taxonomy on this
machine.** A calculation cycle is spec-legal (XBRL 2.1 permits one) but, as far as all available
evidence goes, purely theoretical, so per the maintainer's decision it becomes a hard error rather
than something the encoding accommodates.

How it was checked (throwaway scripts in the agent scratchpad, not committed):

- **DTS-level, via Arelle** (`arelle-release` from `transition_plans/.venv`, offline, package
  loaded from the zip). For each entry point, every ELR's summation-item relationship set was built
  exactly as mireport builds it: 2003 and 2023 arcroles combined into one set per ELR
  (`XbrlConst.summationItems`). A DFS three-colour cycle search ran over each ELR's edges. The
  script also *simulated the proposed tight walk* (§2.2): walk from `rootConcepts`, expanding each
  concept at most once per ELR. It then checked that the walk emitted every arc in the set. It
  would fall short only if part of the network were unreachable from a root, which in a finite
  graph means a cycle.
- **Baked JSON**: the same cycle search over the `calculation` section of all four
  `src/mireport/data/taxonomies/vsme-*.json`.
- **Supplementary raw-XML sweep**: every calculation linkbase on the machine (loose files and
  inside zips), one ELR per `calculationLink`, prohibited arcs dropped. This does not merge base
  sets across files, which is why the DTS-level check above is the authoritative one. 144
  linkbases, about 27.9k arcs (duplicates included), **0 cyclic ELRs**. It covered the four VSME
  source zips in `webapp_taxonomies/`, both IFRSAT zips, the L'Oréal ESEF filer extension in
  `oim/.../examples/loreal-2025-12-31-ReportPkg.zip` (the only non-standard-setter, real filer
  calc on disk), the `xbrl21_to_tavi` calculation fixtures and all generated forward output under
  `.claude/test-scratch/`.
- **Negative control**: the detector finds `A→B→C→A` and `A→A`, and finds nothing in a diamond DAG.

| DTS (entry point) | calc ELRs | arcs | arcrole | multi-root ELRs | concepts both total and item | items with >1 parent (same ELR) | of which are totals (shared subtrees) | cyclic ELRs | tight walk emitted every arc |
|---|---|---|---|---|---|---|---|---|---|
| VSME 2024-12-17 (baked) | 7 | 31 | 2023 | 4 | 2 | 4 | 0 | **0** | yes |
| VSME 2025-07-30 (baked) | 7 | 31 | 2023 | 4 | 2 | 4 | 0 | **0** | yes |
| VSME 2026-02-01 (baked) | 7 | 31 | 2023 | 4 | 2 | 4 | 0 | **0** | yes |
| VSME 2026-05-01 (baked) | 7 | 31 | 2023 | 4 | 2 | 4 | 0 | **0** | yes |
| IFRSAT 2024 Full IFRS + MC | 46 | 1312 | 2023 | 24 | 159 | 2 | 0 | **0** | yes |
| IFRSAT 2024 Combined | 67 | 1786 | 2023 | 33 | 198 | 2 | 0 | **0** | yes |
| IFRSAT 2024 IFRS for SMEs | 21 | 474 | 2023 | 9 | 39 | 0 | 0 | **0** | yes |
| IFRSAT 2025 Full IFRS | 46 | 1312 | 2023 | 24 | 159 | 2 | 0 | **0** | yes |
| IFRSAT 2025 Early IFRS 18 | 45 | 1289 | 2023 | 23 | 157 | 2 | 0 | **0** | yes |

(IFRSAT 2024 Deprecated and 2025 Management Commentary carry no calculation arcs. The identical
2024/2025 Full IFRS calc counts are real: the forward output of both editions under
`.claude/test-scratch/` agrees, 46/1312.)

What the numbers mean for the design:

- **Multiple roots are routine**: about half of all real calc ELRs have more than one. As already
  settled, this is not an obstacle: `extractPresentation` emits one `(0, root)` row per root.
- **A concept that is both total and item is routine** (159 in IFRS Full). Also not an obstacle:
  the subtotal appears at depth *n* under its parent, with its own items at *n+1*.
- **A shared calc subtree (a total reached from two parents in one ELR) never occurs in real
  data** (0 everywhere). A shared *leaf* item does (2–4 per DTS). The encoding still defines what
  happens for a shared total (§2.2), because it is legal and cheap to specify, but no real data
  exercises it.

### 1.1 Domain-member networks (for §2.1)

XDT already forbids directed cycles here. These numbers are about the two properties the tight
encoding must preserve:

| DTS | domain-member arcs | with `xbrldt:targetRole` | dimension-domain arcs with `targetRole` | members with >1 parent (same ELR) | `usable="false"` arcs |
|---|---|---|---|---|---|
| IFRSAT 2024 Full IFRS + MC | 3207 | 0 | 0 | 9 | 0 |
| IFRSAT 2024 Combined | 3631 | 0 | 0 | 9 | 0 |
| IFRSAT 2025 Full IFRS | 3339 | 0 | 0 | 13 | 0 |
| IFRSAT 2025 Early IFRS 18 | 3375 | 0 | 0 | 14 | 0 |

Baked VSME trees (`explicitDimensionDomains` + `ee20Domain`): 2808–3024 arcs per edition, no
multi-parent members, no unusable arcs. There is **exactly one real ELR crossing**, in VSME
2025-07-30 and later: `vsme:WasteGeneratedTable` / `vsme:TypeOfWasteAxis`. Its dimension-domain
arc sits in `role-119` and carries a `targetRole` to the `.../domain` ELR, so all 973 member arcs
of that tree record `elr = .../domain` while the tree's own `"elr"` is `role-119`. The crossing is
on the **dimension-domain arc, not partway down the member tree**. Nothing on the machine crosses
ELRs *within* a domain-member walk. That shapes §2.1: a tree-level field carries the real case,
and a per-row override covers the XDT-legal but unobserved mid-tree case.

So both preservation constraints are real in production data, not theoretical: the ELR change
(VSME) and DAG de-duplication (IFRSAT, 9–14 multi-parent members per DTS). `usable="false"` has no
real-data hits, which U1 had already noted; it is covered by the `xbrl21_to_tavi` hand-built
`usable` fixture.

## 2. Proposed encoding

Principle: **change the wire format only; keep the `Taxonomy`/`Concept` Python API identical**
(same dataclasses, same fields, same values) wherever that is achievable, and state exactly where
it is not (§2.2, relationship order in `CalculationGroup`). Rows are JSON arrays with
presentation's trailing-field elision: optional trailing fields are left out when they carry
their default, and a later field that must be present forces the earlier ones to be written.

### 2.1 Domain-member trees (`explicitDimensionDomains[dim][i]` and `other.ee20Domain`)

Today, one dict per arc:

```json
{"elr": E, "domain": H, "order": 1.0, "usable": true,
 "members": [{"elr": E2, "parent": H, "member": M, "order": 1.0, "usable": true}, ...]}
```

Proposed:

```json
{"elr": E, "domain": H, "order": 1.0, "usable": true,
 "targetRole": E2,
 "rows": [[1, M, 1.0], [2, M2, 1.0, false], [2, M3, 2.0, true, E3], ...]}
```

- `elr`, `domain`, `order` (dimension trees only) and `usable` are unchanged.
- `targetRole`: new tree-level field, **written only when set**. It is the dimension-domain arc's
  `xbrldt:targetRole`, i.e. the ELR of the head's own domain-member arcs when that differs from
  `elr`. Never present on an `ee20Domain`: there is no dimension-domain arc, and the head's arcs are
  in `enum2:linkrole` = `elr` by definition.
- `rows`: `[indent, member, order(, usable(, targetRole))]` in the extractor's existing walk order
  (depth-first, siblings in arc order). Indent 1 is a child of `domain`. The head itself gets no
  row because it is already `domain`, which matches `walkDefinitionChildren`'s indent-1 convention.
  - `order`: **always written.** Unlike presentation, the oim consumers use the value. `domains.
    DomainArcs`/`nest` merge the arcs of *several* trees into one network and interleave siblings
    by real arc order. `calculation.py` copies it onto the Tavi relationship. A position-derived
    substitute would change interleaving, not just bytes.
  - `usable`: written only when `false` (or when a `targetRole` follows it).
  - `targetRole`: written only when **this row's arc** carries an `xbrldt:targetRole` that moves
    its children into another ELR.

Extraction-side NamedTuple, replacing `DefinitionRelationship`:

```python
class DomainMemberRow(NamedTuple):
    indent: int
    qname: QName
    order: float
    usable: bool
    targetRole: str | None  # rel.consecutiveLinkrole if != relSet.linkrole, else None
```

`walkDefinitionRelationships` keeps its body and its `(elr, parent, member)` `_seen` key. It gains
an `indent` parameter (like `walkPresentationChildren`) and yields `DomainMemberRow`s instead.
`getDomainMemberArcs` serialises them with trailing elision.

**Constraint (1): the per-arc ELR is preserved exactly.** The encoding puts a `targetRole` on
the arc that *causes* an ELR change, rather than an ELR on every arc that *results* from one. The
loader rebuilds each relationship's `roleUri` with an indent stack of `(qname, elr-of-its-
children)`:

- The head's entry is `targetRole or elr`.
- A row's own arc is in its parent entry's ELR.
- The row pushes `(qname, row.targetRole or thatElr)`.

This is exactly the ELR `walkDefinitionRelationships` records today: `relSet.linkrole` of the set
reached through `consecutiveSet(rel)`. It is written once per crossing, not once per arc beneath
one, which is why this shape was chosen over the `elr_if_changed`-per-row alternative in the brief.
Real data is the reason. VSME's only crossing (§1.1) moves 973 arcs, 20 of them direct children of
the head, and would cost 20 row annotations under per-row-ELR but costs one tree-level field here.
A per-row `elr_if_changed` is also subtly lossy under DAG de-dup: if every child of a re-reached
member is suppressed, the crossing arc has no child row left to carry the ELR. Recording the
`targetRole` on the arc itself keeps it even then. (Today's per-arc `elr` has the same blind spot,
so this is strictly no worse.)

**Constraint (2): usable and DAG de-dup are preserved exactly.** Rows are emitted in exactly the
order `walkDefinitionRelationships` yields arcs today. Each yielded arc is followed immediately by
its target's recursion, so each row's parent is recoverable: it is the nearest preceding row one
indent shallower, or `domain` at indent 1. The rows are therefore a lossless re-spelling of
today's `members` list. A member reached through a second parent in the same ELR gets its own row
(one relationship per parent, as today), but no child rows under it, because the walk's `_seen`
key suppresses them exactly as it does now. **The loader must not re-expand it**, and does not
need to: it only rebuilds edges, and `getChildren(member)` already filters by parent concept
across the whole tree, so it returns the children listed at the first occurrence. Nothing is
duplicated, and nothing a consumer can observe is lost.

Verified, not argued: converting all 19 real trees in each VSME edition from today's JSON to rows
and back rebuilds `members` **identical and in order, 19/19 × 4 editions**, the `TypeOfWasteAxis`
crossing included (scratch script, §1 method). Tree JSON shrinks to **32%** of today's size, about
0.43–0.50 MB off each 2.4–3.2 MB baked VSME file. The IFRSAT multi-parent case (9–14 members per
DTS) and the per-row `targetRole` case have no real VSME instance. The existing stub tests in
`tests/unitTests/arelle/test_taxonomy_extraction.py` (`TestWalkDefinitionRelationships`, and the
enum2 fixture whose `Europe` arc carries `targetRole TARGET_ELR`) are where the implementing item
proves them.

Python API: `DimensionDomainTree`, `EnumerationDomainTree` and `DomainMemberRelationship` are
**unchanged**, with the same fields, values and relationship order, so equality and hashing are
unchanged too. oim's `dimensions._declared_trees` dedupes equal trees by hashing them. Only
`Taxonomy._domainTreeFromJSON` and `Concept._eeDomainTreeFromJSON` change, sharing one new
rows-to-relationships helper.

### 2.2 Calculation (`calculation[elr]`)

Today: `{"relationships": [{"source", "target", "weight", "order"}, ...]}`, from
`relationshipsBySource()`. Proposed:

```json
{"rows": [[0, Total], [1, Item, 1.0, 1.0], [1, SubTotal, -1.0, 2.0], [2, Item2, 1.0, 1.0], ...]}
```

- Root rows are `[0, qname]`, one per `relSet.rootConcepts()` in that order, exactly as
  `extractPresentation` does. Multiple roots and a concept that is both total and item need
  nothing special (settled; §1 shows both are routine).
- Item rows are `[indent, qname, weight, order]`, **both always written**. `weight` is
  load-bearing: Tavi admits only ±1, and `calculation._warn_unweighable_totals` must see any other
  value. `order`: same reason as §2.1. Eliding `weight == 1.0` would save little on a section that
  is already small (VSME: 7 ELRs, 31 arcs) and would make a root row and an item row differ only
  by indent, so it is not proposed.
- Shared subtrees use the §2.1 rule: a total's items are listed under its first occurrence in the
  ELR only. Real data never has a shared calc *total* (§1), so this is specified for completeness.
  It is not a real-data concern.

```python
class CalculationRow(NamedTuple):
    indent: int
    qname: QName
    weight: float | None  # None only on indent-0 root rows
    order: float | None
```

**Cycle detection: where, what, and what it aborts.**

1. *Where*: in a new `walkCalculationChildren(concept, relSet, indent, path, expanded)`, called
   from `extractCalculation` once per root. It is ordinary DFS. `path` is the current ancestor
   chain and `expanded` holds the concepts whose items have already been listed. For each arc from
   `concept`, the check `rel.target in path` (self-loops included) runs **before** the
   already-expanded skip. That order makes it complete: in DFS, a back edge to a node still on the
   stack is exactly a directed cycle, and skipping fully-explored nodes cannot hide one.
2. *The rootless case*: a strongly connected component that no root reaches, e.g. an ELR holding
   only `A→B→A`, has no root and would never be walked. So after the roots, `extractCalculation`
   compares the item rows emitted against `len(relSet)` arcs. If any are missing, it walks each
   not-yet-expanded source with the same function, purely to locate the back edge for the
   message. A finite acyclic graph is fully reachable from its roots, so a shortfall **is** a cycle.
   The §1 simulation confirmed the converse on every real ELR: no shortfall anywhere.
3. *What fires*: `raise ArelleModelInconsistency(ArelleDiagnostic.error("Summation-item
   relationships form a directed cycle", elr=elrUri, concepts=(A, B, ..., A)))`, with the cycle's
   concepts in path order. This is the existing hard-fail precedent (`_summationWeight`,
   `extractConceptsAndMetadata`'s enum2 check, `getDimensions`), and `extractConceptsAndMetadata`
   is the closest analogue. It is not a `diagnostics.emit(... warning ...)`.
4. *Scope: the whole DTS, not one ELR.* An exception from `extract()` propagates before
   `runTaxonomyInfo` reaches `writeDataFile`, so **no JSON is written for that taxonomy at all**.
   This is how every existing `ArelleModelInconsistency` already behaves, and
   `xbrl21_to_tavi.load` already turns it into a `TaxonomyLoadError`. The plan deliberately does
   **not** skip the ELR or mark it partial:
   - A "this ELR was cyclic" marker is a structural accommodation of the defect, which the
     maintainer rejected. It would also need a new JSON field that every consumer then handles.
   - Silently dropping the ELR is the "silence is not success" anti-pattern.
   - With zero real-data hits (§1), the cost of the stricter choice is nil.

   The error names the first cyclic ELR found. Reporting all of them at once is a cheap optional
   refinement (collect, then raise once with the rest as an extra `otherElrs=` field). It is not
   needed for correctness.
5. `arcsByArcrole` counting moves off the per-source loop onto a plain iteration over the ELR's
   relationships, so the mixed-arcrole warning is unchanged. In a mixed DTS, an arc declared under
   both arcroles in one ELR is still two relationships: the second becomes a second row with no
   child rows, the same as today's two edges.

**Loader-side guard.** Unlike an edge list, rows *can* literally spell a cycle: a row whose QName
equals an ancestor on the indent stack. `Taxonomy._calculationGroupFromJSON` should reject that
with a `TaxonomyException` naming the ELR and concept. This is cheap: check each row against the
stack it already keeps. It means hand-built JSON cannot reintroduce what the extractor forbids.
The same guard applies to domain trees, which XDT already forbids from cycling.

**Python API: one real, bounded change.** `CalculationGroup`, `CalculationRelationship`,
`getItems()` and `totals` keep their shapes and values. The **order of
`CalculationGroup.relationships` changes**: preorder from each root, as `DimensionDomainTree`'s
already is, instead of "grouped by source, sources in first-arc document order". Measured on both
IFRSAT editions, the orders agree in only 16/46 ELRs. Regrouping rows by source in the loader still
agrees in just 43/46, so no loader-side trick recovers today's order without writing extra
information. The order is recorded nowhere in the XBRL, it is an artefact of `fromModelObjects()`
dict order, so it is not worth preserving. What consumers depend on is unaffected:

- `getItems()` filters by source, so the arc order within a total is the same.
- The top-level totals are in the **same order in 46/46 IFRSAT ELRs**. Those totals are what
  `xbrl21_to_tavi.calculation._roots` numbers as `xbrl:rootSource` 1, 2, ...

The `CalculationGroup` docstring changes from "deliberately not a tree ... summation-item allows
cycles" to preorder plus "acyclic, enforced at extraction and load". Round-trip on real VSME
calc: 7/7 ELRs per edition lossless as a set of `(source, target, weight, order)`, 6/7 in the same
order. The calc JSON shrinks to 68%, trivial in absolute terms: the real benefit is one shape
everywhere, not bytes.

## 3. Consumer inventory

Headline: **no oim `src/` module reads mireport's JSON**. Every one of them goes through the Python
API, which §2 keeps identical for domain trees and changes only in relationship *order* for
calculation. The real work on the oim side is in **tests**: `tests/xbrl21_to_tavi/builders.py`
hand-writes mireport JSON in today's wire shape. (oim state read at U10's tip `2df5496`, read-only
via `git show`, because U11 is running in that worktree.)

### 3.1 mireport (this repo)

| Where | What changes | Invasiveness |
|---|---|---|
| `arelle/taxonomy_extraction.py` | `DefinitionRelationship` becomes `DomainMemberRow`. `walkDefinitionRelationships` gains `indent`, keeps its `_seen` key. `getDomainMemberArcs` serialises rows, and `getDomainTreesForExplicitDimension` adds `targetRole` when set (enum2 trees never do). New `CalculationRow` + `walkCalculationChildren` with cycle detection. `extractCalculation` drops `relationshipsBySource()` and writes `rows`. Docstrings for `extractCalculation` and `walkDefinitionRelationships` updated. | Moderate, contained: around 120 lines in one module |
| `arelle/model_access.py` | `relationshipsBySource()` loses its only caller. Delete it, or keep it for the unreached-arc diagnostic in §2.2 step 2 (its docstring justifies it by cycles, which are now an error). | Trivial |
| `taxonomy.py` | `_domainTreeFromJSON`, `Concept._eeDomainTreeFromJSON` and `_calculationGroupFromJSON` read `rows` through one shared stack-based helper, with the ancestor-cycle guard. `DimensionDomainTree` / `EnumerationDomainTree` / `DomainMemberRelationship` are unchanged. `CalculationGroup` is unchanged except that its docstring now says preorder and acyclic. | Small: 3 loaders plus one helper |
| Baked data | Re-bake all 4 `data/taxonomies/vsme-*.json` (§4). | Scripted, but real production data |
| Tests | `tests/unitTests/arelle/test_taxonomy_extraction.py`: `TestWalkDefinitionRelationships` (around 20 touchpoints), the `explicitDimensionDomains` / `ee20Domain` extraction assertions, and `expectedCalculation()`. Add new cases: a direct cycle, a self-loop, a rootless cycle, a mixed-arcrole duplicate arc, and a shared calc total. `test_taxonomy_domain_trees.py`, `test_taxonomy_enumeration_domain_trees.py` and `test_taxonomy_calculation.py` hand-build JSON, so switch them to rows and add the loader cycle-guard cases. `integrationTests/test_calculation_arcroles.py` reads `baked["calculation"]` directly. | Moderate: mostly mechanical fixture reshaping |
| Anything else | Checked, nothing else reads these keys: `taxonomy_checker.py`, `scripts/dump-taxonomy.py`, the report/coverage generators and the webapp. | None |

**Old-shape JSON: recommend no dual-read.** The loader should accept `rows` only. This matches
the project's legacy-purge precedent (U4/U8 on the oim side, and mireport's own "Drop the legacy
... keys" line of commits). Every baked file gets regenerated in the same item, and the only other
producer of old-shape JSON is oim's test builders, which §5 migrates in lockstep. The alternative
is a transitional dual-read that lets the oim tests stay green between the M and U items without
lockstep. It is listed as an open decision in §5, not assumed.

### 3.2 `oim_to_xbrl21` / `xbrl21_to_tavi`

| Consumer | Reads | Change needed | Invasiveness |
|---|---|---|---|
| `dimensions.py` (T2c dimension-domain reader) | `signature.domainTrees`, `tree.domainHead/.order/.usable/.roleUri`, `rel.parent/.member/.order/.usable/.roleUri` (per-arc `roleUri` to place notAll and cross-namespace networks), tree hashing in `_declared_trees` | **None.** All of these keep identical values, order and equality. | None to src |
| `domains.py` (U10's shared `DomainArcs`/`nest`) | Only what `dimensions.py`/`convert.py` pass it: `(parent, member, order, usable)` | **None.** `nest`'s `visited` guard (its "a cycle ends" comment) stays as harmless defence. | None |
| `convert.py` (U10's enum2 reader) | `getEEDomainTree()`, `tree.relationships`, `.roleUri`, `.domainHead`, `.usable` | **None.** | None to src |
| `calculation.py` (T2d/STd reader) | `taxonomy.calculation`, `cg.relationships` (in order), `getItems()`, `rel.order/.weight` | The Tavi output is semantically the same, but **arc order within a network follows the new preorder**, while root order is unchanged (46/46). `_roots`' "no top-level total (a directed cycle)" fallback becomes unreachable: an acyclic set, even after filtering out unweighable or non-reportable arcs, always has a root. Recommend removing it together with its warning, or turning it into an assertion. Update the module docstring: "mireport's flat edge list", and the "only possible with a directed cycle" paragraph. | Small src change, optional in part |
| `tests/xbrl21_to_tavi/builders.py` | Writes `explicitDimensionDomains`/`ee20Domain` `members` dicts and `calculation` `relationships` dicts | `domain_tree()`, `tree_enumeration()` and `calculation_role()` keep their **edge-list signatures**. They gain a small edges-to-rows serialiser: preorder from the head or roots, first-occurrence expansion, and an assertion that every given edge was emitted, so an unreachable or cyclic fixture fails loudly instead of being silently dropped. `tree_enumeration()`'s flat-value derivation reads the input edges, not `tree["members"]`. | Moderate, contained in one file. The 41 call sites across 5 test files mostly do not change. |
| `test_convert_dimensions.py` | `test_a_negative_cube_s_domain_in_several_elrs_...` rewrites `tree["members"]` ELRs by hand | Re-express it with the tree-level `targetRole`. | Small |
| `test_convert_calculation.py` | `_arcs()` asserts arcs in **document order** | Update expected lists where a fixture's arcs are not already in preorder. **Delete** `test_a_directed_cycle_is_rooted_at_its_first_total_warned`: a cycle can no longer be built through `build()`, because the loader guard rejects it. Replace it with a test that the loader raises. | Small to moderate |
| `test_roundtrip_calculation.py` | `_arcs()` is a `frozenset` | None (order-free). Its real 2.1 fixtures re-bake through Arelle, so they pick up rows automatically. | None |
| `test_convert_concepts.py`, `test_convert_modules.py`, `test_tavi.py` | Builder calls; `"relationships"` hits there are Tavi-side, not mireport JSON | None beyond what `builders.py` absorbs. | None |
| `test_convert_module.py`, `test_roundtrip_usable.py` (real baked VSME / Arelle-baked fixtures) | Python API only. Not golden files ("only that a real baked DTS converts"). | None expected. Re-run. | None |

**Honest overall estimate.** mireport: a real but contained change, about one focused item per
encoding. oim: **zero src change for domain trees**, a small optional src cleanup for calculation,
and a moderate, mechanical test-builder migration that is *forced*, not optional, because the
builders write the wire format.

## 4. Re-bake and regeneration consequences

- **Re-bake all four `src/mireport/data/taxonomies/vsme-*.json` again**, with the same
  `scripts/update-taxonomy.py` recipe as M7/M9, from `webapp_taxonomies/*.zip`. That is the
  fourth re-bake this session, after M7, M8 and M9. Acceptance, beyond "it ran":
  1. The diff touches only `explicitDimensionDomains`, `ee20Domain` and `calculation`.
  2. Load old and new JSON into `Taxonomy`. Every `DimensionDomainTree`/`EnumerationDomainTree`
     must compare **equal** (§2.1 predicts identical). Each `CalculationGroup`'s relationships
     must be **set-equal**, with top-level totals in the same order.
  3. Each file shrinks by about 0.43–0.50 MB.
- **Re-confirm both real IFRSAT editions** end to end with the STc recipe (reverse, schema,
  `validate.mjs`, forward, Arelle). Expectation, derived from the API analysis:
  - Dimension and enumeration networks in the Tavi output are **byte-identical**.
  - Summation-item networks are **set-identical, with identical roots, but arcs may be
    reordered** within a network. Arc order in a Tavi network carries no meaning; each arc's
    `order` property is unchanged. So a sha256 comparison of the whole file *will* differ, and
    the check must compare networks as sets.
  - Same `[WARN]` set and counts.
  - Arelle-clean forward output, with the same 46/1312 calc arcs.
- **`other_tavi_taxonomies/` is already pending regeneration.** U10's enum2 fix produced better
  IFRSAT output and is waiting on the user's copy-in decision (queue, Blocked section). This item
  **compounds that existing decision rather than creating a new one**. The natural moment for a
  single regeneration is after the U-items below land, so the deliverable is regenerated once,
  not twice. If the user copies U10's files in first, this item's calc arc reordering means a
  second, cosmetic-only regeneration later.
- M8's oim-side consumption is on hold pending a separate user decision. It is untouched by this
  plan, but whichever lands second rebases onto the other's re-bake.

## 5. Recommended sequencing

Nothing below is dispatched. It is for the user's go-ahead.

**Hard coupling.** The oim x2t worktree's `.venv` resolves `mireport` to *this* mireport x2t
worktree (`.../Digital-Template-to-XBRL-Converter/.claude/worktrees/x2t/src/mireport`), not the
main checkout. So the moment an M-item below changes mireport source here, oim's test builders go
red in the oim x2t worktree until the paired U-item lands. Therefore:

- Do not start an M-item while any oim item is running in the oim x2t worktree (U11 currently).
- Dispatch each U-item immediately after its M-item. This applies unless the user prefers a
  transitional dual-read (decision 1 below).

| Item | Repo / branch | Scope | Depends on |
|---|---|---|---|
| **M11** | mireport, stacked on this branch | Domain-member trees to rows (§2.1): extractor, loaders, shared rows helper and loader guard, tests, **re-bake the 4 VSME JSONs**, and the §4 Taxonomy-equality acceptance check. Python API unchanged. | M10 go-ahead; U11 finished |
| **U12** | oim | `builders.py` domain/enum serialiser with the all-edges-emitted assertion, plus the `test_convert_dimensions` targetRole test. **No src change** (verify by running the oim suite unchanged apart from tests). Re-run both IFRSAT editions: dimension and enum networks byte-identical. | M11 |
| **M12** | mireport, stacked on M11 | Calculation to rows (§2.2), cycle detection as a hard `ArelleModelInconsistency` aborting the whole bake, loader cycle guard, `CalculationGroup` docstring (preorder, acyclic), `relationshipsBySource` disposition, new cycle tests, **re-bake the 4 VSME JSONs**, and the §4 set-equality check. | M11 (or in parallel with U12) |
| **U13** | oim | `builders.calculation_role` serialiser; `test_convert_calculation` order expectations; delete the cycle test and add a loader-raises test; remove or assert `_roots`' cycle fallback; `calculation.py` docstring. Re-run both IFRSAT editions: calc networks set-identical, roots identical. | M12, U12 |
| then | — | One `other_tavi_taxonomies/` regeneration covering U10 and U12/U13, per the user's pending decision; then RF2-style review and a merge gate like MG3. | U13 |

M11 and M12 could be one item with two commits, halving the re-bakes. They are split here to match
the M8/M9 granularity and because M11 is a zero-API-change refactor while M12 is not. Each is
reviewable on its own terms.

**Decisions for the user before dispatch:**

1. **Old-shape JSON in the loader: none** (recommended, §3.1), which forces M/U lockstep. The
   alternative is a transitional dual-read removed in U13, which decouples the timing.
2. **One cyclic ELR aborts the whole bake** (recommended, §2.2 step 4). This is the existing
   `ArelleModelInconsistency` behaviour. No per-ELR partial marker.
3. **Remove `calculation._roots`' cycle fallback in U13** (recommended; unreachable once the loader
   rejects cycles), or keep it as defence in depth.
4. **Two M-items or one** (above).
