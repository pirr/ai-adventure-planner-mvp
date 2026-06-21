from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from app.config import settings
from app.schemas import AnalyticsEvent, FeedbackRequest
from app.services.storage.accounts import AccountsRepo
from app.services.storage.api_usage import ApiUsageRepo
from app.services.storage.auth import AuthRepo
from app.services.storage.community import CommunityRepo
from app.services.storage.db import Database
from app.services.storage.events import EventsRepo
from app.services.storage.feedback import FeedbackRepo
from app.services.storage.lifecycle import LifecycleRepo
from app.services.storage.place_marks import PlaceMarksRepo
from app.services.storage.search import SearchRepo
from app.services.storage.users import UsersRepo


class Storage:
    """Thin facade over the per-domain repositories. Consumers reach a repo by
    attribute (`storage.accounts`, `storage.place_marks`, ...); all repos share
    one `Database`. Business logic lives in the service layer, which calls these
    repos. The only methods kept here are the two no-logic write coordinators
    that touch the `users` table alongside a domain write."""

    def __init__(self, path: Path = settings.sqlite_path):
        self.db = Database(path)
        self.users = UsersRepo()
        self.accounts = AccountsRepo(self.db)
        self.auth = AuthRepo(self.db, self.accounts)
        self.place_marks = PlaceMarksRepo(self.db)
        self.search = SearchRepo(self.db)
        self.feedback = FeedbackRepo(self.db)
        self.events = EventsRepo(self.db)
        self.community = CommunityRepo(self.db)
        self.api_usage = ApiUsageRepo(self.db)
        self.lifecycle = LifecycleRepo(self.db)

    def _connect(self) -> sqlite3.Connection:
        """Test/seed seam: a raw connection (its `with` block is a transaction)."""
        return self.db.connect()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One transaction services use to make multi-repo writes atomic."""
        with self.db.transaction() as conn:
            yield conn

    def save_feedback(self, feedback: FeedbackRequest, account_id: int | None = None) -> None:
        with self.transaction() as conn:
            self.users.touch(conn, feedback.anonymous_id)
            self.feedback.insert(conn, feedback, account_id)

    def save_event(self, event: AnalyticsEvent, account_id: int | None = None) -> None:
        with self.transaction() as conn:
            self.users.touch(conn, event.anonymous_id)
            self.events.insert(conn, event, account_id)


storage = Storage()
