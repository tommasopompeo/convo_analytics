import sqlite3
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import create_access_token, hash_password, verify_password
from app.db import utc_now_iso
from app.web import db_dep, get_current_user_optional, templates

router = APIRouter()


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, user=Depends(get_current_user_optional)):
    """Render the registration page, redirecting to home if already logged in."""
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "register.html", {})


@router.post("/register")
def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    conn=Depends(db_dep),
):
    """Handle user registration submission."""
    username = username.strip()
    if not username or not password:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"error": "Username and password are required."},
            status_code=400,
        )

    # Check if user already exists
    existing = conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()
    if existing:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"error": "Username is already taken."},
            status_code=400,
        )

    # Hash password and insert user
    pw_hash = hash_password(password)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, pw_hash, utc_now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"error": "Username is already taken."},
            status_code=400,
        )

    return RedirectResponse("/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user=Depends(get_current_user_optional)):
    """Render the login page, redirecting to home if already logged in."""
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
def login_submit(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    conn=Depends(db_dep),
):
    """Handle user login submission, establishing a cookie session token on success."""
    username = username.strip()
    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password."},
            status_code=400,
        )

    # Create token and set cookie
    token = create_access_token({"sub": row["username"]})
    res = RedirectResponse("/", status_code=303)
    res.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=7 * 24 * 3600,
        samesite="lax",
    )
    return res


@router.post("/logout")
def logout(response: Response):
    """Handle logging out, clearing the session token cookie."""
    res = RedirectResponse("/login", status_code=303)
    res.delete_cookie(key="session_token")
    return res
