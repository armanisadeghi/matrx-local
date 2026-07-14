"""Local NER service package."""

from app.services.ner.service import NerService, get_ner_service

__all__ = ["NerService", "get_ner_service"]
