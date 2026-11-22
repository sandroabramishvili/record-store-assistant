"""CustomerContext: who the customer is, as far as the store rules are concerned. Pure."""

from dataclasses import dataclass, field


@dataclass
class CustomerContext:
    profile: dict = field(default_factory=dict)          # favorite/disliked genres, budget_cap, min_condition
    collection: list[dict] = field(default_factory=list)  # [{record_id, artist_norm, title_norm}]
    wantlist_artists: list[str] = field(default_factory=list)

    @classmethod
    def from_state(cls, state: dict) -> 'CustomerContext':
        return cls(profile=state.get('profile') or {}, collection=state.get('collection') or [],
                   wantlist_artists=state.get('wantlist_artists') or [])

    @property
    def owned_record_ids(self) -> set[int]:
        return {c['record_id'] for c in self.collection if c['record_id'] is not None}

    @property
    def owned_keys(self) -> set[tuple[str, str]]:
        return {(c['artist_norm'], c['title_norm']) for c in self.collection}

    @property
    def collection_artists(self) -> set[str]:
        return {c['artist_norm'] for c in self.collection}
