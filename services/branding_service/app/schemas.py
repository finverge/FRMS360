"""DTO tier — branding contracts."""
from datetime import datetime

from pydantic import BaseModel, Field

HEX = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"


class BrandingUpsert(BaseModel):
    display_name: str = Field(default="", max_length=255)
    logo_url: str = Field(default="", max_length=1024)
    primary_color: str = Field(default="#0F6E7A", pattern=HEX)
    accent_color: str = Field(default="#19A6B8", pattern=HEX)
    neutral_color: str = Field(default="#5A6472", pattern=HEX)
    default_theme: str = Field(default="light", pattern=r"^(light|dark)$")
    custom_domain: str = Field(default="", max_length=255)


class BrandingOut(BaseModel):
    tenant_id: str
    display_name: str
    logo_url: str
    primary_color: str
    accent_color: str
    neutral_color: str
    default_theme: str
    custom_domain: str
    updated_at: datetime

    model_config = {"from_attributes": True}
