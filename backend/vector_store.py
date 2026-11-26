"""
Vector Store Module for CampBuddy
Manages FAISS index creation, vector storage, similarity search, and persistence
for campus document chunks.
Maintains a thread-safe mapping between FAISS vector positions and rich chunk metadata.
"""

import json
import logging
from pathlib import Path
import threading
import numpy as np

logger = logging.getLogger("campbuddy.vector_store")

# Optional import of faiss with graceful fallback to NumPy
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    faiss = None
    FAISS_AVAILABLE = False


class NumpyIndexFlatIP:
    """NumPy-based drop-in replacement for faiss.IndexFlatIP."""

    def __init__(self, dimension: int):
        self.dimension = dimension
        self.vectors = np.empty((0, dimension), dtype=np.float32)

    @property
    def ntotal(self) -> int:
        return len(self.vectors)

    def add(self, x: np.ndarray):
        x = np.asarray(x, dtype=np.float32)
        if len(self.vectors) == 0:
            self.vectors = x.copy()
        else:
            self.vectors = np.vstack([self.vectors, x])

    def search(self, query: np.ndarray, k: int):
        query = np.asarray(query, dtype=np.float32)
        if self.ntotal == 0:
            return np.empty((1, 0), dtype=np.float32), np.empty((1, 0), dtype=np.int64)
        scores = np.dot(query, self.vectors.T)
        k = min(k, self.ntotal)
        top_k_indices = np.argsort(-scores[0])[:k]
        top_k_scores = scores[0][top_k_indices]
        return np.array([top_k_scores], dtype=np.float32), np.array([top_k_indices], dtype=np.int64)

    def reset(self):
        self.vectors = np.empty((0, self.dimension), dtype=np.float32)

# Import embedding components
try:
    from backend.embeddings import generate_embeddings, EMBEDDING_DIMENSION, MODEL_NAME
except ModuleNotFoundError:
    from embeddings import generate_embeddings, EMBEDDING_DIMENSION, MODEL_NAME

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"


class VectorStore:
    """
    Thread-safe vector store powered by FAISS with disk persistence.
    Uses Inner Product (IndexFlatIP) on normalized embeddings to compute cosine similarity.
    """

    def __init__(self, dimension: int = EMBEDDING_DIMENSION, data_dir: Path | str = DEFAULT_DATA_DIR):
        self.dimension = dimension
        self.data_dir = Path(data_dir)
        self.index = None
        self.chunk_metadata: list[dict] = []
        self._lock = threading.Lock()

        self._init_index()
        # Try to auto-load previously persisted index if available
        self.load_from_disk()

    def _init_index(self):
        """Initialize the vector index (FAISS or NumPy fallback)."""
        if FAISS_AVAILABLE:
            self.index = faiss.IndexFlatIP(self.dimension)
        else:
            self.index = NumpyIndexFlatIP(self.dimension)

    def add_documents(self, chunks: list[dict], embeddings: np.ndarray, auto_save: bool = True) -> int:
        """
        Add document chunks and their corresponding embeddings into the FAISS index.
        Thread-safe: protected by an internal re-entrant/mutex lock.
        If chunks from the same filename already exist, they can be replaced.

        Args:
            chunks (list[dict]): List of chunk dictionaries containing metadata.
            embeddings (np.ndarray): 2D float32 numpy array of shape (N, dimension).
            auto_save (bool): Automatically persist updated index to disk.

        Returns:
            int: Number of chunks added.
        """
        if not chunks or embeddings is None or len(embeddings) == 0:
            return 0

        with self._lock:
            if self.index is None:
                self._init_index()

            # Ensure embeddings are a 2D float32 numpy array
            embeddings_np = np.asarray(embeddings, dtype=np.float32)

            # 1. Add vectors to the FAISS index
            self.index.add(embeddings_np)

            # 2. Append chunk metadata in the exact corresponding sequential order
            for chunk in chunks:
                self.chunk_metadata.append({
                    "chunk_id": chunk.get("chunk_id"),
                    "filename": chunk.get("filename"),
                    "file_type": chunk.get("file_type"),
                    "page_number": chunk.get("page_number"),
                    "text": chunk.get("text"),
                    "metadata": chunk.get("metadata", {})
                })

            if auto_save:
                try:
                    self._save_to_disk_unlocked()
                except Exception as e:
                    logger.warning(f"Failed to auto-save index to disk: {e}")

            return len(chunks)

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """
        Perform semantic similarity search on the indexed campus document chunks.

        Args:
            query (str): The search query or question from the student.
            top_k (int): Number of top matching chunks to retrieve (default: 3).

        Returns:
            list[dict]: Top matching chunk dictionaries with similarity scores and metadata.
        """
        if not query or not query.strip():
            return []

        with self._lock:
            # Return empty list if no documents have been indexed yet
            if self.index is None or self.index.ntotal == 0:
                return []

            # Clamp k to available indexed vectors
            k = max(1, min(top_k, self.index.ntotal))

            # 1. Encode query text using the Sentence Transformer model
            query_embedding = generate_embeddings([query.strip()])

            # 2. Search FAISS index for the top_k closest vectors
            scores, indices = self.index.search(query_embedding, k)

            # 3. Assemble results with metadata and similarity scores
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx == -1 or idx >= len(self.chunk_metadata):
                    continue
                chunk = self.chunk_metadata[idx]
                results.append({
                    "chunk_id": chunk["chunk_id"],
                    "filename": chunk["filename"],
                    "file_type": chunk["file_type"],
                    "page_number": chunk["page_number"],
                    "text": chunk["text"],
                    "similarity_score": round(float(score), 4),
                    "metadata": chunk.get("metadata", {})
                })

            return results

    def total_chunks(self) -> int:
        """Return the total number of indexed vectors/chunks."""
        with self._lock:
            if self.index is not None:
                return int(self.index.ntotal)
            return len(self.chunk_metadata)

    def list_indexed_files(self) -> list[str]:
        """Return a sorted list of unique filenames currently in the index."""
        with self._lock:
            filenames = {c.get("filename") for c in self.chunk_metadata if c.get("filename")}
            return sorted(filenames)

    def clear(self, delete_disk: bool = True):
        """Reset the vector index and metadata store in-memory and on disk."""
        with self._lock:
            self._init_index()
            self.chunk_metadata = []

            if delete_disk:
                index_path = self.data_dir / "campbuddy_faiss.index"
                vec_path = self.data_dir / "campbuddy_vectors.npy"
                meta_path = self.data_dir / "campbuddy_metadata.json"
                for p in (index_path, vec_path, meta_path):
                    if p.exists():
                        try:
                            p.unlink()
                        except Exception:
                            pass

    def _save_to_disk_unlocked(self, directory: Path | None = None) -> bool:
        """Internal helper to save to disk while lock is already acquired."""
        target_dir = directory or self.data_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        if self.index is None or self.index.ntotal == 0:
            return False

        meta_path = target_dir / "campbuddy_metadata.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(self.chunk_metadata, f, ensure_ascii=False, indent=2)

        if FAISS_AVAILABLE:
            index_path = target_dir / "campbuddy_faiss.index"
            faiss.write_index(self.index, str(index_path))
        else:
            vec_path = target_dir / "campbuddy_vectors.npy"
            np.save(vec_path, self.index.vectors)

        return True

    def save_to_disk(self, directory: Path | None = None) -> bool:
        """Save the vector index and chunk metadata to disk."""
        with self._lock:
            return self._save_to_disk_unlocked(directory)

    def load_from_disk(self, directory: Path | None = None) -> bool:
        """
        Load the vector index and chunk metadata from disk if present.
        Returns True if successfully loaded, False otherwise.
        """
        target_dir = directory or self.data_dir
        meta_path = target_dir / "campbuddy_metadata.json"
        index_path = target_dir / "campbuddy_faiss.index"
        vec_path = target_dir / "campbuddy_vectors.npy"

        if not meta_path.exists():
            return False

        if FAISS_AVAILABLE and not index_path.exists():
            return False
        if not FAISS_AVAILABLE and not vec_path.exists():
            return False

        with self._lock:
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    loaded_meta = json.load(f)

                if FAISS_AVAILABLE:
                    loaded_index = faiss.read_index(str(index_path))
                    if loaded_index.ntotal == len(loaded_meta):
                        self.index = loaded_index
                        self.chunk_metadata = loaded_meta
                        logger.info(f"Loaded {loaded_index.ntotal} chunks from {target_dir}")
                        return True
                else:
                    loaded_vectors = np.load(vec_path)
                    if len(loaded_vectors) == len(loaded_meta):
                        self.index = NumpyIndexFlatIP(self.dimension)
                        self.index.vectors = loaded_vectors
                        self.chunk_metadata = loaded_meta
                        logger.info(f"Loaded {self.index.ntotal} chunks from {target_dir}")
                        return True

                logger.warning("Index vector count and metadata length mismatch; skipping disk load.")
                return False
            except Exception as e:
                logger.warning(f"Failed to load vector store from disk: {e}")
                return False


# Singleton VectorStore instance across the backend application
vector_store = VectorStore()


def get_vector_store() -> VectorStore:
    """Provide external access to the active vector store instance."""
    return vector_store
