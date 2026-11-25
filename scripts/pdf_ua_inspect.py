#!/usr/bin/env python3
"""Deterministic PDF/UA-1, WCAG 2.1 AA, and Section 508 pre-flight inspector.

Inspects PDF catalog metadata, /StructTreeRoot tag trees, per-page MCID uniqueness
(root cause of PAC 2026 freezes), heading hierarchy, <Figure> /Alt text, <Table>
<TH> header cells, and AcroForm /Widget visual tab order using pure-Python pypdf.

Exit codes:
  0 - Inspection completed cleanly
  2 - Usage error, missing file, or missing pypdf dependency
  3 - Encrypted / password-protected PDF (aborted cleanly)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject, NumberObject
    _HAS_PYPDF = True
except ImportError:
    _HAS_PYPDF = False


def resolve(obj: Any) -> Any:
    """Safely dereference a pypdf IndirectObject."""
    if isinstance(obj, IndirectObject):
        try:
            return obj.get_object()
        except Exception:
            return None
    return obj


def obj_id_str(obj: Any) -> str:
    """Return a stable PDF object identifier string such as '12 0 R'."""
    if isinstance(obj, IndirectObject):
        gen = getattr(obj, "generation", 0)
        return f"{obj.idnum} {gen} R"
    ref = getattr(obj, "indirect_reference", None)
    if ref is not None:
        gen = getattr(ref, "generation", 0)
        return f"{ref.idnum} {gen} R"
    return "inline"


def clean_str(val: Any) -> str:
    """Convert a pypdf string/name object to a stripped Python str safely."""
    val = resolve(val)
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace").strip()
    return str(val).strip()


def extract_xmp_dc_title(reader: PdfReader) -> str:
    """Extract dc:title from XMP metadata if present."""
    try:
        xmp = reader.xmp_metadata
        if xmp is not None and getattr(xmp, "dc_title", None):
            dc_title = xmp.dc_title
            if isinstance(dc_title, dict):
                for v in dc_title.values():
                    if str(v).strip():
                        return str(v).strip()
            elif str(dc_title).strip():
                return str(dc_title).strip()
    except Exception:
        pass

    # Fallback: parse raw /Catalog/Metadata XML stream
    try:
        catalog = resolve(reader.trailer.get("/Root"))
        if isinstance(catalog, DictionaryObject) and "/Metadata" in catalog:
            meta_stream = resolve(catalog.get("/Metadata"))
            if hasattr(meta_stream, "get_data"):
                raw = meta_stream.get_data()
                root = ET.fromstring(raw)
                ns = {
                    "dc": "http://purl.org/dc/elements/1.1/",
                    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                }
                for li in root.findall(".//dc:title//rdf:li", ns):
                    if li.text and li.text.strip():
                        return li.text.strip()
                title_el = root.find(".//dc:title", ns)
                if title_el is not None and title_el.text and title_el.text.strip():
                    return title_el.text.strip()
    except Exception:
        pass
    return ""


def sort_widgets_visual(
    widgets: list[dict[str, Any]],
    layout: str = "rows",
    y_tolerance: float = 10.0,
) -> list[dict[str, Any]]:
    """Sort widget records into deterministic visual reading order.

    PDF coordinate space has origin at bottom-left, so higher y1/y_top is higher on the page.
    - rows: cluster top-to-bottom within y_tolerance points, then left-to-right (ascending x0).
    - columns: cluster left-to-right by x0 bands (36pt band), then top-to-bottom (descending y_top).
    """
    if not widgets:
        return []

    if layout == "columns":
        ordered = sorted(widgets, key=lambda w: (round(w["rect"][0] / 36.0), -w["rect"][3], w["rect"][0]))
        return ordered

    # Row-first clustering with y_tolerance
    by_top = sorted(widgets, key=lambda w: (-w["rect"][3], w["rect"][0]))
    rows: list[list[dict[str, Any]]] = []
    row_anchors: list[float] = []
    for w in by_top:
        y_top = w["rect"][3]
        placed = False
        for idx, anchor in enumerate(row_anchors):
            if abs(anchor - y_top) <= y_tolerance:
                rows[idx].append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
            row_anchors.append(y_top)

    result: list[dict[str, Any]] = []
    for row in rows:
        row.sort(key=lambda w: (w["rect"][0], -w["rect"][3]))
        result.extend(row)
    return result


def has_text_operators(page: Any) -> bool:
    """Return True if a page contains extractable text or PDF text-showing operators."""
    try:
        txt = page.extract_text() or ""
        if txt.strip():
            return True
    except Exception:
        pass

    try:
        contents = page.get_contents()
        if contents is not None:
            data = contents.get_data() if hasattr(contents, "get_data") else b""
            if re.search(rb"\b(Tj|TJ|'|\"|BT)\b", data):
                # Require an actual text-showing operator, not just empty BT/ET
                if re.search(rb"(\)|>|\])\s*(Tj|TJ)\b", data):
                    return True
    except Exception:
        pass
    return False


def build_page_index(reader: PdfReader) -> dict[int, int]:
    """Map page indirect object idnum -> 1-based page number."""
    page_map: dict[int, int] = {}
    for idx, page in enumerate(reader.pages, start=1):
        ref = getattr(page, "indirect_reference", None)
        if ref is not None:
            page_map[ref.idnum] = idx
    return page_map


def inspect_structure_tree(
    struct_root: DictionaryObject,
    page_map: dict[int, int],
) -> dict[str, Any]:
    """Traverse /StructTreeRoot iteratively to detect MCID duplicates, heading skips,
    missing Figure /Alt text, and Table tags missing <TH> cells.
    """
    role_map_raw = resolve(struct_root.get("/RoleMap"))
    role_map: dict[str, str] = {}
    if isinstance(role_map_raw, DictionaryObject):
        for k, v in role_map_raw.items():
            role_map[clean_str(k).lstrip("/")] = clean_str(v).lstrip("/")

    def map_role(tag: str) -> str:
        seen_r: set[str] = set()
        cur = tag.lstrip("/")
        while cur in role_map and cur not in seen_r:
            seen_r.add(cur)
            cur = role_map[cur]
        return cur

    # Per-page MCID occurrences: page_num -> mcid -> list of obj_id strings
    mcid_occurrences: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    headings: list[dict[str, Any]] = []
    missing_alt_figures: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    node_count = 0

    # Helper to check if a Table subtree contains any TH tag
    def subtree_has_th(node_obj: Any, local_visited: set[int]) -> bool:
        resolved = resolve(node_obj)
        if not isinstance(resolved, DictionaryObject):
            return False
        ref = getattr(node_obj, "idnum", None)
        if ref is not None:
            if ref in local_visited:
                return False
            local_visited.add(ref)
        s_tag = map_role(clean_str(resolved.get("/S")))
        if s_tag == "TH":
            return True
        kids = resolve(resolved.get("/K"))
        if kids is None:
            return False
        kid_list = list(kids) if isinstance(kids, ArrayObject) else [kids]
        for child in kid_list:
            if subtree_has_th(child, local_visited):
                return True
        return False

    # Iterative DFS preserving document order
    root_k = struct_root.get("/K")
    if root_k is None:
        return {
            "has_kids": False,
            "node_count": 0,
            "duplicate_mcids": [],
            "heading_skips": [],
            "missing_alt_figures": [],
            "table_header_issues": [],
        }

    # Stack items: (raw_obj, inherited_page_num)
    initial_kids = list(resolve(root_k)) if isinstance(resolve(root_k), ArrayObject) else [root_k]
    stack: list[tuple[Any, int | None]] = [(k, None) for k in reversed(initial_kids)]
    visited_nodes: set[int] = set()

    while stack:
        raw_item, inherited_page = stack.pop()
        if isinstance(raw_item, IndirectObject):
            if raw_item.idnum in visited_nodes:
                continue
            visited_nodes.add(raw_item.idnum)

        item = resolve(raw_item)
        if item is None:
            continue

        # Direct integer MCID leaf
        if isinstance(item, (int, NumberObject)):
            pg = inherited_page or 1
            mcid_occurrences[pg][int(item)].append(obj_id_str(raw_item))
            continue

        if not isinstance(item, DictionaryObject):
            continue

        item_type = clean_str(item.get("/Type")).lstrip("/")
        pg_ref = item.get("/Pg")
        cur_page = inherited_page
        if isinstance(pg_ref, IndirectObject) and pg_ref.idnum in page_map:
            cur_page = page_map[pg_ref.idnum]

        # Marked-content reference dictionary (/Type /MCR)
        if item_type == "MCR" or ("/MCID" in item and "/S" not in item):
            mcid_val = resolve(item.get("/MCID"))
            if isinstance(mcid_val, (int, NumberObject)):
                pg = cur_page or 1
                mcid_occurrences[pg][int(mcid_val)].append(obj_id_str(raw_item))
            continue

        # Structure element
        raw_s = clean_str(item.get("/S")).lstrip("/")
        if raw_s:
            node_count += 1
            mapped_s = map_role(raw_s)
            oid = obj_id_str(raw_item)
            pg = cur_page or 1

            # Headings H1..H6
            m_head = re.fullmatch(r"H([1-6])", mapped_s)
            if m_head:
                headings.append(
                    {
                        "level": int(m_head.group(1)),
                        "tag": mapped_s,
                        "page": pg,
                        "object_id": oid,
                    }
                )

            # Figure alt text check
            if mapped_s == "Figure":
                alt_text = clean_str(item.get("/Alt"))
                if not alt_text:
                    missing_alt_figures.append(
                        {
                            "page": pg,
                            "object_id": oid,
                            "tag": raw_s,
                            "issue": "Figure tag is missing non-empty /Alt text.",
                            "requires_human_review": True,
                        }
                    )

            # Table header check
            if mapped_s == "Table":
                if not subtree_has_th(raw_item, set()):
                    tables.append(
                        {
                            "page": pg,
                            "object_id": oid,
                            "tag": raw_s,
                            "issue": "Table structure element has no descendant <TH> header cell.",
                        }
                    )

        # Traverse children /K
        kids_raw = item.get("/K")
        kids = resolve(kids_raw)
        if kids is not None:
            if isinstance(kids, (int, NumberObject)):
                pg = cur_page or 1
                mcid_occurrences[pg][int(kids)].append(obj_id_str(raw_item))
            elif isinstance(kids, ArrayObject):
                for child in reversed(list(kids)):
                    child_res = resolve(child)
                    if isinstance(child_res, (int, NumberObject)):
                        pg = cur_page or 1
                        mcid_occurrences[pg][int(child_res)].append(obj_id_str(raw_item))
                    else:
                        stack.append((child, cur_page))
            else:
                stack.append((kids_raw, cur_page))

    # Build duplicate MCIDs list
    duplicate_mcids: list[dict[str, Any]] = []
    for pg in sorted(mcid_occurrences.keys()):
        for mcid_val in sorted(mcid_occurrences[pg].keys()):
            refs = mcid_occurrences[pg][mcid_val]
            if len(refs) > 1:
                duplicate_mcids.append(
                    {
                        "page": pg,
                        "mcid": mcid_val,
                        "occurrence_count": len(refs),
                        "object_ids": refs,
                        "issue": f"Duplicate MCID {mcid_val} on page {pg} ({len(refs)} references); root cause of PAC 2026 freezes.",
                    }
                )

    # Build heading skips list
    heading_skips: list[dict[str, Any]] = []
    prev_heading: dict[str, Any] | None = None
    for h in headings:
        if prev_heading is not None and h["level"] > prev_heading["level"] + 1:
            heading_skips.append(
                {
                    "page": h["page"],
                    "object_id": h["object_id"],
                    "previous_level": f"H{prev_heading['level']}",
                    "previous_page": prev_heading["page"],
                    "current_level": f"H{h['level']}",
                    "issue": f"Skipped heading level from H{prev_heading['level']} to H{h['level']}.",
                }
            )
        prev_heading = h

    return {
        "has_kids": node_count > 0 or bool(mcid_occurrences),
        "node_count": node_count,
        "duplicate_mcids": duplicate_mcids,
        "heading_skips": heading_skips,
        "missing_alt_figures": missing_alt_figures,
        "table_header_issues": tables,
    }


def inspect_acroform_pages(
    reader: PdfReader,
    layout: str = "rows",
    y_tolerance: float = 10.0,
    progress_every: int = 50,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, bool]:
    """Inspect all pages for AcroForm /Widget tab order, /Tabs key, /TU tooltips, and text presence."""
    tab_mismatches: list[dict[str, Any]] = []
    missing_tooltips: list[dict[str, Any]] = []
    total_widgets = 0
    any_text = False
    total_pages = len(reader.pages)

    for page_idx, page in enumerate(reader.pages, start=1):
        if total_pages > 200 and (page_idx == 1 or page_idx % progress_every == 0 or page_idx == total_pages):
            print(f"[progress] Inspected page {page_idx}/{total_pages}", file=sys.stderr)

        if not any_text and has_text_operators(page):
            any_text = True

        annots = resolve(page.get("/Annots"))
        if not isinstance(annots, ArrayObject):
            continue

        widgets: list[dict[str, Any]] = []
        for annot_pos, raw_annot in enumerate(annots):
            annot = resolve(raw_annot)
            if not isinstance(annot, DictionaryObject):
                continue
            if clean_str(annot.get("/Subtype")) != "/Widget":
                continue

            rect_raw = resolve(annot.get("/Rect"))
            if isinstance(rect_raw, ArrayObject) and len(rect_raw) == 4:
                coords = [float(resolve(c)) for c in rect_raw]
                rect = [
                    min(coords[0], coords[2]),
                    min(coords[1], coords[3]),
                    max(coords[0], coords[2]),
                    max(coords[1], coords[3]),
                ]
            else:
                rect = [0.0, 0.0, 0.0, 0.0]

            parent = resolve(annot.get("/Parent"))
            field_name = clean_str(annot.get("/T"))
            if not field_name and isinstance(parent, DictionaryObject):
                field_name = clean_str(parent.get("/T"))

            tu = clean_str(annot.get("/TU"))
            if not tu and isinstance(parent, DictionaryObject):
                tu = clean_str(parent.get("/TU"))

            oid = obj_id_str(raw_annot)
            w_rec = {
                "annot_index": annot_pos,
                "object_id": oid,
                "field_name": field_name or f"unnamed_widget_{annot_pos}",
                "tooltip": tu,
                "rect": [round(c, 2) for c in rect],
            }
            widgets.append(w_rec)
            total_widgets += 1

            if not tu:
                missing_tooltips.append(
                    {
                        "page": page_idx,
                        "object_id": oid,
                        "field_name": w_rec["field_name"],
                        "issue": "AcroForm widget is missing /TU accessible tooltip.",
                    }
                )

        if not widgets:
            continue

        tabs_val = clean_str(page.get("/Tabs"))
        expected = sort_widgets_visual(widgets, layout=layout, y_tolerance=y_tolerance)
        current_ids = [w["object_id"] for w in widgets]
        expected_ids = [w["object_id"] for w in expected]
        order_mismatch = current_ids != expected_ids
        tabs_mismatch = tabs_val != "/S"

        if order_mismatch or tabs_mismatch:
            mismatched_fields = []
            for pos, (cur_w, exp_w) in enumerate(zip(widgets, expected)):
                if cur_w["object_id"] != exp_w["object_id"]:
                    mismatched_fields.append(
                        {
                            "position": pos,
                            "current_field": cur_w["field_name"],
                            "current_object_id": cur_w["object_id"],
                            "expected_field": exp_w["field_name"],
                            "expected_object_id": exp_w["object_id"],
                        }
                    )
            tab_mismatches.append(
                {
                    "page": page_idx,
                    "widget_count": len(widgets),
                    "tabs_key": tabs_val or "MISSING",
                    "tabs_key_valid": not tabs_mismatch,
                    "annots_order_valid": not order_mismatch,
                    "layout_mode": layout,
                    "current_order": [w["field_name"] for w in widgets],
                    "expected_visual_order": [w["field_name"] for w in expected],
                    "mismatched_positions": mismatched_fields,
                }
            )

    return tab_mismatches, missing_tooltips, total_widgets, any_text


def compute_pass_score(audit: dict[str, Any]) -> float:
    """Compute deterministic 0.0 to 100.0 pass score across 7 accessibility categories."""
    score = 0.0
    meta = audit["metadata_checks"]

    # 1. Tagging (25 pts)
    if audit["is_tagged"] and meta["mark_info_marked"]:
        score += 25.0
    elif audit["is_tagged"]:
        score += 15.0

    # 2. Core metadata (15 pts: 5 each)
    if meta["has_lang"]:
        score += 5.0
    if meta["has_title"] and meta["has_xmp_title"]:
        score += 5.0
    elif meta["has_title"] or meta["has_xmp_title"]:
        score += 2.5
    if meta["display_doc_title"]:
        score += 5.0

    # 3. Duplicate MCIDs (15 pts)
    if audit["is_tagged"] and not audit["duplicate_mcids"]:
        score += 15.0

    # 4. Heading hierarchy (10 pts)
    if audit["is_tagged"] and not audit["heading_skips"]:
        score += 10.0

    # 5. Figure alt text (15 pts)
    if audit["is_tagged"] and not audit["missing_alt_figures"]:
        score += 15.0

    # 6. Table headers (10 pts)
    if audit["is_tagged"] and not audit["table_header_issues"]:
        score += 10.0

    # 7. AcroForm tab order & tooltips (10 pts)
    if not audit["form_tab_order_mismatches"] and not audit["missing_form_tooltips"]:
        score += 10.0
    elif not audit["form_tab_order_mismatches"]:
        score += 5.0

    return round(score, 1)


def render_markdown_report(audit: dict[str, Any], template_path: Path | None = None) -> str:
    """Render pdf_ua_remediation_plan.md from audit data."""
    meta = audit["metadata_checks"]
    banners_lines: list[str] = []
    if "SCANNED_IMAGE_PDF" in audit["banners"]:
        banners_lines.append(
            "> **SCANNED_IMAGE_PDF**: Zero text operators and no `/StructTreeRoot` were detected. "
            "Run OCR and recreate the document from an editable source file before tagging."
        )
    if "CRITICAL_UNTAGGED_PDF" in audit["banners"]:
        banners_lines.append(
            "> **CRITICAL_UNTAGGED_PDF**: `/StructTreeRoot` is absent or empty (commonly caused by Print-to-PDF). "
            "Re-export from the source Word or InDesign file using Save As PDF (Tagged) rather than spending hours manually tagging in Acrobat."
        )
    banners_block = "\n\n".join(banners_lines) if banners_lines else "No critical untagged or scanned-image gates triggered."

    rows: list[str] = []
    max_rows_per_cat = 25 if audit["page_count"] > 200 else 200

    if not audit["is_tagged"]:
        rows.append(
            "| Critical | Tagging | All | Catalog | Missing or empty `/StructTreeRoot` | Re-export tagged PDF from source Word/InDesign |"
        )
    if not meta["mark_info_marked"]:
        rows.append(
            "| High | Metadata | All | Catalog | `/MarkInfo/Marked` is missing or false | Enable Tagged PDF on export |"
        )
    if not meta["has_lang"]:
        rows.append(
            "| High | Metadata | All | Catalog | Missing `/Lang` BCP-47 language code | Patch with `pdf_ua_safe_patch.py --lang en-US` or set in source |"
        )
    if not (meta["has_title"] and meta["has_xmp_title"]):
        rows.append(
            "| Medium | Metadata | All | Info/XMP | Missing document `/Title` or XMP `dc:title` | Set descriptive title via `pdf_ua_safe_patch.py --title` |"
        )
    if not meta["display_doc_title"]:
        rows.append(
            "| Medium | Metadata | All | Catalog | `/ViewerPreferences/DisplayDocTitle` not true | Patch with `pdf_ua_safe_patch.py` |"
        )

    for item in audit["duplicate_mcids"][:max_rows_per_cat]:
        oids = ", ".join(item["object_ids"])
        rows.append(
            f"| Critical | Duplicate MCID | {item['page']} | `{oids}` | MCID `{item['mcid']}` duplicated {item['occurrence_count']}x (PAC 2026 freeze risk) | Re-export via IDML/source or delete redundant container in Acrobat Tags |"
        )
    if len(audit["duplicate_mcids"]) > max_rows_per_cat:
        rows.append(
            f"| Critical | Duplicate MCID | Multiple | See JSON | {len(audit['duplicate_mcids']) - max_rows_per_cat} additional duplicate MCIDs summarized | Inspect `duplicate_mcids` in `pdf_ua_audit.json` |"
        )

    for item in audit["heading_skips"][:max_rows_per_cat]:
        rows.append(
            f"| High | Heading Skip | {item['page']} | `{item['object_id']}` | Skipped from `{item['previous_level']}` to `{item['current_level']}` | Fix Heading style in source or Tag Properties in Acrobat |"
        )

    for item in audit["missing_alt_figures"][:max_rows_per_cat]:
        rows.append(
            f"| High | Missing Alt Text | {item['page']} | `{item['object_id']}` | `<Figure>` missing `/Alt` text (human review required) | Add semantic `/Alt` in source or mark decorative as Artifact |"
        )
    if len(audit["missing_alt_figures"]) > max_rows_per_cat:
        rows.append(
            f"| High | Missing Alt Text | Multiple | See JSON | {len(audit['missing_alt_figures']) - max_rows_per_cat} additional figures missing `/Alt` | Inspect `missing_alt_figures` in `pdf_ua_audit.json` |"
        )

    for item in audit["table_header_issues"][:max_rows_per_cat]:
        rows.append(
            f"| High | Table Headers | {item['page']} | `{item['object_id']}` | `<Table>` missing `<TH>` header cells | Mark header row in Word/InDesign or set `<TH>` Scope in Acrobat |"
        )

    for item in audit["form_tab_order_mismatches"][:max_rows_per_cat]:
        rows.append(
            f"| High | Form Tab Order | {item['page']} | Page `{item['page']}` `/Annots` | {item['widget_count']} widgets; `/Tabs={item['tabs_key']}`, visual order match=`{item['annots_order_valid']}` | Run `pdf_ua_safe_patch.py --layout {item['layout_mode']}` to set `/Tabs /S` and sort `/Annots` |"
        )

    for item in audit["missing_form_tooltips"][:max_rows_per_cat]:
        rows.append(
            f"| Medium | Form Tooltip | {item['page']} | `{item['object_id']}` | Field `{item['field_name']}` missing `/TU` tooltip | Add descriptive Tooltip in Acrobat Prepare Form |"
        )
    if len(audit["missing_form_tooltips"]) > max_rows_per_cat:
        rows.append(
            f"| Medium | Form Tooltip | Multiple | See JSON | {len(audit['missing_form_tooltips']) - max_rows_per_cat} additional fields missing `/TU` | Inspect `missing_form_tooltips` in `pdf_ua_audit.json` |"
        )

    defect_rows = "\n".join(rows) if rows else "| None | All Checks | All | None | No automated defects detected | Proceed to manual NVDA/JAWS verification |"

    source_steps = (
        "- Re-export from Microsoft Word (**File > Save As > PDF > Options > Document structure tags**) or Adobe InDesign (**File > Export > Adobe PDF (Interactive)** with **Create Tagged PDF** and **Use Structure for Tab Order**).\n"
        "- Never use Print-to-PDF virtual printers, which strip `/StructTreeRoot`.\n"
        "- Fix heading skips, figure alt text, and table `<TH>` header rows in the source file before final export."
    )
    safe_patch_steps = (
        f"- Run `python3 scripts/pdf_ua_safe_patch.py \"{audit['file']}\" --lang en-US --layout {audit['layout_mode']}` to write a non-destructive `<stem>_patched.pdf` copy setting `/Lang`, `/Title` + XMP `dc:title`, `/DisplayDocTitle true`, page `/Tabs /S`, and visually sorted AcroForm `/Annots`."
    )
    acrobat_steps = (
        "- Set `<TH>` Scope (`Column`/`Row`) via Reading Order > Table Editor when source files are unavailable.\n"
        "- Author human-reviewed `/Alt` text for charts/diagrams and populate `/TU` tooltips in Prepare Form."
    )

    if template_path and template_path.is_file():
        tpl = template_path.read_text(encoding="utf-8")
    else:
        default_tpl = Path(__file__).resolve().parent.parent / "assets" / "remediation_report_template.md"
        tpl = default_tpl.read_text(encoding="utf-8") if default_tpl.is_file() else ""

    if tpl:
        replacements = {
            "{{FILE_NAME}}": audit["file"],
            "{{STANDARD}}": audit["standard"],
            "{{STATUS}}": audit["status"],
            "{{IS_TAGGED}}": "Yes" if audit["is_tagged"] else "No",
            "{{PAGE_COUNT}}": str(audit["page_count"]),
            "{{PASS_SCORE_PCT}}": str(audit["pass_score_pct"]),
            "{{BANNERS_BLOCK}}": banners_block,
            "{{STRUCT_STATUS}}": "PASS" if audit["is_tagged"] else "FAIL",
            "{{STRUCT_VAL}}": "Present" if audit["is_tagged"] else "Absent or Empty",
            "{{STRUCT_ACTION}}": "None" if audit["is_tagged"] else "Re-export tagged PDF from source",
            "{{MARKED_STATUS}}": "PASS" if meta["mark_info_marked"] else "FAIL",
            "{{MARKED_VAL}}": str(meta["mark_info_marked"]),
            "{{MARKED_ACTION}}": "None" if meta["mark_info_marked"] else "Enable Tagged PDF on export",
            "{{LANG_STATUS}}": "PASS" if meta["has_lang"] else "FAIL",
            "{{LANG_VAL}}": meta["lang"] or "MISSING",
            "{{LANG_ACTION}}": "None" if meta["has_lang"] else "Set BCP-47 /Lang via safe patch or source",
            "{{TITLE_STATUS}}": "PASS" if (meta["has_title"] and meta["has_xmp_title"]) else "FAIL",
            "{{TITLE_VAL}}": meta["xmp_title"] or meta["info_title"] or "MISSING",
            "{{TITLE_ACTION}}": "None" if (meta["has_title"] and meta["has_xmp_title"]) else "Set /Title and XMP dc:title",
            "{{DISPLAY_TITLE_STATUS}}": "PASS" if meta["display_doc_title"] else "FAIL",
            "{{DISPLAY_TITLE_VAL}}": str(meta["display_doc_title"]),
            "{{DISPLAY_TITLE_ACTION}}": "None" if meta["display_doc_title"] else "Set /ViewerPreferences/DisplayDocTitle true",
            "{{DEFECT_ROWS}}": defect_rows,
            "{{SOURCE_FIX_STEPS}}": source_steps,
            "{{SAFE_PATCH_STEPS}}": safe_patch_steps,
            "{{ACROBAT_STEPS}}": acrobat_steps,
        }
        out = tpl
        for k, v in replacements.items():
            out = out.replace(k, v)
        return out

    return f"# PDF/UA Accessibility Remediation Plan\n\n{banners_block}\n\n{defect_rows}\n"


def inspect_pdf(
    pdf_path: Path,
    standard: str = "PDF/UA-1",
    layout: str = "rows",
    y_tolerance: float = 10.0,
    progress_every: int = 50,
) -> tuple[dict[str, Any], int]:
    """Run full pre-flight inspection on pdf_path and return (audit_dict, exit_code)."""
    reader = PdfReader(str(pdf_path))
    if reader.is_encrypted:
        result = {
            "file": str(pdf_path),
            "standard": standard,
            "status": "ENCRYPTED_PDF",
            "banners": ["ENCRYPTED_PDF"],
            "error": "PDF is encrypted or password-protected. Provide an unlocked copy before auditing.",
            "is_tagged": False,
            "page_count": 0,
            "metadata_checks": {},
            "duplicate_mcids": [],
            "heading_skips": [],
            "missing_alt_figures": [],
            "table_header_issues": [],
            "form_tab_order_mismatches": [],
            "missing_form_tooltips": [],
            "pass_score_pct": 0.0,
        }
        return result, 3

    catalog = resolve(reader.trailer.get("/Root"))
    if not isinstance(catalog, DictionaryObject):
        catalog = DictionaryObject()

    mark_info = resolve(catalog.get("/MarkInfo"))
    mark_info_marked = bool(
        isinstance(mark_info, DictionaryObject) and bool(resolve(mark_info.get("/Marked")))
    )
    lang_val = clean_str(catalog.get("/Lang"))

    viewer_prefs = resolve(catalog.get("/ViewerPreferences"))
    display_doc_title = bool(
        isinstance(viewer_prefs, DictionaryObject)
        and bool(resolve(viewer_prefs.get("/DisplayDocTitle")))
    )

    info_title = ""
    try:
        if reader.metadata and reader.metadata.title:
            info_title = str(reader.metadata.title).strip()
    except Exception:
        pass
    xmp_title = extract_xmp_dc_title(reader)

    acroform = resolve(catalog.get("/AcroForm"))
    acroform_fields_count = 0
    if isinstance(acroform, DictionaryObject):
        fields = resolve(acroform.get("/Fields"))
        if isinstance(fields, ArrayObject):
            acroform_fields_count = len(fields)

    page_map = build_page_index(reader)
    struct_root = resolve(catalog.get("/StructTreeRoot"))
    if isinstance(struct_root, DictionaryObject):
        struct_res = inspect_structure_tree(struct_root, page_map)
        is_tagged = bool(struct_res["has_kids"])
    else:
        struct_res = {
            "has_kids": False,
            "node_count": 0,
            "duplicate_mcids": [],
            "heading_skips": [],
            "missing_alt_figures": [],
            "table_header_issues": [],
        }
        is_tagged = False

    tab_mismatches, missing_tooltips, total_widgets, any_text = inspect_acroform_pages(
        reader=reader,
        layout=layout,
        y_tolerance=y_tolerance,
        progress_every=progress_every,
    )

    is_scanned_image_only = (not is_tagged) and (not any_text) and (total_widgets == 0)
    banners: list[str] = []
    if is_scanned_image_only:
        banners.append("SCANNED_IMAGE_PDF")
    if not is_tagged:
        banners.append("CRITICAL_UNTAGGED_PDF")

    metadata_checks = {
        "struct_tree_root_present": isinstance(struct_root, DictionaryObject),
        "struct_tree_has_nodes": struct_res["has_kids"],
        "struct_node_count": struct_res["node_count"],
        "mark_info_marked": mark_info_marked,
        "has_lang": bool(lang_val),
        "lang": lang_val or None,
        "has_title": bool(info_title),
        "info_title": info_title or None,
        "has_xmp_title": bool(xmp_title),
        "xmp_title": xmp_title or None,
        "display_doc_title": display_doc_title,
        "acroform_fields_count": acroform_fields_count,
        "widget_annotations_count": total_widgets,
    }

    audit: dict[str, Any] = {
        "file": str(pdf_path),
        "standard": standard,
        "layout_mode": layout,
        "status": "OK",
        "banners": banners,
        "page_count": len(reader.pages),
        "is_tagged": is_tagged,
        "is_scanned_image_only": is_scanned_image_only,
        "metadata_checks": metadata_checks,
        "duplicate_mcids": struct_res["duplicate_mcids"],
        "heading_skips": struct_res["heading_skips"],
        "missing_alt_figures": struct_res["missing_alt_figures"],
        "table_header_issues": struct_res["table_header_issues"],
        "form_tab_order_mismatches": tab_mismatches,
        "missing_form_tooltips": missing_tooltips,
        "pass_score_pct": 0.0,
        "human_verification_required": (
            "Semantic alt text on complex charts/medical figures requires human review, "
            "and final NVDA/JAWS screen-reader verification is required for legal compliance."
        ),
    }
    audit["pass_score_pct"] = compute_pass_score(audit)
    return audit, 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pdf_ua_inspect.py",
        description="Inspect a PDF for PDF/UA-1, WCAG 2.1 AA, and Section 508 accessibility defects.",
        epilog="Exit codes: 0 = completed, 2 = invalid input/missing dependency, 3 = encrypted PDF.",
    )
    parser.add_argument("pdf_path", help="Path to target .pdf file")
    parser.add_argument(
        "--standard",
        default="PDF/UA-1",
        choices=["PDF/UA-1", "WCAG 2.1 AA", "Section 508"],
        help="Target accessibility standard (default: PDF/UA-1)",
    )
    parser.add_argument(
        "--layout",
        default="rows",
        choices=["rows", "columns"],
        help="Expected AcroForm visual reading order (default: rows)",
    )
    parser.add_argument(
        "--y-tolerance",
        type=float,
        default=10.0,
        help="Vertical Y-tolerance in points for grouping form widgets into rows (default: 10.0)",
    )
    parser.add_argument("--json-out", help="Optional path to write pdf_ua_audit.json")
    parser.add_argument("--md-out", help="Optional path to write pdf_ua_remediation_plan.md")
    parser.add_argument("--template", help="Optional path to custom Markdown remediation template")
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Log progress to stderr every N pages on PDFs > 200 pages (default: 50)",
    )
    args = parser.parse_args()

    if not _HAS_PYPDF:
        print(
            json.dumps(
                {
                    "status": "MISSING_DEPENDENCY",
                    "error": "pypdf is required. Install with: pip install pypdf",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    pdf_path = Path(args.pdf_path).resolve()
    if not pdf_path.is_file():
        print(json.dumps({"status": "ERROR", "error": f"File not found: {pdf_path}"}), file=sys.stderr)
        return 2

    audit, code = inspect_pdf(
        pdf_path=pdf_path,
        standard=args.standard,
        layout=args.layout,
        y_tolerance=args.y_tolerance,
        progress_every=args.progress_every,
    )

    if args.json_out:
        out_json = Path(args.json_out)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")

    if args.md_out and code == 0:
        out_md = Path(args.md_out)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        tpl_path = Path(args.template).resolve() if args.template else None
        out_md.write_text(render_markdown_report(audit, template_path=tpl_path), encoding="utf-8")

    print(json.dumps(audit, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
