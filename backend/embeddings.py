"""
Text Embeddings Module for CampBuddy
Generates vector representations of document chunks using Sentence Transformers (all-MiniLM-L6-v2)
and maintains an in-memory store for downstream FAISS vector indexing.
"""

import numpy as np

# Embedding Model Configuration
MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384  # Standard vector dimension produced by all-MiniLM-L6-v2

# Cached singleton model instance to ensure the model is loaded only once in memory
_model_instance = None


def get_embedding_model(model_name: str = MODEL_NAME):
    """
    Retrieve or initialize the SentenceTransformer model instance (Singleton pattern).
    Loading the model once saves memory and eliminates repeat initialization latency.

    Args:
        model_name (str): Hugging Face model identifier.

    Returns:
        SentenceTransformer: Loaded model instance.
    """
    global _model_instance
    if _model_instance is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "The 'sentence-transformers' library is required for generating embeddings. "
                "Please install it with: pip install sentence-transformers"
            )
        _model_instance = SentenceTransformer(model_name)
    return _model_instance


def generate_embeddings(
    texts: list[str],
    batch_size: int = 32,
    model_name: str = MODEL_NAME
) -> np.ndarray:
    """
    Convert a list of text strings into normalized numerical embeddings using batch encoding.

    Args:
        texts (list[str]): List of chunk texts to encode.
        batch_size (int): Number of texts processed simultaneously for performance.
        model_name (str): Name of the embedding model.

    Returns:
        np.ndarray: 2D numpy array of shape (len(texts), EMBEDDING_DIMENSION) with dtype float32,
                    ready for FAISS index ingestion.
    """
    if not texts:
        return np.empty((0, EMBEDDING_DIMENSION), dtype=np.float32)

    model = get_embedding_model(model_name)

    # Encode texts into normalized vectors
    # normalize_embeddings=True makes dot product equivalent to cosine similarity
    raw_embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True
    )

    # Ensure float32 format as required by FAISS
    return np.asarray(raw_embeddings, dtype=np.float32)


class InMemoryEmbeddingStore:
    """
    In-memory registry storing document chunks and their corresponding embeddings.
    Keeps all data readily available for FAISS similarity search in subsequent steps.
    """

    def __init__(self):
        self.chunks: list[dict] = []
        self.embeddings: np.ndarray | None = None

    def add_document_embeddings(self, chunks: list[dict], new_embeddings: np.ndarray):
        """
        Store new chunk metadata and append their numerical embeddings.

        Args:
            chunks (list[dict]): Chunk dictionaries with preserved metadata (page, filename, etc.).
            new_embeddings (np.ndarray): Corresponding 2D float32 embeddings array.
        """
        if not chunks or len(new_embeddings) == 0:
            return

        self.chunks.extend(chunks)

        if self.embeddings is None or len(self.embeddings) == 0:
            self.embeddings = new_embeddings
        else:
            self.embeddings = np.vstack([self.embeddings, new_embeddings])

    def get_all_chunks(self) -> list[dict]:
        """Return the complete list of stored chunk metadata."""
        return self.chunks

    def get_all_embeddings(self) -> np.ndarray | None:
        """Return the combined 2D numpy array of all embeddings."""
        return self.embeddings

    def get_total_embeddings(self) -> int:
        """Return the total number of stored vectors."""
        return len(self.chunks) if self.embeddings is not None else 0

    def clear(self):
        """Reset the store (useful during testing or cache invalidation)."""
        self.chunks = []
        self.embeddings = None


# Module-level singleton store instance accessible across backend components
embedding_store = InMemoryEmbeddingStore()


def get_embedding_store() -> InMemoryEmbeddingStore:
    """Provide external access to the active in-memory embedding store."""
    return embedding_store


def generate_and_store_embeddings(chunks: list[dict]) -> dict:
    """
    Generate embeddings for document chunks, store them alongside their metadata,
    index them in the FAISS vector store, and return a clean summary for API responses.

    Args:
        chunks (list[dict]): List of generated chunk dictionaries.

    Returns:
        dict: Summary of the embedding operation (status, counts, model name, dimension).
    """
    if not chunks:
        return {
            "status": "empty",
            "model_name": MODEL_NAME,
            "embedding_dimension": EMBEDDING_DIMENSION,
            "total_embeddings": 0,
            "message": "No chunks available to embed."
        }

    try:
        # Extract plain text from each chunk
        texts = [chunk["text"] for chunk in chunks]

        # Generate float32 embeddings in batches
        embeddings = generate_embeddings(texts)

        # Retain embeddings and metadata in memory
        embedding_store.add_document_embeddings(chunks=chunks, new_embeddings=embeddings)

        # Index chunks and embeddings into the FAISS vector store
        try:
            from backend.vector_store import get_vector_store
        except ModuleNotFoundError:
            from vector_store import get_vector_store

        store = get_vector_store()
        store.add_documents(chunks=chunks, embeddings=embeddings)

        return {
            "status": "success",
            "model_name": MODEL_NAME,
            "embedding_dimension": EMBEDDING_DIMENSION,
            "total_embeddings": int(len(embeddings)),
            "total_indexed_chunks": store.total_chunks(),
            "message": f"Successfully generated {len(embeddings)} embeddings and indexed them in FAISS."
        }

    except Exception as e:
        return {
            "status": "error",
            "model_name": MODEL_NAME,
            "embedding_dimension": EMBEDDING_DIMENSION,
            "total_embeddings": 0,
            "message": f"Failed to generate embeddings: {str(e)}"
        }
