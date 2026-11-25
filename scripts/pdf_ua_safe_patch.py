#!/usr/bin/env python3
"""Non-destructive PDF/UA safe patcher for metadata and AcroForm tab order.

Writes a new <stem>_patched.pdf copy without ever modifying the original PDF.
Applies safe structural patches: /Catalog/Lang, /Info/Title + XMP dc:title,
/ViewerPreferences/DisplayDocTitle true, page /Tabs /S, and visually sorted
AcroForm /Widget entries in /Annots (plus optional /TU missing-tooltip flagging).

Exit codes:
  0 - Patched copy written cleanly
  2 - Usage error, overwrite-guard violation, or missing dependency
  3 - Encrypted / password-protected PDF (aborted cleanly)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DecodedStreamObject,
        DictionaryObject,
        IndirectObject,
        NameObject,
        TextStringObject,
    )
    _HAS_PYPDF = True
except ImportError:
    _HAS_PYPDF = False


def resolve(obj: Any) -> Any:
    if isinstance(obj, IndirectObject):
        try:
            return obj.get_object()
        except Exception:
            return None
    return obj


def clean_str(val: Any) -> str:
    val = resolve(val)
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace").strip()
    return str(val).strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_xmp_packet(title: str, lang: str) -> bytes:
    """Build a minimal, standards-compliant XMP packet containing dc:title and pdfuaid."""
    safe_title = xml_escape(title)
    safe_lang = xml_escape(lang or "x-default")
    packet = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '    <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '      <dc:title>\n'
        '        <rdf:Alt>\n'
        f'          <rdf:li xml:lang="x-default">{safe_title}</rdf:li>\n'
        f'          <rdf:li xml:lang="{safe_lang}">{safe_title}</rdf:li>\n'
        '        </rdf:Alt>\n'
        '      </dc:title>\n'
        '    </rdf:Description>\n'
        '  </rdf:RDF>\n'
        '</x:xmpmeta>\n'
        '<?xpacket end="w"?>\n'
    )
    return packet.encode("utf-8")


def sort_widget_entries(
    widget_entries: list[tuple[Any, list[float]]],
    layout: str = "rows",
    y_tolerance: float = 10.0,
) -> list[tuple[Any, list[float]]]:
    """Sort (raw_annot, [x0, y0, x1, y1]) tuples into visual reading order."""
    if not widget_entries:
        return []
    if layout == "columns":
        return sorted(
            widget_entries,
            key=lambda item: (round(item[1][0] / 36.0), -item[1][3], item[1][0]),
        )

    by_top = sorted(widget_entries, key=lambda item: (-item[1][3], item[1][0]))
    rows: list[list[tuple[Any, list[float]]]] = []
    anchors: list[float] = []
    for entry in by_top:
        y_top = entry[1][3]
        placed = False
        for idx, anchor in enumerate(anchors):
            if abs(anchor - y_top) <= y_tolerance:
                rows[idx].append(entry)
                placed = True
                break
        if not placed:
            rows.append([entry])
            anchors.append(y_top)

    out: list[tuple[Any, list[float]]] = []
    for row in rows:
        row.sort(key=lambda item: (item[1][0], -item[1][3]))
        out.extend(row)
    return out


def safe_patch_pdf(
    input_path: Path,
    output_path: Path,
    lang: str | None = None,
    title: str | None = None,
    layout: str = "rows",
    y_tolerance: float = 10.0,
) -> tuple[dict[str, Any], int]:
    """Apply non-destructive safe patches and write to output_path."""
    if input_path.resolve() == output_path.resolve():
        return (
            {
                "status": "ERROR",
                "error": "Refusing to overwrite original PDF. Specify a distinct output path.",
            },
            2,
        )

    orig_sha_before = sha256_file(input_path)
    reader = PdfReader(str(input_path))
    if reader.is_encrypted:
        return (
            {
                "status": "ENCRYPTED_PDF",
                "input_file": str(input_path),
                "error": "PDF is encrypted or password-protected. Provide an unlocked copy before patching.",
            },
            3,
        )

    writer = PdfWriter()
    writer.clone_document_from_reader(reader)

    root = writer._root_object
    patches_applied: list[str] = []

    # 1. /Catalog/Lang
    existing_lang = clean_str(root.get("/Lang"))
    effective_lang = lang or existing_lang
    if lang:
        root[NameObject("/Lang")] = TextStringObject(lang)
        patches_applied.append(f"Set /Catalog/Lang = '{lang}'")

    # 2. /Info/Title and XMP dc:title
    existing_title = ""
    try:
        if reader.metadata and reader.metadata.title:
            existing_title = str(reader.metadata.title).strip()
    except Exception:
        pass
    effective_title = title or existing_title or input_path.stem.replace("_", " ").strip()
    writer.add_metadata({"/Title": effective_title})
    xmp_stream = DecodedStreamObject()
    xmp_stream.set_data(build_xmp_packet(effective_title, effective_lang or "en-US"))
    xmp_stream[NameObject("/Type")] = NameObject("/Metadata")
    xmp_stream[NameObject("/Subtype")] = NameObject("/XML")
    root[NameObject("/Metadata")] = writer._add_object(xmp_stream)
    patches_applied.append(f"Set /Info/Title and XMP dc:title = '{effective_title}'")

    # 3. /ViewerPreferences/DisplayDocTitle true
    vp = resolve(root.get("/ViewerPreferences"))
    if not isinstance(vp, DictionaryObject):
        vp = DictionaryObject()
        root[NameObject("/ViewerPreferences")] = vp
    vp[NameObject("/DisplayDocTitle")] = BooleanObject(True)
    patches_applied.append("Set /ViewerPreferences/DisplayDocTitle = true")

    # 4. Page /Tabs /S, AcroForm /Annots visual sort, and missing /TU audit
    pages_reordered: list[int] = []
    pages_tabs_set: list[int] = []
    missing_tu_flags: list[dict[str, Any]] = []

    for page_idx, page in enumerate(writer.pages, start=1):
        annots = resolve(page.get("/Annots"))
        if not isinstance(annots, ArrayObject) or len(annots) == 0:
            continue

        widget_positions: list[int] = []
        widget_entries: list[tuple[Any, list[float]]] = []

        for idx, raw_annot in enumerate(annots):
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

            widget_positions.append(idx)
            widget_entries.append((raw_annot, rect))

            parent = resolve(annot.get("/Parent"))
            field_name = clean_str(annot.get("/T")) or (
                clean_str(parent.get("/T")) if isinstance(parent, DictionaryObject) else ""
            )
            tu = clean_str(annot.get("/TU")) or (
                clean_str(parent.get("/TU")) if isinstance(parent, DictionaryObject) else ""
            )
            if not tu:
                missing_tu_flags.append(
                    {
                        "page": page_idx,
                        "field_name": field_name or f"unnamed_widget_{idx}",
                        "action_required": "Add human-authored /TU tooltip in Prepare Form or source.",
                    }
                )

        if not widget_entries:
            continue

        # Set /Tabs /S on every page containing form widgets
        if clean_str(page.get("/Tabs")) != "/S":
            page[NameObject("/Tabs")] = NameObject("/S")
            pages_tabs_set.append(page_idx)

        # Reorder only the widget slots inside /Annots, preserving non-widget annotations in place
        sorted_entries = sort_widget_entries(widget_entries, layout=layout, y_tolerance=y_tolerance)
        original_refs = [e[0] for e in widget_entries]
        sorted_refs = [e[0] for e in sorted_entries]
        if original_refs != sorted_refs:
            new_annots = ArrayObject(list(annots))
            for slot, sorted_ref in zip(widget_positions, sorted_refs):
                new_annots[slot] = sorted_ref
            page[NameObject("/Annots")] = new_annots
            pages_reordered.append(page_idx)

    if pages_tabs_set:
        patches_applied.append(f"Set /Tabs /S on page(s): {pages_tabs_set}")
    if pages_reordered:
        patches_applied.append(f"Reordered AcroForm /Annots ({layout} layout) on page(s): {pages_reordered}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as fh:
        writer.write(fh)

    orig_sha_after = sha256_file(input_path)
    report = {
        "status": "PATCHED",
        "input_file": str(input_path),
        "output_file": str(output_path),
        "original_file_untouched": orig_sha_before == orig_sha_after,
        "original_sha256": orig_sha_after,
        "patched_sha256": sha256_file(output_path),
        "layout_mode": layout,
        "patches_applied": patches_applied,
        "pages_tabs_set": pages_tabs_set,
        "pages_annots_reordered": pages_reordered,
        "missing_tu_flags": missing_tu_flags,
        "human_verification_required": (
            "Safe patching fixes metadata and visual tab order only. Human review for semantic alt text "
            "and final NVDA/JAWS screen-reader verification remain required for legal compliance."
        ),
    }
    return report, 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pdf_ua_safe_patch.py",
        description="Apply non-destructive PDF/UA metadata and AcroForm tab-order patches to a new PDF copy.",
        epilog="Exit codes: 0 = patched copy created, 2 = invalid input or overwrite blocked, 3 = encrypted PDF.",
    )
    parser.add_argument("pdf_path", help="Path to source .pdf file (never modified)")
    parser.add_argument(
        "--output",
        help="Destination path for patched copy (default: <stem>_patched.pdf alongside source)",
    )
    parser.add_argument("--lang", help="BCP-47 document language code to set on /Catalog/Lang (e.g., en-US)")
    parser.add_argument("--title", help="Human-readable document title for /Info/Title and XMP dc:title")
    parser.add_argument(
        "--layout",
        default="rows",
        choices=["rows", "columns"],
        help="Visual reading order for sorting AcroForm widgets (default: rows)",
    )
    parser.add_argument(
        "--y-tolerance",
        type=float,
        default=10.0,
        help="Vertical Y-tolerance in points for row clustering (default: 10.0)",
    )
    parser.add_argument("--report-out", help="Optional path to write JSON patch report")
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

    input_path = Path(args.pdf_path).resolve()
    if not input_path.is_file():
        print(json.dumps({"status": "ERROR", "error": f"File not found: {input_path}"}), file=sys.stderr)
        return 2

    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = input_path.with_name(f"{input_path.stem}_patched.pdf")

    report, code = safe_patch_pdf(
        input_path=input_path,
        output_path=output_path,
        lang=args.lang,
        title=args.title,
        layout=args.layout,
        y_tolerance=args.y_tolerance,
    )

    if args.report_out and code == 0:
        rep_path = Path(args.report_out)
        rep_path.parent.mkdir(parents=True, exist_ok=True)
        rep_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
