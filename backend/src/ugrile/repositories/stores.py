"""Stores and people repositories."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.errors import NotFoundError
from .models import Person, Store


class StoreRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, store_id: str) -> Store:
        store = self.session.get(Store, store_id)
        if store is None:
            raise NotFoundError(f"store not found: {store_id}")
        return store

    def list_for_tenant(self, tenant_id: str) -> list[Store]:
        stmt = select(Store).where(Store.tenant_id == tenant_id).order_by(Store.internal_code)
        return list(self.session.execute(stmt).scalars())


class PersonRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, person_id: str) -> Person:
        person = self.session.get(Person, person_id)
        if person is None:
            raise NotFoundError(f"person not found: {person_id}")
        return person

    def list_for_tenant(self, tenant_id: str) -> list[Person]:
        stmt = select(Person).where(Person.tenant_id == tenant_id).order_by(Person.internal_code)
        return list(self.session.execute(stmt).scalars())

    def list_for_store(self, tenant_id: str, store_id: str) -> list[Person]:
        stmt = (
            select(Person)
            .where(Person.tenant_id == tenant_id, Person.home_store_id == store_id)
            .order_by(Person.internal_code)
        )
        return list(self.session.execute(stmt).scalars())


__all__ = ["StoreRepository", "PersonRepository"]
