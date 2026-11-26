"""Local CampBuddy server with document excerpts and no external model calls."""

import json
import os
from pathlib import Path
import re
import sqlite3
from contextlib import closing

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .chunking import create_document_chunks, split_into_sentences
from .document_processor import process_document

app = FastAPI(title="CampBuddy local preview")
frontend = Path(__file__).resolve().parent.parent / "frontend"
database = Path(os.getenv("CAMPBUDDY_PREVIEW_DB", Path(__file__).resolve().parent / "data" / "local-preview.sqlite3"))
database.parent.mkdir(parents=True, exist_ok=True)
with closing(sqlite3.connect(database)) as conn, conn:
    conn.execute("CREATE TABLE IF NOT EXISTS documents (filename TEXT PRIMARY KEY, chunks TEXT NOT NULL)")

stop_words = set("a an and are at be by can could do does for from how i in is it me of on or our tell the to was what when where which who will with you your campus time".split())


class Search(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class Question(BaseModel):
    question: str
    top_k: int = Field(default=3, ge=1, le=10)


def words(text):
    tokens = set(re.findall(r"[^\W_]+", text.casefold())) - stop_words
    return {word.removesuffix("s") if len(word) > 4 else word for word in tokens}


def documents():
    with closing(sqlite3.connect(database)) as conn:
        return [(name, json.loads(chunks)) for name, chunks in conn.execute("SELECT filename, chunks FROM documents ORDER BY filename")]


def match_passages(query, entries, limit):
    tokens = words(query)
    if not tokens:
        return []
    results = []
    for _, chunks in entries:
        for chunk in chunks:
            overlap = len(words(chunk["text"]) & tokens)
            if overlap:
                results.append({**chunk, "similarity_score": round(overlap / len(tokens), 3)})
    return sorted(results, key=lambda chunk: chunk["similarity_score"], reverse=True)[:limit]


@app.get("/health")
def health():
    data = library()
    return {"status": "healthy", "mode": "local_preview", "gemini_configured": False,
            "indexed_documents": data["documents"], "total_indexed_chunks": data["total_chunks"]}


@app.get("/documents")
def library():
    entries = documents()
    return {"documents": [name for name, _ in entries], "total_chunks": sum(len(chunks) for _, chunks in entries)}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    contents = await file.read(20 * 1024 * 1024 + 1)
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(413, "Local preview uploads are limited to 20 MB.")
    result = process_document(file.filename or "", contents)
    if result["status"] != "success":
        raise HTTPException(400, result["message"])
    chunks = create_document_chunks(
        result["filename"], result["file_type"], result["extracted_text"], result.get("pages")
    )
    if not chunks:
        raise HTTPException(400, "No readable passages were found in this document.")
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO documents VALUES (?, ?)", (result["filename"], json.dumps(chunks)))
    return {"status": "success", "total_chunks": len(chunks), "embedding_info": {"status": "success"}}


@app.post("/search")
def search(request: Search):
    return {"results": match_passages(request.query, documents(), request.top_k)}


notice_heading = re.compile(r"(?im)(?=^\s*notice \d+\s*:)")
schedule_label = re.compile(r"(?im)^[^\n:]{0,60}\b(?:deadline|closes|date|window)\s*:[^\n]+")
time_question = re.compile(r"\b(?:when|deadline|due|what (?:date|time)|closes?|corrections?)\b", re.I)


def answer_excerpt(text, query, query_words):
    sections = [section.strip() for section in notice_heading.split(text) if section.strip()]
    section = max(sections, key=lambda item: len(words(item) & query_words), default=text)
    sentences = split_into_sentences(section)
    excerpt = max(sentences, key=lambda sentence: len(words(sentence) & query_words), default="")
    if time_question.search(query):
        dates = [line.strip() for line in schedule_label.findall(section) if line.strip() not in excerpt]
        if dates:
            excerpt = "\n".join(dates) + "\n\n" + excerpt
    return excerpt.strip()


@app.post("/chat")
def chat(request: Question):
    entries = documents()
    if not entries:
        return {"answer": "Upload a campus document to start asking questions.", "sources": [], "status": "empty_index"}
    query_words = words(request.question)
    anchors = query_words - {"close", "open", "start", "end", "due"}
    matches = [
        passage for passage in match_passages(request.question, entries, request.top_k)
        if not anchors or words(passage["text"]) & anchors
    ]
    if not matches:
        return {"answer": "I couldn’t find a matching passage in your uploaded documents.", "sources": [], "status": "no_relevant_context"}

    excerpts = []
    sources = []
    for match in matches:
        excerpt = answer_excerpt(match["text"], request.question, query_words)
        if excerpt and excerpt not in excerpts:
            excerpts.append(excerpt)
            sources.append({key: match.get(key) for key in ("filename", "page_number", "similarity_score")})
    return {
        "answer": "Matching excerpts from your documents:\n\n" + "\n\n".join(excerpts),
        "sources": sources,
        "status": "success",
    }


@app.post("/clear")
def clear():
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("DELETE FROM documents")
    return {"status": "success"}


@app.middleware("http")
async def disable_preview_cache(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
