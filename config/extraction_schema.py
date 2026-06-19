from typing import Literal
from pydantic import BaseModel


class Entity(BaseModel):
    name: str
    type: Literal["Person", "Paper", "Concept", "Tool", "Method", "Dataset", "Organization", "Event"]
    description: str


class Relation(BaseModel):
    subject: str
    predicate: str
    object: str
    evidence: str


class Extraction(BaseModel):
    entities: list[Entity]
    relations: list[Relation]
