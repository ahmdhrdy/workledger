from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import delete, func, select

from .models import Session, User
from .schemas import Strict
from .security import hasher, require_role


class UserInput(Strict):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    role: Literal["admin", "hr", "payroll", "viewer", "disabled"]
    password: str | None = Field(default=None, min_length=12, max_length=128)


def register(app, factory, current_user, audit):
    def database():
        with factory() as db:
            yield db

    @app.get("/api/v1/users")
    def users(user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin")
        return [
            {"id": u.id, "name": u.name, "email": u.email, "role": u.role}
            for u in db.scalars(
                select(User).where(User.company_id == user.company_id).order_by(User.name)
            ).all()
        ]

    @app.post("/api/v1/users", status_code=201)
    def add_user(body: UserInput, user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin")
        if not body.password:
            raise HTTPException(422, "Set a password of at least 12 characters")
        row = User(
            company_id=user.company_id,
            name=body.name,
            email=body.email.lower(),
            role=body.role,
            password_hash=hasher.hash(body.password),
        )
        db.add(row)
        db.flush()
        audit(db, user, "user.created", row.id, {"role": row.role})
        db.commit()
        return {"id": row.id}

    @app.put("/api/v1/users/{identifier}")
    def edit_user(
        identifier: str, body: UserInput, user=Depends(current_user), db=Depends(database)
    ):
        require_role(user, "admin")
        row = db.scalar(
            select(User).where(User.id == identifier, User.company_id == user.company_id)
        )
        if not row:
            raise HTTPException(404, "User not found")
        if (
            row.role == "admin"
            and body.role != "admin"
            and db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.company_id == user.company_id, User.role == "admin")
            )
            <= 1
        ):
            raise HTTPException(409, "Keep at least one administrator account")
        invalidate = row.role != body.role or row.email != body.email.lower() or bool(body.password)
        row.name, row.email, row.role = body.name, body.email.lower(), body.role
        if body.password:
            row.password_hash = hasher.hash(body.password)
        if invalidate:
            db.execute(delete(Session).where(Session.user_id == row.id))
        audit(db, user, "user.updated", row.id, {"role": row.role, "sessions_revoked": invalidate})
        db.commit()
        return {"ok": True, "sign_in_again": row.id == user.id and invalidate}
