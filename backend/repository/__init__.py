from .store import StoreRepository
from .subscription_repository import SubscriptionRepository

# Compatibility alias for integrations that imported the old repository name.
DataStore = StoreRepository

__all__ = ["StoreRepository", "SubscriptionRepository", "DataStore"]
