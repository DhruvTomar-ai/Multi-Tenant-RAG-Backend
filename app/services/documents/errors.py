from __future__ import annotations


class UploadValidationError(Exception):
    """The uploaded file was rejected. The message is safe to show to the client."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class DuplicateDocumentError(Exception):
    """This team already has a document with identical bytes."""

    def __init__(self, document_id: int, document_status: str):
        super().__init__("This team already has a document with the same content")
        self.document_id = document_id
        self.document_status = document_status


class IngestionQueueError(Exception):
    """The document was saved but its task could not be sent to the broker."""


class EmbeddingRetryConflict(Exception):
    """The document's embeddings are not in a state that can be retried."""

    def __init__(self, embedding_status: str):
        super().__init__(f"Embeddings cannot be retried while their status is {embedding_status}")
        self.embedding_status = embedding_status


class PermanentIngestionError(Exception):
    """Processing can never succeed for this file, so retrying would be pointless.

    The message is stored on the job and shown through the API, so it must not
    contain file paths or other internal details.
    """
