# CampBuddy 🎓🤖

**CampBuddy** is an AI-powered campus information assistant backend built on a high-precision Retrieval-Augmented Generation (RAG) architecture. It allows university students and staff to query campus policy handbooks, course materials, administrative guidelines, schedules, and fee structures with document-grounded answers and transparent source citations.

---

## 🏗️ Architecture & Pipeline

```text
Student Document (PDF / DOCX / TXT)
               │
               ▼
   [ Document Processor ] ─── Multi-format in-memory extraction (PDF pages, DOCX tables)
               │
               ▼
     [ Sentence Chunker ] ─── Page-aware, sliding-window chunking (~500 words, ~100 overlap)
               │
               ▼
    [ Embeddings Generator ] ── Sentence-Transformers (all-MiniLM-L6-v2, 384-d normalized)
               │
               ▼
      [ FAISS Vector Store ] ── Thread-safe IndexFlatIP with persistent disk storage
               │
               ▼
  Student Query -> [ Semantic Search & Relevance Check ] (Relevance threshold >= 0.25)
               │
               ▼
   [ Grounded Gemini Service ] ── Prompt-shielded, grounded inference (configurable Gemini model)
               │
               ▼
   AI Response + Deduplicated Source Citations (Filename, Page Number, Similarity Score)
```

---

## 🚀 Quick Start

### 1. Install Dependencies
Ensure Python 3.10+ is installed, then install requirements:

```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `backend/.env` and insert your Gemini API Key:

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env`:
```env
GEMINI_API_KEY=your_actual_gemini_api_key_here
```
*(Get a free Gemini API key from [Google AI Studio](https://aistudio.google.com/)).*

### 3. Run the Development Server
From the project root:

```bash
uvicorn backend.main:app --reload --port 8000
```
or from the `backend/` directory:
```bash
uvicorn main:app --reload --port 8000
```

Access the interactive frontend web application at:
🌐 **http://localhost:8000**

Access the interactive Swagger API documentation at:
📌 **http://localhost:8000/docs**

---

## Run locally without a Gemini key

On Windows, extract the ZIP and double-click `Start-CampBuddy.bat`. On macOS or Linux, open a terminal in the extracted CampBuddy folder and run `python3 start_campbuddy.py`. Python 3.10 or newer is required. The launcher installs missing local dependencies on first use, uses port 8001 to keep browser history and reminders available across restarts, and opens the browser when the server is ready. Keep the terminal window open while using CampBuddy.

Upload a campus document and ask a question about its contents. This mode stores a separate local index, uses keyword matching, and returns source excerpts. Use the standard server above for Gemini-generated answers.

## Submission demo

1. Start the local launcher and upload `examples/CampBuddy_Sample_Campus_Notices_TEST_DATA.pdf`.
2. Ask “When is the Sample Merit Scholarship application due?” The PDF lists 5 December 2026 at 5:00 PM. Expand **Sources** to see the document and page.
3. Open **Smart Campus Alarm → Find dates in campus notices** and paste “Form submission deadline: 15 December 2026, 4:00 PM”. Select the date, confirm the prefilled time, and save a reminder. Deadline suggestions show upcoming dates relative to the system clock. For a demo after those dates, paste a future date instead.
4. Download the reminder and import it into a calendar that supports imported alarms.

The sample notices are fictional. A live Gemini key is supplied locally in `backend/.env` and is excluded from this archive.

## Using the frontend

- **Campus assistant** accepts typed questions or suggested prompts. Answers include source filenames, page numbers when available, and similarity scores. Recent chats stay in the current browser or app. Use **New chat** to start a separate conversation and reopen previous chats from the sidebar.
- **Document library** accepts PDF, DOCX, and TXT from the file picker or by drag and drop. Clearing the index requires confirmation and affects the connected backend.
- **Semantic search** shows matching document passages independently of Gemini.
- **Notice Detective** compares pasted notices against indexed document passages and shows the source and page when available.
- **Smart Campus Alarm** finds dates in pasted notices and uploaded documents. Confirm the suggested year for dates like “15 December,” choose a date, time, and reminder lead time, and save it. Download the calendar reminder and import it into a calendar that supports imported alarms to receive a notification.
- **Knowledge Gap Dashboard** records questions that returned an empty index or lacked relevant context. Retry a question from the dashboard after adding relevant documents.
- **Settings** lets you switch the backend URL and set the number of context passages per answer. The web frontend defaults to the same origin as the page.
- **Android** bundles the same interface. The emulator defaults to `http://10.0.2.2:8000`. On a physical device, enter a backend URL reachable from that device. For local testing over a trusted network, run Uvicorn with `--host 0.0.0.0`.

## 📡 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | CampBuddy web frontend |
| `GET` | `/api` | API status and welcome message |
| `GET` | `/health` | Server health, total indexed chunks, indexed documents list, and Gemini config status |
| `GET` | `/documents` | List of all indexed documents and chunk statistics |
| `POST` | `/upload` | Upload `.pdf`, `.docx`, or `.txt` document to extract, chunk, embed, and index into FAISS |
| `POST` | `/search` | Semantic similarity search against indexed document chunks |
| `POST` | `/chat` | Ask questions and receive grounded answers from Gemini with citations |
| `POST` | `/clear` | Reset and clear the in-memory and persistent FAISS vector store |

---

## 🧪 Example API Requests

### 1. Document Upload
```bash
curl -X POST "http://localhost:8000/upload" \
  -F "file=@campus_handbook.pdf"
```

### 2. Semantic Search
```bash
curl -X POST "http://localhost:8000/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the deadline for tuition fees?", "top_k": 3}'
```

### 3. Grounded Chat
```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"question": "How do I apply for library access as a freshman?", "top_k": 3}'
```

---

## 🔒 Security & Guardrails

- **Retrieval threshold:** Queries that do not match campus documents above the semantic similarity threshold (`SIMILARITY_RELEVANCE_THRESHOLD = 0.25`) return an honest notification rather than guessing.
- **Untrusted Context Isolation:** The system prompt treats all document text as untrusted data to defend against prompt injection inside uploaded documents.
- **API Key Redaction:** Any upstream API exception sanitizes the error response to guarantee API keys are never exposed in logs or user-facing responses.
- **Thread-Safety & Persistence:** Index operations are protected by mutex locks and auto-saved to disk under `backend/data/`.
