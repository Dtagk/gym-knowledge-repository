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


# Allowed technique-cue keywords. Keep this list small and stable: it is the
# user-facing filter ("show me mistakes during lateral raises").
CueKind = Literal["mistake", "cue", "setup", "tempo", "breathing", "range-of-motion"]


class TechniqueCue(BaseModel):
    exercise: str   # must match an extracted entity name (a Method entity)
    cue: str        # the technique note / common mistake
    kind: CueKind = "cue"
    evidence: str = ""  # exact quote from the transcript


class Extraction(BaseModel):
    entities: list[Entity]
    relations: list[Relation]
    cues: list[TechniqueCue] = []
