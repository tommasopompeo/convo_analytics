import os
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from passlib.context import CryptContext
from . import config

# Using bcrypt scheme for password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Return the bcrypt hash of the password."""
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify that a password matches its bcrypt hash."""
    return pwd_context.verify(password, hashed)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT token containing user data."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=7)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, config.JWT_SECRET, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    """Decode a JWT token, verifying its signature. Returns payload or None."""
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
        return payload
    except JWTError:
        return None
