"""Data-access tier for branding."""
from sqlalchemy.orm import Session

from .models import Branding
from .schemas import BrandingUpsert


class BrandingRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, tenant_id: str) -> Branding | None:
        return self.db.get(Branding, tenant_id)

    def upsert(self, tenant_id: str, data: BrandingUpsert) -> Branding:
        row = self.db.get(Branding, tenant_id)
        if row is None:
            row = Branding(tenant_id=tenant_id)
            self.db.add(row)
        for field, value in data.model_dump().items():
            setattr(row, field, value)
        self.db.commit()
        self.db.refresh(row)
        return row
