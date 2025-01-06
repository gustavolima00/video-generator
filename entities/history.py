from typing import List
from pydantic import BaseModel, Field

class History(BaseModel):
    title: str = Field('', title="Title of the history")
    description: str = Field(..., title="Description of the history")
    content: str = Field(..., title="Content of the history")
    hashtags: List[str] = Field(..., title="List of hashtags")
