from pydantic import BaseModel, Field, HttpUrl


class GeneratePersonaRequest(BaseModel):
    homepageUrl: HttpUrl
    aboutPageUrl: HttpUrl | None = None
    role: str
    targetAudience: str
    guardrails: str | None = None
    responseStyle: str


class GeneratedPersona(BaseModel):
    personaName: str = Field(description="Short, brandable agent name — 1-2 words.")
    personaDescription: str = Field(
        description=(
            "Detailed markdown persona prompt with sections: Identity & brand, "
            "Personality & tone, Scope of help, Strict guardrails (only when "
            "provided), Response style. Second person. 180–350 words. Drops "
            "directly into the downstream system prompt."
        )
    )
