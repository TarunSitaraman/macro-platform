"""Authentication routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from src.database import get_db, User, Tenant
from src.utils.auth import (
    create_access_token, get_current_user, hash_password, verify_password
)
from pydantic import BaseModel, EmailStr
import uuid

router = APIRouter(prefix="/auth", tags=["Authentication"])


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    tenant_name: str  # Creating a new tenant during registration for now


class UserOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    tenant_id: uuid.UUID

    class Config:
        from_attributes = True


def authenticate_user(db: Session, email: str, password: str):
    from src.utils.auth import verify_password
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user


@router.post("/login")
async def login(
    db: Session = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends()
):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": str(user.user_id)})
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/register", response_model=UserOut)
async def register(user_in: UserCreate, db: Session = Depends(get_db)):
    # Check if user exists
    if db.query(User).filter(User.email == user_in.email).first():
        raise HTTPException(status_code=400, detail="User already registered")

    # Create tenant
    tenant = Tenant(
        name=user_in.tenant_name,
        slug=user_in.tenant_name.lower().replace(" ", "-")
    )
    db.add(tenant)
    db.flush()

    # Create user
    user = User(
        email=user_in.email,
        hashed_password=hash_password(user_in.password),
        full_name=user_in.full_name,
        tenant_id=tenant.tenant_id,
        role="admin"  # First user is admin
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/me", response_model=UserOut)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user
