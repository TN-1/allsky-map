from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator, field_serializer, model_validator
from pydantic.alias_generators import to_camel
import re

# Compile regex at module level to prevent recompiling on every validation call.
# Bare IPs and 'localhost' are intentionally excluded — they would bypass SSRF
# guards and expose internal services. Only proper hostnames are accepted.
URL_REGEX = re.compile(
    r'^https?://'
    r'(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)'
    r'(?::\d+)?'
    r'(?:/?|[/?]\S+)$', re.IGNORECASE
)

class CameraResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True
    )

    name: str
    owner: Optional[str] = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    site_url: Optional[str] = ""
    image_url: Optional[str] = ""
    status: str
    last_seen: Optional[datetime] = None
    
    # Load validation flags from DB, but exclude them from the JSON response
    site_url_valid: Optional[bool] = Field(default=True, exclude=True)
    image_url_valid: Optional[bool] = Field(default=True, exclude=True)

    # Round coordinates to 2 decimal places to protect owner privacy
    @field_serializer("lat", "lng")
    def serialize_coords(self, val: float) -> float:
        return round(val, 2) if val is not None else 0.0

    # Format owner to default to empty string if None
    @field_serializer("owner")
    def serialize_owner(self, val: Optional[str]) -> str:
        return val or ""

    # Format last_seen to ISO-8601 string or empty string
    @field_serializer("last_seen")
    def serialize_last_seen(self, val: Optional[datetime]) -> str:
        if not val:
            return ""
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc).isoformat()
        return val.astimezone(timezone.utc).isoformat()


    # Model validator running after attribute loading to sanitize dead links
    @model_validator(mode="after")
    def sanitize_urls(self) -> "CameraResponse":
        self.site_url = self.site_url or ""
        self.image_url = self.image_url or ""
        # Default to True if None is loaded
        site_ok = self.site_url_valid if self.site_url_valid is not None else True
        img_ok = self.image_url_valid if self.image_url_valid is not None else True
        if not site_ok:
            self.site_url = ""
        if not img_ok:
            self.image_url = ""
        return self


class CameraPing(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True
    )

    name: str = Field(..., min_length=1, max_length=100)
    owner: str = Field("", max_length=100)
    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)
    site_url: str = Field("", max_length=2000)
    image_base64: str = Field(..., description="Base64 encoded image content")

    @field_validator("site_url", mode="before")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> str:
        if not v:
            return ""
        v = str(v).strip()
        if not v:
            return ""
        if not re.match(r"^https?://", v, re.IGNORECASE):
            v = "https://" + v
        if not URL_REGEX.match(v):
            raise ValueError("Must be a valid HTTP or HTTPS URL")
        return v


