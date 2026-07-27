import uuid
from abc import ABC
from abc import abstractmethod
from typing import Generic
from typing import TypeVar

from traderos.domain.repositories.base import Repository

T = TypeVar("T")


class RepositoryContractTests(ABC, Generic[T]):
    @abstractmethod
    def make_repository(self) -> Repository[T]: ...

    @abstractmethod
    def make_entity(self) -> T: ...

    def test_add_and_get(self) -> None:
        repo = self.make_repository()
        entity = self.make_entity()
        added = repo.add(entity)
        assert added.id is not None
        fetched = repo.get(added.id)
        assert fetched is not None
        assert fetched.id == added.id

    def test_get_nonexistent_returns_none(self) -> None:
        repo = self.make_repository()
        fetched = repo.get(uuid.uuid4())
        assert fetched is None

    def test_list_empty(self) -> None:
        repo = self.make_repository()
        assert repo.list() == []

    def test_list_with_entities(self) -> None:
        repo = self.make_repository()
        repo.add(self.make_entity())
        repo.add(self.make_entity())
        items = repo.list()
        assert len(items) == 2

    def test_delete_removes_entity(self) -> None:
        repo = self.make_repository()
        entity = repo.add(self.make_entity())
        repo.delete(entity.id)
        assert repo.get(entity.id) is None

    def test_delete_nonexistent_does_not_raise(self) -> None:
        repo = self.make_repository()
        repo.delete(uuid.uuid4())
