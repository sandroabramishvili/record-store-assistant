"""Import every model here, so that `Base.metadata` knows all tables.

Alembic imports this package before comparing the models with the database.
A model that is not imported here is invisible to autogenerate.
"""

from app.models.catalog import InventoryItem, Record
from app.models.chat import ChatMessage, ChatSession
from app.models.collection import CollectionItem, WantlistItem
from app.models.knowledge import KnowledgeArticle
from app.models.reservation import Reservation
from app.models.search_log import SearchLog
from app.models.user import TasteProfile, User

__all__ = [
    'ChatMessage',
    'ChatSession',
    'CollectionItem',
    'InventoryItem',
    'KnowledgeArticle',
    'Record',
    'Reservation',
    'SearchLog',
    'TasteProfile',
    'User',
    'WantlistItem',
]
