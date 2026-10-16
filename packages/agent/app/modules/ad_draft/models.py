from pydantic import BaseModel, Field


class AdProductHint(BaseModel):
    """A catalog item the caller passes in so the draft can propose a real creative."""

    title: str
    imageUrl: str | None = None
    price: str | None = None
    url: str | None = None


class AdDraftRequest(BaseModel):
    agentId: str
    #: Optional catalog items (from the caller's catalog) to source a creative from.
    catalog: list[AdProductHint] = Field(default_factory=list)
    #: Optional free-text steer — promo, angle, season, audience note.
    brief: str | None = None


class AdTargeting(BaseModel):
    countries: list[str] = Field(description="ISO-3166-1 alpha-2 codes, e.g. ['CA', 'US'].")
    ageMin: int = Field(ge=13, le=65)
    ageMax: int = Field(ge=13, le=65)


class AdDraft(BaseModel):
    copyVariants: list[str] = Field(
        description="Exactly 3 short, on-brand ad primary-text variants (ideally under 125 chars)."
    )
    openingMessage: str = Field(
        description=(
            "The first WhatsApp message the agent sends when someone taps the ad — "
            "warm, in the agent's voice, references the offer, invites a reply."
        )
    )
    creativePrompt: str = Field(
        description="One-line description of the suggested creative, for when no catalog image fits."
    )
    suggestedImageUrl: str | None = Field(
        default=None,
        description="A catalog image URL to reuse as the creative. Only from the provided catalog; never invented.",
    )
    targeting: AdTargeting
