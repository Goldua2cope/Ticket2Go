"""
Verify LLM-supplied quotes against the documents they claim to come from.

The LLM only returns (document_id, quote). The application locates the quote
in the real text and assigns offsets, so stored evidence is always traceable.
"""

import logging
import re

from .schemas import EvidenceRef, QuoteOutput, SourceDocument

logger = logging.getLogger(__name__)

# Very short quotes match almost anywhere and prove nothing.
MIN_QUOTE_CHARS = 8

# Typographic variants LLMs swap when copying. Every entry maps one character
# to one character, so offsets in the normalized text equal offsets in the original.
_TYPOGRAPHY = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ",
})


def normalize_typography(text: str) -> str:
    return text.translate(_TYPOGRAPHY)


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Return (start, end) of the quote in text, or None.

    Tries an exact match first, then tolerates differences in whitespace,
    letter case, and typographic quotes/dashes, which LLMs commonly introduce
    when copying. Offsets always refer to the original text.
    """
    quote = normalize_typography(quote.strip())
    if len(quote) < MIN_QUOTE_CHARS:
        return None

    normalized_text = normalize_typography(text)
    start = normalized_text.find(quote)
    if start >= 0:
        return start, start + len(quote)

    words = quote.split()
    escaped_words = [re.escape(word) for word in words]
    pattern = r"\s+".join(escaped_words)
    match = re.search(pattern, normalized_text, flags=re.IGNORECASE)
    if match is None:
        return None
    return match.start(), match.end()


def resolve_quotes(
    quotes: list[QuoteOutput],
    documents: dict[str, SourceDocument],
) -> list[EvidenceRef]:
    """Turn LLM quotes into verified evidence, dropping any that don't match."""
    verified: list[EvidenceRef] = []
    seen: set[tuple[str, int, int]] = set()

    for quote in quotes:
        document = documents.get(quote.document_id)
        if document is None:
            logger.info("Dropping quote citing unknown document %r", quote.document_id)
            continue

        span = locate_quote(document.text, quote.quote)
        if span is None:
            logger.info("Dropping unverifiable quote from %s", quote.document_id)
            continue

        start, end = span
        key = (document.document_id, start, end)
        if key in seen:
            continue
        seen.add(key)

        verified.append(
            EvidenceRef(
                document_id=document.document_id,
                source=document.source,
                quote=document.text[start:end],
                meeting_id=document.meeting_id,
                start_char=start,
                end_char=end,
            )
        )

    return verified


def merge_evidence(*groups: list[EvidenceRef]) -> list[EvidenceRef]:
    """Concatenate evidence lists, keeping the first copy of each quote."""
    merged: list[EvidenceRef] = []
    seen: set[tuple[str, int, int]] = set()
    for group in groups:
        for ref in group:
            key = (ref.document_id, ref.start_char, ref.end_char)
            if key not in seen:
                seen.add(key)
                merged.append(ref)
    return merged


def format_documents(documents: list[SourceDocument]) -> str:
    """Render documents for a prompt, each wrapped in a tag carrying its id."""
    blocks: list[str] = []
    for document in documents:
        attributes = [f'id="{document.document_id}"', f'source="{document.source.value}"']
        if document.meeting_id:
            attributes.append(f'meeting_id="{document.meeting_id}"')
        if document.meeting_date:
            attributes.append(f'meeting_date="{document.meeting_date}"')
        blocks.append(
            f"<document {' '.join(attributes)}>\n{document.text}\n</document>"
        )
    return "\n\n".join(blocks)
