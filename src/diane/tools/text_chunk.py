"""Built-in tool: split text into overlapping chunks for RAG pipelines."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "text_chunk",
    "description": (
        "Split text into overlapping chunks by character count or word count. "
        "Useful for RAG pipelines where large documents need to be broken into "
        "smaller pieces for embedding or LLM context windows."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to split into chunks.",
            },
            "chunk_size": {
                "type": "integer",
                "description": "Maximum size of each chunk.",
                "default": 1000,
            },
            "overlap": {
                "type": "integer",
                "description": "Number of characters (or words) to overlap between chunks.",
                "default": 100,
            },
            "method": {
                "type": "string",
                "enum": ["characters", "words"],
                "description": "Chunking method: 'characters' counts characters, 'words' counts words.",
                "default": "characters",
            },
        },
        "required": ["text"],
    },
    "examples": [
        {
            "input": {"text": "The quick brown fox jumps over the lazy dog.", "chunk_size": 20, "overlap": 5},
            "output": {
                "chunks": [
                    {"chunk": "The quick brown fox ", "index": 0, "start_char": 0, "end_char": 20},
                ],
                "count": 3,
            },
        },
    ],
    "category": "document",
    "returns": "dict with chunks (list of {chunk, index, start_char, end_char}) and count",
}


async def text_chunk(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 100,
    method: str = "characters",
    **_kwargs,
) -> dict:
    if not text:
        return {"chunks": [], "count": 0}

    if method == "words":
        return _chunk_by_words(text, chunk_size, overlap)
    return _chunk_by_chars(text, chunk_size, overlap)


def _chunk_by_chars(text: str, size: int, overlap: int) -> dict:
    chunks = []
    step = max(size - overlap, 1)
    idx = 0
    pos = 0
    while pos < len(text):
        end = min(pos + size, len(text))
        chunks.append({
            "chunk": text[pos:end],
            "index": idx,
            "start_char": pos,
            "end_char": end,
        })
        idx += 1
        pos += step
        if end == len(text):
            break
    return {"chunks": chunks, "count": len(chunks)}


def _chunk_by_words(text: str, size: int, overlap: int) -> dict:
    words = text.split()
    if not words:
        return {"chunks": [], "count": 0}

    chunks = []
    step = max(size - overlap, 1)
    idx = 0
    pos = 0
    while pos < len(words):
        end = min(pos + size, len(words))
        chunk_words = words[pos:end]
        chunk_text = " ".join(chunk_words)

        start_char = text.index(chunk_words[0], _word_start_hint(text, words, pos))
        end_char = start_char + len(chunk_text)

        chunks.append({
            "chunk": chunk_text,
            "index": idx,
            "start_char": start_char,
            "end_char": end_char,
        })
        idx += 1
        pos += step
        if end == len(words):
            break
    return {"chunks": chunks, "count": len(chunks)}


def _word_start_hint(text: str, words: list[str], word_idx: int) -> int:
    """Approximate char offset for the start of word_idx (used as search hint)."""
    offset = 0
    for i in range(min(word_idx, len(words))):
        found = text.find(words[i], offset)
        if found == -1:
            break
        offset = found + len(words[i])
    return max(offset - len(words[min(word_idx, len(words) - 1)]), 0) if word_idx > 0 else 0
