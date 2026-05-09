"""Site — описание квартала: исходные данные пользователя."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Site(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area_m2: float = Field(..., gt=0, description="площадь квартала, м²")
    name: str = "site"
