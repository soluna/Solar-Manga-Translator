# Use evidence-focused retries for project glossary extraction

Project Glossary extraction reads OCR source text from Page Documents. It gives the LLM as much project context as the request budget allows and highlights evidence discovered from explicit self-introductions and honorifics. The highlighted evidence is not a pre-tokenized term list: the LLM remains responsible for word boundaries and selection. Missing evidence triggers a second review over the same broad context. Candidates never become accepted entries without a model-supplied translation.

## Consequences

- The request budget keeps up to 48,000 OCR characters, with highlighted evidence duplicated near the instructions so it is not lost in the middle of a long project.
- Providers that reject the large prompt with an explicit context-length error are retried with a compact context budget; authentication and other failures are not disguised as context errors.
- A model may return valid JSON, structured chat content, reasoning content, or explicitly labeled non-JSON fields; the Adapter normalizes these forms before glossary validation.
- Existing entries and newly extracted entries are merged by source term.
- Empty reviews remain retryable. A successful evidence review is authoritative even when its chosen boundary does not exactly match detector clues. An empty glossary is never a completed extraction, including in projects written by older versions.
- Candidate coverage accepts a model-selected full name or shorter common name as coverage for overlapping detector clues; deterministic matching does not force the model's tokenization.
