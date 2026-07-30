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

**Project Artifact Store**:
The content-addressed collection of immutable Page Artifact files referenced by both the current project and its snapshots.
_Avoid_: Current output directory, snapshot-only blob store

**Project Head**:
The fully committed set of Page Artifact revisions currently visible to the user. An unfinished or failed project command never advances it.
_Avoid_: Workflow stage, latest files on disk

**Pending Artifact Set**:
Uncommitted Page Artifact revisions produced by an active or interrupted project command. They may be reused when the command resumes but are not part of the Project Head.
_Avoid_: Partial project result

**Project Command**:
A typed request for one canonical workflow action against a project, optionally narrowed to one page and based on an expected Page Revision. It expresses user intent without carrying loaded project state, storage paths, or transaction machinery.
_Avoid_: Session task payload, Engine call parameters

Compatibility removal condition: the task starter's ignored legacy `session` input is removed once WU-0A callers and regressions construct only Project Commands.

**Project Glossary Candidate**:
A source-language clue supported by high-confidence OCR evidence such as an explicit self-introduction or honorific. A candidate highlights evidence inside the broad LLM extraction context but does not define the final word boundary and is not an accepted glossary entry until the model supplies a translation.
_Avoid_: Automatically generated glossary translation

**Remote Task Node**:
An explicitly enabled, token-protected LAN service that persists across application restarts and executes trusted diagnostic, CUDA, and command tasks on the host computer. It is separate from the short-lived Read-only Diagnostics Service.
_Avoid_: Remote diagnostics when the capability can mutate files or start processes

**Remote Task Bundle**:
An isolated uploaded file or safely extracted archive used as a Remote Task working directory. A bundle is input material, not a project or a Page Artifact.
_Avoid_: Project upload when the files exist only for remote testing

**Remote Task**:
A persisted, cancellable unit of host work with a status, log, exit result, and downloadable artifacts. Remote Tasks run serially so GPU workloads do not compete for device memory.
_Avoid_: Project Command, which changes the canonical manga workflow
