from datetime import datetime, timezone
import hashlib
from sqlalchemy import String, Float, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class CameraDB(Base):
    __tablename__ = "cameras"
    
    api_key: Mapped[str] = mapped_column(String, primary_key=True, index=True)  # The hashed UUID
    id: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)  # Public unique camera ID
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    owner: Mapped[str | None] = mapped_column(String, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    site_url: Mapped[str | None] = mapped_column(String, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    status: Mapped[str] = mapped_column(String, default="online")
    site_url_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    image_url_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __init__(self, **kwargs):
        if "id" not in kwargs and "api_key" in kwargs and kwargs["api_key"]:
            kwargs["id"] = hashlib.sha256(kwargs["api_key"].encode("utf-8")).hexdigest()[:16]
        super().__init__(**kwargs)

