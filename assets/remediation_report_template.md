# PDF/UA Accessibility Remediation Plan

## Executive Summary

- **File**: `{{FILE_NAME}}`
- **Target Standard**: `{{STANDARD}}`
- **Audit Status**: `{{STATUS}}`
- **Tagged PDF**: `{{IS_TAGGED}}`
- **Page Count**: `{{PAGE_COUNT}}`
- **Automated Pre-Flight Pass Score**: `{{PASS_SCORE_PCT}}%`

{{BANNERS_BLOCK}}

## Metadata Checks

| Check | Status | Current Value | Required Action |
| --- | --- | --- | --- |
| `/StructTreeRoot` Present | {{STRUCT_STATUS}} | {{STRUCT_VAL}} | {{STRUCT_ACTION}} |
| `/MarkInfo/Marked` | {{MARKED_STATUS}} | {{MARKED_VAL}} | {{MARKED_ACTION}} |
| `/Catalog/Lang` (BCP-47) | {{LANG_STATUS}} | {{LANG_VAL}} | {{LANG_ACTION}} |
| Document Title (`/Title` & XMP `dc:title`) | {{TITLE_STATUS}} | {{TITLE_VAL}} | {{TITLE_ACTION}} |
| `/ViewerPreferences/DisplayDocTitle` | {{DISPLAY_TITLE_STATUS}} | {{DISPLAY_TITLE_VAL}} | {{DISPLAY_TITLE_ACTION}} |

## Defect Inventory (Critical / High / Medium)

| Severity | Category | Page | PDF Object ID | Finding | Recommended Fix |
| --- | --- | --- | --- | --- | --- |
{{DEFECT_ROWS}}

## Remediation Blueprint: Source File vs. Acrobat

### Priority 1: Source-File Fixes (Word / InDesign)

{{SOURCE_FIX_STEPS}}

### Priority 2: Safe Non-Destructive PDF Patching

{{SAFE_PATCH_STEPS}}

### Priority 3: Acrobat Pro Manual Polish

{{ACROBAT_STEPS}}

## Human Review and Compliance Verification Notice

- **Semantic Alt Text**: Automated checks detect missing `/Alt` attributes only. Complex charts, infographics, and medical or technical diagrams require human subject-matter review to author accurate alternative text.
- **Legal Compliance Sign-Off**: Automated pre-flight inspection does not replace assistive-technology testing. Final verification with **NVDA** or **JAWS** (plus manual color-contrast and reading-order review) is required before certifying PDF/UA-1, WCAG 2.1 AA, or Section 508 compliance.
