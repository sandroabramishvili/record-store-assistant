"""Structured outputs (spec section 5): what the LLM must return, as Pydantic models.

The field descriptions are sent to the model as part of the JSON schema, so they are
instructions too. Validation runs on every response; an invalid one is retried
(app/ai/llm.py) and never reaches the graph.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.services.conditions import Condition

Intent = Literal['find_record', 'record_info', 'reserve', 'wantlist', 'collection_help', 'off_topic']
Format = Literal['LP', 'EP', '7"', '10"', '12"']


class SearchConstraints(BaseModel):
    artists: list[str] = Field(default_factory=list, description='Artists the customer explicitly mentioned, as written')
    # Not in the spec: without it "Do you have Nevermind?" loses the album name, so search
    # can't look for that album and the wantlist entry would have no title.
    titles: list[str] = Field(
        default_factory=list, description='Album or single titles the customer explicitly mentioned, as written')
    genres: list[str] = Field(
        default_factory=list,
        description='Genres or styles in lower case, e.g. jazz, post-punk, jazz-funk, hip-hop, electronic')
    similar_to: str | None = Field(
        default=None, description='"Something like X": the reference artist or album, e.g. "Kind of Blue"')
    decade: int | None = Field(default=None, ge=1900, le=2030, description='Start year of the decade, e.g. 1970 for "70s"')
    max_price: float | None = Field(default=None, gt=0, le=10000, description='Upper price limit in dollars, only if stated')
    min_condition: Condition | None = Field(
        default=None, description='Lowest acceptable Goldmine grade of the vinyl, only if stated (e.g. "at least VG+")')
    format: Format | None = None
    original_pressing_only: bool | None = Field(
        default=None, description='True if the customer wants an original pressing / "not a reissue"')
    include_owned: bool = Field(
        default=False, description='True only if the customer explicitly wants a record they already own (e.g. a better copy)')
    query_text: str = Field(
        description='Short free-text description of the music the customer is looking for, for similarity search')


class RequestAnalysis(BaseModel):
    intent: Intent
    constraints: SearchConstraints
    refers_to_previous: bool = Field(description='Does the request refer to records shown earlier in the chat?')
    reference_index: int | None = Field(
        default=None, ge=1, le=10,
        description='1-based position in the last numbered list shown, e.g. "the second one" → 2')


class AnswerDraft(BaseModel):
    message: str = Field(
        max_length=1500,
        description="Answer text in the customer's language. Never invent records, prices, conditions or stock.")
    featured_item_ids: list[int] = Field(
        default_factory=list, max_length=5,
        description='Ids of copies to show as cards, ONLY from the provided list of available copies')
    follow_up_question: str | None = Field(default=None, description='Optional question to narrow down the search')
