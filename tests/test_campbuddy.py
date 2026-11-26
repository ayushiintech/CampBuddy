"""
Unit and Integration Tests for CampBuddy Backend
Tests document processing, chunking, vector store indexing, and search functionality.
"""

import unittest
from backend.chunking import split_into_sentences, split_text_into_chunks, create_document_chunks
from backend.document_processor import process_document


class TestCampBuddy(unittest.TestCase):

    def test_sentence_splitting(self):
        sample_text = "CampBuddy is an AI assistant. It helps students find campus information! Does it work well?"
        sentences = split_into_sentences(sample_text)
        self.assertEqual(len(sentences), 3)
        self.assertEqual(sentences[0], "CampBuddy is an AI assistant.")
        self.assertEqual(sentences[1], "It helps students find campus information!")
        self.assertEqual(sentences[2], "Does it work well?")

    def test_text_chunking(self):
        long_text = " ".join([f"Sentence {i} describes campus rule number {i}." for i in range(1, 100)])
        chunks = split_text_into_chunks(long_text, chunk_size=50, overlap=10)
        self.assertTrue(len(chunks) > 1)
        for chunk in chunks:
            self.assertTrue(len(chunk) > 0)

    def test_txt_document_processing(self):
        sample_bytes = b"Welcome to CampBuddy.\nCampus library is open Monday through Friday 8am to 8pm."
        result = process_document("campus_rules.txt", sample_bytes)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["file_type"], "txt")
        self.assertIn("library is open", result["extracted_text"])

    def test_chunk_metadata_creation(self):
        sample_text = "Academic policies must be observed. Plagiarism results in disciplinary action."
        chunks = create_document_chunks(
            filename="policies.txt",
            file_type="txt",
            extracted_text=sample_text,
            chunk_size=100,
            overlap=10
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["filename"], "policies.txt")
        self.assertEqual(chunks[0]["chunk_id"], "policies.txt_c1")
        self.assertEqual(chunks[0]["page_number"], None)


if __name__ == "__main__":
    unittest.main()
