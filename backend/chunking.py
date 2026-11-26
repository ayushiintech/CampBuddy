"""
Text Chunking Module for CampBuddy
Divides extracted document text into manageable chunks with overlap,
preserving sentence boundaries, source filenames, file types, and page numbers.
"""

import re

# Default chunking parameters
DEFAULT_CHUNK_SIZE = 500   # Approximate words per chunk
DEFAULT_OVERLAP = 100      # Approximate words of overlap between consecutive chunks


def split_into_sentences(text: str, max_sentence_words: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """
    Split text into sentences while respecting sentence boundary punctuation (. ! ?).
    If a sentence is unusually long (exceeds max_sentence_words), it splits it
    into smaller word pieces to prevent oversized chunks.

    Args:
        text (str): Input text string.
        max_sentence_words (int): Maximum words allowed for a single sentence piece.

    Returns:
        list[str]: List of individual sentences/sub-sentences.
    """
    text = text.strip()
    if not text:
        return []

    # Split on sentence-ending punctuation followed by whitespace, or double newlines
    raw_sentences = re.split(r'(?<=[.!?])\s+|\n\n+', text)
    sentences = []

    for s in raw_sentences:
        clean_sentence = s.strip()
        if not clean_sentence:
            continue

        words = clean_sentence.split()
        # If an individual sentence is longer than chunk size, divide it into word slices
        if len(words) > max_sentence_words:
            for i in range(0, len(words), max_sentence_words):
                sentences.append(" ".join(words[i:i + max_sentence_words]))
        else:
            sentences.append(clean_sentence)

    return sentences


def split_text_into_chunks(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP
) -> list[str]:
    """
    Divide a continuous string of text into chunks of approximately `chunk_size` words,
    with approximately `overlap` words shared between consecutive chunks.
    Avoids breaking sentences in the middle whenever possible.

    Args:
        text (str): The raw text to split.
        chunk_size (int): Target word count per chunk.
        overlap (int): Number of overlapping words between consecutive chunks.

    Returns:
        list[str]: List of chunk text strings.
    """
    text = text.strip()
    if not text:
        return []

    words = text.split()
    # If the entire text is smaller than or equal to the target chunk size, return as single chunk
    if len(words) <= chunk_size:
        return [text]

    # Split text into sentences so we preserve natural grammar boundaries
    sentences = split_into_sentences(text, max_sentence_words=chunk_size)
    if not sentences:
        return [text]

    chunks = []
    start_idx = 0
    total_sentences = len(sentences)

    while start_idx < total_sentences:
        current_chunk_sentences = []
        current_word_count = 0
        end_idx = start_idx

        # 1. Accumulate sentences until reaching chunk_size words
        while end_idx < total_sentences:
            sentence = sentences[end_idx]
            sentence_words = len(sentence.split())

            # Stop if adding this sentence exceeds chunk_size (and chunk is not empty)
            if current_chunk_sentences and (current_word_count + sentence_words > chunk_size):
                break

            current_chunk_sentences.append(sentence)
            current_word_count += sentence_words
            end_idx += 1

        # 2. Join the collected sentences into a chunk
        chunk_content = " ".join(current_chunk_sentences).strip()
        if chunk_content:
            chunks.append(chunk_content)

        # If we have reached the end of all sentences, finish
        if end_idx >= total_sentences:
            break

        # 3. Calculate overlap for the next chunk:
        # Step backwards from end_idx to find the starting sentence that provides ~overlap words
        overlap_words = 0
        next_start_idx = end_idx

        for j in range(end_idx - 1, start_idx, -1):
            next_start_idx = j
            overlap_words += len(sentences[j].split())
            if overlap_words >= overlap:
                break

        # Ensure forward progress to prevent infinite loops
        if next_start_idx <= start_idx:
            next_start_idx = start_idx + 1

        start_idx = next_start_idx

    return chunks


def create_document_chunks(
    filename: str,
    file_type: str,
    extracted_text: str,
    pages: list[dict] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP
) -> list[dict]:
    """
    Create structured chunks with rich metadata from extracted document content.

    For PDFs:
        Processes page-by-page to guarantee that each chunk is strictly tied
        to its specific page number for accurate future citations.

    For DOCX and TXT:
        Processes the entire document text with page_number set to None.

    Args:
        filename (str): Name of the source file.
        file_type (str): Extension/type ('pdf', 'docx', 'txt').
        extracted_text (str): Combined text of the document.
        pages (list[dict], optional): Page-wise text list for PDFs.
        chunk_size (int): Desired words per chunk.
        overlap (int): Overlapping words between chunks.

    Returns:
        list[dict]: List of chunk objects with unique IDs and metadata.
    """
    chunks = []
    global_chunk_index = 0

    # PDF Processing: Maintain strict page boundaries
    if file_type.lower() == "pdf" and pages:
        for page_data in pages:
            page_number = page_data.get("page_number")
            page_text = page_data.get("text", "").strip()

            # Skip pages with no text content
            if not page_text:
                continue

            page_chunks = split_text_into_chunks(
                text=page_text,
                chunk_size=chunk_size,
                overlap=overlap
            )

            for page_chunk_idx, chunk_text in enumerate(page_chunks):
                global_chunk_index += 1
                chunk_id = f"{filename}_p{page_number}_c{page_chunk_idx + 1}"
                word_count = len(chunk_text.split())
                char_count = len(chunk_text)

                chunk_item = {
                    "chunk_id": chunk_id,
                    "text": chunk_text,
                    "filename": filename,
                    "file_type": file_type,
                    "page_number": page_number,
                    "metadata": {
                        "filename": filename,
                        "file_type": file_type,
                        "page_number": page_number,
                        "chunk_index": global_chunk_index,
                        "page_chunk_index": page_chunk_idx + 1,
                        "word_count": word_count,
                        "character_count": char_count
                    }
                }
                chunks.append(chunk_item)

    # DOCX and TXT Processing: Process continuous text
    else:
        if not extracted_text or not extracted_text.strip():
            return []

        text_chunks = split_text_into_chunks(
            text=extracted_text,
            chunk_size=chunk_size,
            overlap=overlap
        )

        for chunk_idx, chunk_text in enumerate(text_chunks):
            global_chunk_index += 1
            chunk_id = f"{filename}_c{chunk_idx + 1}"
            word_count = len(chunk_text.split())
            char_count = len(chunk_text)

            chunk_item = {
                "chunk_id": chunk_id,
                "text": chunk_text,
                "filename": filename,
                "file_type": file_type,
                "page_number": None,
                "metadata": {
                    "filename": filename,
                    "file_type": file_type,
                    "page_number": None,
                    "chunk_index": global_chunk_index,
                    "word_count": word_count,
                    "character_count": char_count
                }
            }
            chunks.append(chunk_item)

    return chunks
