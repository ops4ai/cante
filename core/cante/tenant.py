"""Fail-closed multi-tenant enforcement (S1).

A request-scoped tenant id lives in a :class:`~contextvars.ContextVar`. A
SQLAlchemy ``do_orm_execute`` event requires a tenant context for every SELECT
against :class:`~cante.models.TenantScoped` models — if none is set and no
explicit bypass is active, the query raises :class:`MissingTenantContextError`
(fail-closed). A ``before_flush`` event stamps ``tenant_id`` server-side on new
rows from the active context and rejects rows whose tenant_id disagrees.

Bypass is reserved for cross-tenant setup such as login (which must look a user
up by email before a tenant is known) and the seed/admin bootstrap.
"""
from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

_tenant_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cante_tenant_id", default=None
)
_tenant_bypass_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "cante_tenant_bypass", default=False
)


class MissingTenantContextError(RuntimeError):
    """Raised when a tenant-scoped query/write runs without a tenant context."""


class TenantMismatchError(RuntimeError):
    """Raised when a row's tenant_id conflicts with the active context."""


def current_tenant_id() -> str | None:
    return _tenant_id_var.get()


def is_bypass_active() -> bool:
    return _tenant_bypass_var.get()


@contextmanager
def with_tenant(tenant_id: str) -> Iterator[None]:
    """Run a block scoped to *tenant_id* (queries filtered, writes stamped)."""
    tok_t = _tenant_id_var.set(str(tenant_id))
    tok_b = _tenant_bypass_var.set(False)
    try:
        yield
    finally:
        _tenant_id_var.reset(tok_t)
        _tenant_bypass_var.reset(tok_b)


@contextmanager
def with_bypass() -> Iterator[None]:
    """Run a block with tenant enforcement disabled (login / bootstrap only)."""
    tok_t = _tenant_id_var.set(None)
    tok_b = _tenant_bypass_var.set(True)
    try:
        yield
    finally:
        _tenant_id_var.reset(tok_t)
        _tenant_bypass_var.reset(tok_b)


def _tenant_scoped_models() -> set[type]:
    """All registered model classes that inherit TenantScoped."""
    from cante.db import Base
    from cante.models import TenantScoped

    return {m for m in Base.registry._class_registry.values() if isinstance(m, type) and issubclass(m, TenantScoped)}


def _referenced_tenant_models(stmt) -> set[type]:
    """Concrete TenantScoped model classes referenced by *stmt* (incl. counts/joins)."""
    from sqlalchemy import Table
    from sqlalchemy.sql import visitors

    from cante.db import Base
    from cante.models import TenantScoped

    tables = {o for o in visitors.iterate(stmt, {}) if isinstance(o, Table)}
    found: set[type] = set()
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if isinstance(cls, type) and issubclass(cls, TenantScoped) and mapper.local_table in tables:
            found.add(cls)
    return found


def install_tenant_enforcement() -> None:
    """Register the do_orm_execute + before_flush hooks (idempotent)."""
    from cante.models import TenantScoped

    if getattr(install_tenant_enforcement, "_installed", False):
        return
    install_tenant_enforcement._installed = True  # type: ignore[attr-defined]

    @event.listens_for(Session, "do_orm_execute")
    def _enforce_tenant_read(state):
        # Bypass (login/bootstrap): no filtering.
        if _tenant_bypass_var.get():
            return
        # Only filter ORM SELECTs (skip inserts/updates/DDL).
        if not state.is_select:
            return
        # Is this query touching any tenant-scoped table at all? If not (e.g.
        # SkillVersion), leave it alone — those have no tenant_id column.
        models = _referenced_tenant_models(state.statement)
        if not models:
            return

        # S1 fail-closed: a tenant-scoped read REQUIRES a tenant context.
        # This check runs in plain Python (NOT inside the with_loader_criteria
        # lambda) — SQLAlchemy's lambda-SQL instrumentation rebinds closure
        # variables during analysis, so a raise keyed on a closure var would
        # silently never fire.
        tid = _tenant_id_var.get()
        if tid is None:
            names = ", ".join(sorted(m.__name__ for m in models))
            raise MissingTenantContextError(
                f"Refusing tenant-scoped SELECT on [{names}] without a tenant context"
            )

        # tid is guaranteed non-None here, so the lambda has no conditional
        # raise — it always yields a valid filter expression. with_loader_criteria
        # applies it to each TenantScoped subclass present in the query.
        def _criteria(cls):
            return cls.tenant_id == tid

        state.statement = state.statement.options(
            with_loader_criteria(TenantScoped, _criteria, include_aliases=True)
        )

    @event.listens_for(Session, "before_attach")
    def _stamp_tenant_on_attach(session, obj):
        """Stamp tenant_id server-side the moment an object joins the session.

        Using before_attach (not before_flush) means the stamp happens while the
        request's tenant context is active, so a commit that runs *outside* the
        with_tenant() block (a common pattern in tests and seeds) still carries
        the correct tenant. Fail-closed: adding a tenant-scoped object with no
        context and no bypass is refused.
        """
        if _tenant_bypass_var.get():
            return
        if not isinstance(obj, tuple(_tenant_scoped_models())):
            return
        tid = _tenant_id_var.get()
        if tid is None:
            raise MissingTenantContextError(
                f"Refusing to attach {type(obj).__name__} without a tenant context"
            )
        existing = obj.tenant_id
        if existing is None or str(existing) != str(tid):
            obj.tenant_id = tid  # server-side stamp; ignores any client-supplied value

    @event.listens_for(Session, "before_flush")
    def _guard_tenant_on_flush(session, flush_context, instances):
        """Safety net + dirty-tamper guard.

        New rows must have a tenant_id by flush time (before_attach should have
        stamped them). Dirty rows whose tenant_id was mutated to disagree with
        the active context are refused — but only when a context is active, so a
        commit that runs outside with_tenant() (common in tests/seeds) is fine.
        """
        if _tenant_bypass_var.get():
            return
        scoped = _tenant_scoped_models()
        tid = _tenant_id_var.get()
        for obj in list(session.new):
            if isinstance(obj, tuple(scoped)) and obj.tenant_id is None:
                if tid is None:
                    raise MissingTenantContextError(
                        f"Refusing tenant-scoped write on {type(obj).__name__} without a tenant context"
                    )
                obj.tenant_id = tid
        if tid is None:
            return  # commit outside a tenant context — trust earlier attach/load
        for obj in list(session.dirty):
            if isinstance(obj, tuple(scoped)) and obj.tenant_id is not None and str(obj.tenant_id) != str(tid):
                raise TenantMismatchError(
                    f"Row tenant_id {obj.tenant_id} != active tenant {tid}"
                )


# Auto-install on import so every process that imports this module is protected.
install_tenant_enforcement()


__all__ = [
    "MissingTenantContextError",
    "TenantMismatchError",
    "current_tenant_id",
    "is_bypass_active",
    "with_tenant",
    "with_bypass",
    "install_tenant_enforcement",
]
