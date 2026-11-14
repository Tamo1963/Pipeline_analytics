"""File-based multi-user project store.

Layout under the root (default ``$PIPELINE_PROJECTS_DIR`` or ``./projects``)::

    <project id>/project.json          name, owner, sharing, latest version
    <project id>/versions/000001.json  one file per saved version (append-only)

Rules

* **Access.** The owner can do anything. Users in ``shared_with`` are either
  ``viewer`` (read) or ``editor`` (read and save new versions). Only the owner
  shares, unshares, renames or archives. Everyone else is told the project does
  not exist, so ids do not leak.
* **History.** Saving never overwrites: each save writes the next version file.
  Any earlier version can be read back, and restoring one saves it again as a
  new version.
* **Concurrency.** A save states the version it was based on
  (``expected_version``). If someone saved in the meantime the save is refused
  with :class:`VersionConflictError` and the caller reloads. Every read-modify-write
  holds a per-project file lock, and files are written atomically.

User names are whatever identity the caller has verified (the Streamlit app
uses the signed-in e-mail). The store does not authenticate anyone.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pipeline_analytics.core.storage import atomic_write_text, file_lock

Role = Literal["viewer", "editor"]
_SLUG = re.compile(r"[^a-z0-9]+")


class ProjectError(Exception):
    pass


class ProjectNotFoundError(ProjectError, KeyError):
    pass


class AccessDeniedError(ProjectError, PermissionError):
    pass


class VersionConflictError(ProjectError):
    def __init__(self, project_id: str, expected: int, latest: int, by: str) -> None:
        self.expected, self.latest, self.by = expected, latest, by
        super().__init__(
            f"project {project_id} is at version {latest} (saved by {by}); your copy was based "
            f"on version {expected}. Reload, re-apply your changes and save again."
        )


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _norm(user: str) -> str:
    u = user.strip().lower()
    if not u:
        raise ValueError("a user name is needed")
    return u


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_inf_nan="constants")


class Project(_M):
    id: str
    name: str
    description: str = ""
    owner: str
    shared_with: dict[str, Role] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    latest_version: int = 0
    latest_by: str = ""
    archived: bool = False

    def role_of(self, user: str) -> Literal["owner", "editor", "viewer"] | None:
        u = _norm(user)
        if u == self.owner:
            return "owner"
        return self.shared_with.get(u)

    def can_read(self, user: str) -> bool:
        return self.role_of(user) is not None

    def can_write(self, user: str) -> bool:
        return self.role_of(user) in ("owner", "editor")


class VersionInfo(_M):
    version: int
    author: str
    created_at: datetime
    message: str


class ProjectVersion(VersionInfo):
    project_id: str
    payload: dict[str, Any]


class ProjectStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or os.environ.get("PIPELINE_PROJECTS_DIR", "projects"))

    # ----------------------------------------------------------- internals
    def _dir(self, pid: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{1,80}", pid):
            raise ProjectNotFoundError(pid)
        return self.root / pid

    def _meta_path(self, pid: str) -> Path:
        return self._dir(pid) / "project.json"

    def _read(self, pid: str) -> Project:
        p = self._meta_path(pid)
        if not p.exists():
            raise ProjectNotFoundError(pid)
        return Project.model_validate_json(p.read_text(encoding="utf-8"))

    def _write(self, project: Project) -> None:
        atomic_write_text(self._meta_path(project.id), project.model_dump_json(indent=2))

    def _readable(self, pid: str, user: str) -> Project:
        p = self._read(pid)
        if not p.can_read(user):
            raise ProjectNotFoundError(pid)  # do not reveal that it exists
        return p

    @staticmethod
    def _rebuild(p: Project, **upd: Any) -> Project:
        return Project.model_validate({**p.model_dump(), **upd})

    def _version_path(self, pid: str, version: int) -> Path:
        return self._dir(pid) / "versions" / f"{version:06d}.json"

    # ------------------------------------------------------------- queries
    def list_for(self, user: str, include_archived: bool = False) -> list[Project]:
        if not self.root.is_dir():
            return []
        out = []
        for meta in self.root.glob("*/project.json"):
            p = Project.model_validate_json(meta.read_text(encoding="utf-8"))
            if p.can_read(user) and (include_archived or not p.archived):
                out.append(p)
        return sorted(out, key=lambda p: p.updated_at, reverse=True)

    def get(self, pid: str, user: str) -> Project:
        return self._readable(pid, user)

    def history(self, pid: str, user: str) -> list[VersionInfo]:
        self._readable(pid, user)
        vdir = self._dir(pid) / "versions"
        out = []
        for f in sorted(vdir.glob("*.json")):
            v = ProjectVersion.model_validate_json(f.read_text(encoding="utf-8"))
            out.append(VersionInfo(version=v.version, author=v.author, created_at=v.created_at,
                                   message=v.message))  # fmt: skip
        return out

    def load(self, pid: str, user: str, version: int | None = None) -> ProjectVersion:
        p = self._readable(pid, user)
        n = p.latest_version if version is None else version
        f = self._version_path(pid, n)
        if n < 1 or not f.exists():
            raise ProjectNotFoundError(f"{pid} has no version {n}")
        return ProjectVersion.model_validate_json(f.read_text(encoding="utf-8"))

    # ------------------------------------------------------------- changes
    def create(
        self,
        name: str,
        owner: str,
        description: str = "",
        payload: dict[str, Any] | None = None,
        message: str = "created",
    ) -> Project:
        owner = _norm(owner)
        if not name.strip():
            raise ValueError("a project name is needed")
        slug = _SLUG.sub("-", name.lower()).strip("-")[:40] or "project"
        pid = f"{slug}-{uuid.uuid4().hex[:8]}"
        now = _now()
        p = Project(id=pid, name=name.strip(), description=description, owner=owner,
                    created_at=now, updated_at=now)  # fmt: skip
        with file_lock(self._meta_path(pid)):
            self._write(p)
        if payload is not None:
            self.save(pid, owner, payload, message, expected_version=0)
            p = self._read(pid)
        return p

    def save(
        self,
        pid: str,
        user: str,
        payload: dict[str, Any],
        message: str,
        *,
        expected_version: int,
    ) -> ProjectVersion:
        user = _norm(user)
        json.dumps(payload)  # fail early on anything that is not JSON
        with file_lock(self._meta_path(pid)):
            p = self._readable(pid, user)
            if not p.can_write(user):
                raise AccessDeniedError(f"{user} can view but not edit project {pid}")
            if p.archived:
                raise AccessDeniedError(f"project {pid} is archived")
            if p.latest_version != expected_version:
                raise VersionConflictError(pid, expected_version, p.latest_version, p.latest_by)
            n = p.latest_version + 1
            v = ProjectVersion(
                project_id=pid, version=n, author=user, created_at=_now(),
                message=message.strip() or f"version {n}", payload=payload,
            )  # fmt: skip
            path = self._version_path(pid, n)
            if path.exists():  # pragma: no cover - guarded by the lock
                raise VersionConflictError(pid, expected_version, n, "unknown")
            atomic_write_text(path, v.model_dump_json(indent=1))
            self._write(self._rebuild(p, latest_version=n, latest_by=user, updated_at=v.created_at))
        return v

    def restore(
        self, pid: str, user: str, version: int, *, expected_version: int
    ) -> ProjectVersion:
        old = self.load(pid, user, version)
        return self.save(pid, user, old.payload, f"restored version {version}",
                         expected_version=expected_version)  # fmt: skip

    def _owner_change(
        self, pid: str, user: str, change: Callable[[Project], dict[str, Any]]
    ) -> Project:
        """Read, check ownership, apply ``change(project)`` and write, all under the lock."""
        with file_lock(self._meta_path(pid)):
            p = self._readable(pid, user)
            if p.role_of(user) != "owner":
                raise AccessDeniedError(f"only the owner ({p.owner}) can do this")
            new = self._rebuild(p, updated_at=_now(), **change(p))
            self._write(new)
        return new

    def share(self, pid: str, user: str, other: str, role: Role) -> Project:
        other = _norm(other)

        def change(p: Project) -> dict[str, Any]:
            if other == p.owner:
                raise ValueError("the owner already has full access")
            return {"shared_with": {**p.shared_with, other: role}}

        return self._owner_change(pid, user, change)

    def unshare(self, pid: str, user: str, other: str) -> Project:
        other = _norm(other)
        return self._owner_change(
            pid, user,
            lambda p: {"shared_with": {k: v for k, v in p.shared_with.items() if k != other}},
        )  # fmt: skip

    def rename(self, pid: str, user: str, name: str, description: str | None = None) -> Project:
        upd: dict[str, Any] = {"name": name.strip()}
        if description is not None:
            upd["description"] = description
        return self._owner_change(pid, user, lambda _p: upd)

    def set_archived(self, pid: str, user: str, archived: bool = True) -> Project:
        return self._owner_change(pid, user, lambda _p: {"archived": archived})


__all__ = [
    "AccessDeniedError",
    "Project",
    "ProjectError",
    "ProjectNotFoundError",
    "ProjectStore",
    "ProjectVersion",
    "Role",
    "VersionConflictError",
    "VersionInfo",
]
