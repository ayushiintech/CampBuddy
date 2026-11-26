"""
Document Processor Module for CampBuddy
Handles robust text extraction from PDF, DOCX (including tables), and TXT files directly in memory.
Includes OCR fallback detection for scanned PDFs and graceful handling of missing libraries.
"""

import io
import logging
import os

logger = logging.getLogger("campbuddy.document_processor")

# Safe imports for document extraction libraries
try:
    import pymupdf
    PYMUPDF_AVAILABLE = True
except ImportError:
    pymupdf = None
    PYMUPDF_AVAILABLE = False

try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    docx = None
    DOCX_AVAILABLE = False

# Set of allowed extensions
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}


def extract_text_from_pdf(file_bytes: bytes) -> tuple[str, list[dict]]:
    """
    Extract text page-by-page from a PDF using PyMuPDF.
    If a page contains no digital text, attempts OCR fallback if Tesseract is configured.

    Args:
        file_bytes (bytes): The raw bytes of the PDF file.

    Returns:
        tuple[str, list[dict]]:
            - Combined full text from all pages.
            - List of page objects: [{'page_number': int, 'text': str, 'is_ocr': bool}],
              retaining page-wise information for source citations.
    """
    if not PYMUPDF_AVAILABLE:
        raise RuntimeError(
            "The 'pymupdf' package is required to process PDF files. "
            "Please install it using: pip install pymupdf"
        )

    pages = []
    text_parts = []

    # Open PDF document directly from memory buffer
    with pymupdf.open(stream=file_bytes, filetype="pdf") as doc:
        for page_index in range(len(doc)):
            page = doc[page_index]
            page_text = page.get_text().strip()
            is_ocr = False
            page_number = page_index + 1

            # If digital text extraction is empty, attempt OCR if available
            if not page_text:
                try:
                    pix = page.get_pixmap()
                    pdf_ocr_bytes = page.get_textpage_ocr().extractText()
                    if pdf_ocr_bytes and pdf_ocr_bytes.strip():
                        page_text = pdf_ocr_bytes.strip()
                        is_ocr = True
                except Exception as ocr_err:
                    logger.debug(f"OCR not available for PDF page {page_number}: {ocr_err}")

            pages.append({
                "page_number": page_number,
                "text": page_text,
                "is_ocr": is_ocr
            })

            if page_text:
                text_parts.append(page_text)

    full_text = "\n\n".join(text_parts)
    return full_text, pages


def extract_text_from_docx(file_bytes: bytes) -> str:
    """
    Extract paragraph and table text from a DOCX file using python-docx.
    University documents (schedules, grade criteria, fee breakdowns) frequently use tables.

    Args:
        file_bytes (bytes): The raw bytes of the DOCX file.

    Returns:
        str: Combined paragraph and table text.
    """
    if not DOCX_AVAILABLE:
        raise RuntimeError(
            "The 'python-docx' package is required to process DOCX files. "
            "Please install it using: pip install python-docx"
        )

    doc_stream = io.BytesIO(file_bytes)
    doc = docx.Document(doc_stream)

    extracted_sections = []

    # 1. Extract paragraphs
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    if paragraphs:
        extracted_sections.append("\n".join(paragraphs))

    # 2. Extract structured table content
    table_lines = []
    for table in doc.tables:
        for row in table.rows:
            # Extract and deduplicate adjacent merged cells in the row
            row_cells = []
            for cell in row.cells:
                clean_cell = cell.text.strip()
                if clean_cell and (not row_cells or row_cells[-1] != clean_cell):
                    row_cells.append(clean_cell)
            if row_cells:
                table_lines.append(" | ".join(row_cells))

    if table_lines:
        extracted_sections.append("\nTABLE DATA:\n" + "\n".join(table_lines))

    return "\n\n".join(extracted_sections)


def extract_text_from_txt(file_bytes: bytes) -> str:
    """
    Extract text from a TXT file, handling various encodings safely.

    Args:
        file_bytes (bytes): The raw bytes of the TXT file.

    Returns:
        str: Decoded string content.
    """
    # Try common encodings in order of likelihood
    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
    for encoding in encodings:
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue

    # Fallback with replacement character for any undecodable bytes
    return file_bytes.decode("utf-8", errors="replace")


def process_document(filename: str, file_bytes: bytes) -> dict:
    """
    Process an uploaded document, validate its format, and extract its text content.

    Args:
        filename (str): The original filename with extension.
        file_bytes (bytes): The file data in bytes.

    Returns:
        dict: Extraction report containing:
            - filename: Original name of the uploaded file.
            - file_type: Extension ('pdf', 'docx', 'txt').
            - status: 'success', 'empty', or 'error'.
            - extracted_text: Full extracted string content.
            - pages: Page-wise text list for PDFs (None for others).
            - total_pages: Total number of pages for PDFs (None for others).
            - character_count: Length of extracted text.
            - message: Descriptive status message.
    """
    _, ext = os.path.splitext(filename)
    ext_clean = ext.lower().lstrip(".")

    # Handle empty files (0 bytes)
    if not file_bytes or len(file_bytes) == 0:
        return {
            "filename": filename,
            "file_type": ext_clean,
            "status": "empty",
            "extracted_text": "",
            "pages": [] if ext_clean == "pdf" else None,
            "total_pages": 0 if ext_clean == "pdf" else None,
            "character_count": 0,
            "message": "The uploaded file is empty (0 bytes)."
        }

    try:
        pages = None
        total_pages = None
        extracted_text = ""

        if ext_clean == "pdf":
            extracted_text, pages = extract_text_from_pdf(file_bytes)
            total_pages = len(pages)
        elif ext_clean == "docx":
            extracted_text = extract_text_from_docx(file_bytes)
        elif ext_clean == "txt":
            extracted_text = extract_text_from_txt(file_bytes)
        else:
            return {
                "filename": filename,
                "file_type": ext_clean,
                "status": "error",
                "extracted_text": "",
                "pages": None,
                "total_pages": None,
                "character_count": 0,
                "message": f"Unsupported file extension: .{ext_clean}"
            }

        # Check if the document contained any readable text
        if not extracted_text.strip():
            return {
                "filename": filename,
                "file_type": ext_clean,
                "status": "empty",
                "extracted_text": "",
                "pages": pages,
                "total_pages": total_pages,
                "character_count": 0,
                "message": "The document was processed, but no extractable text was found (document may be scanned or empty)."
            }

        return {
            "filename": filename,
            "file_type": ext_clean,
            "status": "success",
            "extracted_text": extracted_text,
            "pages": pages,
            "total_pages": total_pages,
            "character_count": len(extracted_text),
            "message": "Text extracted successfully."
        }

    except Exception as e:
        return {
            "filename": filename,
            "file_type": ext_clean,
            "status": "error",
            "extracted_text": "",
            "pages": None,
            "total_pages": None,
            "character_count": 0,
            "message": f"Failed to extract text from document: {str(e)}"
        }
