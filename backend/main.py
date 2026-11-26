"""
CampBuddy API - Backend Foundation
This module initializes the FastAPI application, defines health/welcome endpoints,
CORS middleware, document upload (with extraction, chunking, and persistent FAISS indexing),
semantic search, and grounded Gemini AI chat.
"""

import os
from pathlib import Path
from pydantic import BaseModel, Field
from fastapi import FastAPI, File, UploadFile, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Import document processing, chunking, embedding, and vector store modules
try:
    from backend.document_processor import process_document, ALLOWED_EXTENSIONS
    from backend.chunking import create_document_chunks
    from backend.embeddings import generate_and_store_embeddings, get_embedding_store, MODEL_NAME
    from backend.vector_store import get_vector_store
    from backend.gemini_service import generate_grounded_answer, DEFAULT_GEMINI_MODEL, get_active_api_key
except ModuleNotFoundError:
    from document_processor import process_document, ALLOWED_EXTENSIONS
    from chunking import create_document_chunks
    from embeddings import generate_and_store_embeddings, get_embedding_store, MODEL_NAME
    from vector_store import get_vector_store
    from gemini_service import generate_grounded_answer, DEFAULT_GEMINI_MODEL, get_active_api_key

# Initialize the FastAPI application with custom title and metadata
app = FastAPI(
    title="CampBuddy API",
    description="Backend API for CampBuddy - an AI-powered campus information assistant.",
    version="1.1.0"
)

# Enable Cross-Origin Resource Sharing (CORS) for web and mobile frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Reference to the shared thread-safe vector store
vector_store = get_vector_store()

# Cosine similarity threshold for considering a retrieved chunk relevant
# Normalized inner product scores range from -1.0 to 1.0; unrelated queries typically score below 0.25
SIMILARITY_RELEVANCE_THRESHOLD = 0.25


class SearchRequest(BaseModel):
    """Schema for student query search requests."""
    query: str = Field(..., description="The student search query or question.")
    top_k: int = Field(default=3, ge=1, le=20, description="Number of relevant chunks to retrieve (1 to 20).")


class ChatRequest(BaseModel):
    """Schema for student chat requests."""
    question: str = Field(..., description="Student's question about campus documents.")
    top_k: int = Field(default=3, ge=1, le=10, description="Number of context chunks to retrieve for Gemini.")


@app.get("/api")
def api_info():
    """
    API info endpoint that provides a welcoming message and documentation link.
    """
    return {
        "message": "Welcome to CampBuddy Backend API!",
        "version": "1.1.0",
        "docs_url": "/docs"
    }


@app.get("/health")
def health_check():
    """
    Health check endpoint to monitor server status and index state.
    Useful for uptime monitoring, deployment checks, and UI dashboards.
    """
    active_key = get_active_api_key()
    return {
        "status": "healthy",
        "total_indexed_chunks": vector_store.total_chunks(),
        "indexed_documents": vector_store.list_indexed_files(),
        "embedding_model": MODEL_NAME,
        "gemini_model": DEFAULT_GEMINI_MODEL,
        "gemini_configured": bool(active_key and len(active_key) > 5)
    }


@app.get("/documents")
def list_documents():
    """
    List all campus documents currently indexed in the vector store.
    """
    files = vector_store.list_indexed_files()
    return {
        "total_documents": len(files),
        "documents": files,
        "total_chunks": vector_store.total_chunks()
    }


@app.post("/clear")
def clear_vector_index():
    """
    Reset and clear all indexed document chunks from both memory and disk.
    """
    vector_store.clear(delete_disk=True)
    get_embedding_store().clear()
    return {
        "status": "success",
        "message": "All vector index data and document metadata have been cleared."
    }


@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    Upload, extract text, chunk, embed, and index a campus document (PDF, DOCX, or TXT).

    The file is processed in memory and persistently indexed into FAISS.
    Supported extensions: .pdf, .docx, .txt

    Returns:
        JSON response with filename, file_type, status, extracted_text,
        page-wise breakdown for PDFs, generated text chunks, and embedding summary.
    """
    # 1. Validate that a filename was provided
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required."
        )

    # 2. Extract and validate file extension
    _, ext = os.path.splitext(file.filename)
    ext_lower = ext.lower()

    if ext_lower not in ALLOWED_EXTENSIONS:
        allowed_list = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{ext}'. Supported formats are: {allowed_list}"
        )

    # 3. Read uploaded file content directly into memory
    try:
        file_bytes = await file.read()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read uploaded file: {str(e)}"
        )

    # 4. Extract text using the document processor module (includes tables for DOCX & OCR fallback for PDF)
    result = process_document(filename=file.filename, file_bytes=file_bytes)

    # 5. If extraction was successful, generate text chunks with preserved metadata
    if result.get("status") == "success":
        chunks = create_document_chunks(
            filename=result["filename"],
            file_type=result["file_type"],
            extracted_text=result["extracted_text"],
            pages=result.get("pages")
        )
        result["chunks"] = chunks
        result["total_chunks"] = len(chunks)

        # 6. Generate vector embeddings and index in FAISS vector store with auto-save
        embedding_summary = generate_and_store_embeddings(chunks)
        result["embedding_info"] = embedding_summary
    else:
        result["chunks"] = []
        result["total_chunks"] = 0
        result["embedding_info"] = {
            "status": "empty" if result.get("status") == "empty" else "error",
            "model_name": MODEL_NAME,
            "embedding_dimension": 384,
            "total_embeddings": 0,
            "message": result.get("message", "Document processing was not successful.")
        }

    return result


@app.post("/search")
async def search_documents(request: SearchRequest):
    """
    Search indexed campus documents using FAISS semantic similarity search.

    Accepts a query and returns the top_k most relevant chunks along with
    their text, source filename, chunk ID, page number, and similarity score.
    """
    # 1. Validate query is not empty or just whitespace
    cleaned_query = request.query.strip()
    if not cleaned_query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query cannot be empty."
        )

    # 2. Check if the index has any documents
    if vector_store.total_chunks() == 0:
        return {
            "query": request.query,
            "top_k": request.top_k,
            "total_results": 0,
            "results": [],
            "message": "No documents have been indexed yet. Please upload documents via /upload first."
        }

    # 3. Perform similarity search in FAISS
    try:
        results = vector_store.search(query=cleaned_query, top_k=request.top_k)
        return {
            "query": request.query,
            "top_k": request.top_k,
            "total_results": len(results),
            "results": results,
            "message": f"Successfully retrieved {len(results)} matching chunks."
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}"
        )


@app.post("/chat")
async def chat_with_campus_assistant(request: ChatRequest):
    """
    Ask a question about campus documents and receive a grounded answer from Gemini.

    Workflow:
    1. Validates the incoming student question.
    2. Retrieves top relevant chunks from the FAISS vector store.
    3. Deduplicates identical chunks (e.g. from repeat uploads or overlapping slices).
    4. Evaluates semantic relevance to avoid guessing on unrelated queries.
    5. Sends the question and grounded context to Google Gemini.
    6. Returns the AI answer alongside transparent, deduplicated source citations.
    """
    # 1. Validate question input
    cleaned_question = request.question.strip()
    if not cleaned_question:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question cannot be empty."
        )

    # 2. Gracefully handle empty FAISS index
    if vector_store.total_chunks() == 0:
        return {
            "question": request.question,
            "answer": "No campus documents have been uploaded or indexed yet. Please upload relevant documents via /upload before asking questions.",
            "sources": [],
            "status": "empty_index"
        }

    # 3. Retrieve relevant chunks using FAISS
    try:
        retrieved_chunks = vector_store.search(query=cleaned_question, top_k=request.top_k)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Vector search failed: {str(e)}"
        )

    # 4. Deduplicate retrieved chunks by chunk_id or text key
    seen_chunk_ids = set()
    unique_chunks = []
    for chunk in retrieved_chunks:
        cid = chunk.get("chunk_id")
        key = cid if cid else (chunk.get("filename"), chunk.get("page_number"), chunk.get("text", "")[:50])
        if key not in seen_chunk_ids:
            seen_chunk_ids.add(key)
            unique_chunks.append(chunk)

    # 5. Relevance evaluation (No-guess mechanism)
    # Filter out chunks that do not meet the minimum semantic similarity threshold
    relevant_chunks = [
        c for c in unique_chunks
        if c.get("similarity_score", 0.0) >= SIMILARITY_RELEVANCE_THRESHOLD
    ]

    # If no chunks meet the relevance threshold, return a direct no-guess response
    if not relevant_chunks:
        return {
            "question": request.question,
            "answer": "The uploaded campus documents do not provide enough information to answer this question.",
            "sources": [],
            "status": "no_relevant_context"
        }

    # Format unique source references for transparency and citations
    sources = [
        {
            "filename": chunk.get("filename"),
            "chunk_id": chunk.get("chunk_id"),
            "page_number": chunk.get("page_number"),
            "similarity_score": chunk.get("similarity_score")
        }
        for chunk in relevant_chunks
    ]

    # 6. Call Gemini to generate grounded response
    try:
        answer = generate_grounded_answer(
            question=cleaned_question,
            chunks=relevant_chunks,
            model_name=DEFAULT_GEMINI_MODEL
        )
        return {
            "question": request.question,
            "answer": answer,
            "sources": sources,
            "status": "success"
        }
    except Exception as e:
        # Sanitize error message to ensure API key is never leaked in response
        raw_error = str(e)
        active_key = get_active_api_key()
        if active_key and active_key in raw_error:
            raw_error = raw_error.replace(active_key, "[REDACTED]")

        return {
            "question": request.question,
            "answer": f"Unable to generate response from Gemini at this time. Details: {raw_error}",
            "sources": sources,
            "status": "error"
        }


# Mount frontend single page application if the directory exists
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

