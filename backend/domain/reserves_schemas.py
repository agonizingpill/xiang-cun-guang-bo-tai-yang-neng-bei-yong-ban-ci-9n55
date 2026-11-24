from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints


class ForecastWh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    airslot: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    meter: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    clear: bool


class SaveReserve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class TrialModels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    airslot: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    meter: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    clear: bool
    candidate_model: Literal['fourier', 'persistence']


class EnableModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
