# Manga Translation Workspace

This context describes the editable page model used from recognition through translation, review, typesetting, and export.

## Language

**Text Region**:
A bounded area on a page that owns source text, translation, typography, layout, and review state. Every text region has the same editing and rendering semantics regardless of how it originated.
_Avoid_: Manual box, automatic box, OCR box as separate entity types

**Region Origin**:
The provenance of a text region: `automatic` when recognition found it, `user` when a person added it, and `derived` when an editing operation produced it. Origin is descriptive metadata, not a separate editing model.
_Avoid_: Region type when referring only to provenance

**Page Document**:
The canonical editable representation of one manga page, including its ordered text regions and page-level image references.
_Avoid_: Review payload, manual-region list

**Page Artifact**:
A revisioned output of the page workflow, such as recognition, blank page, translation, or final typeset page.
_Avoid_: Step status when referring to the actual output

**Page Revision**:
The monotonic version of a Page Document. A Page Command may declare the revision it was based on so a stale editor cannot overwrite newer text-region state.
_Avoid_: UI request counter

**Snapshot Artifact Bundle**:
An immutable, content-addressed set of source images, translated images, Page Documents, and editable caches captured by a project snapshot.
_Avoid_: Snapshot when only a mutable output filename was recorded

**Project Glossary Candidate**:
A source-language clue supported by high-confidence OCR evidence such as an explicit self-introduction or honorific. A candidate highlights evidence inside the broad LLM extraction context but does not define the final word boundary and is not an accepted glossary entry until the model supplies a translation.
_Avoid_: Automatically generated glossary translation
