# Scope: tight `(indent, qname, ...)` rows for domain-member trees and calculation

Status: **scoping only — nothing here is implemented.** Queue item M10.

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

_Still investigating._

## 4. Re-bake and regeneration consequences

_Still investigating._

## 5. Recommended sequencing

_Still investigating._
