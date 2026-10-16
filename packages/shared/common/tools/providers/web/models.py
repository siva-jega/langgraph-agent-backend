from pydantic import BaseModel
class WebSearchResult(BaseModel):
    url: str
    title: str
    content: str
    score: float
