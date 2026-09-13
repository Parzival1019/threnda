"""Local document extraction and mandatory candidate review."""

from threnda.app.services.documents.processing import process_document
from threnda.app.services.documents.review import accept_candidate, reject_candidate

__all__ = ["accept_candidate", "process_document", "reject_candidate"]
