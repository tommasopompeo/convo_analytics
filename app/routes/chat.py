"""Chat router for the digital twin chatbot interface."""
from __future__ import annotations

import json
from typing import Optional
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from ..db import utc_now_iso
from ..web import db_dep, get_current_user, templates, load_current_profile
from ..gemini_client import chat_with_persona_async

router = APIRouter()


@router.get("/chat", response_class=HTMLResponse)
def chat_home(
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    """Render the main chatbot UI and load the user's past chat sessions."""
    sessions = conn.execute(
        "SELECT id, title, created_at FROM chat_sessions WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()

    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "request": request,
            "user": user,
            "sessions": [dict(s) for s in sessions],
            "active_session": None,
            "messages": [],
            "nav": "chat",
        }
    )


@router.get("/chat/{session_id}", response_class=HTMLResponse)
def load_chat_session(
    session_id: int,
    request: Request,
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    """Load a specific chat session, verifying user ownership, and render the chat template."""
    session = conn.execute(
        "SELECT id, title FROM chat_sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"])
    ).fetchone()

    if not session:
        return RedirectResponse("/chat", status_code=303)

    # Fetch past messages in ascending order
    messages_rows = conn.execute(
        "SELECT role, content, sources, created_at FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,)
    ).fetchall()

    messages = []
    for r in messages_rows:
        sources_list = []
        if r["sources"]:
            try:
                sources_list = json.loads(r["sources"])
            except Exception:
                pass
        messages.append({
            "role": r["role"],
            "content": r["content"],
            "sources": sources_list,
            "created_at": r["created_at"]
        })

    # Fetch all sessions for the sidebar list
    sessions = conn.execute(
        "SELECT id, title, created_at FROM chat_sessions WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()

    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "request": request,
            "user": user,
            "sessions": [dict(s) for s in sessions],
            "active_session": dict(session),
            "messages": messages,
            "nav": "chat",
        }
    )


@router.post("/chat/message")
async def send_chat_message(
    message: str = Form(...),
    session_id: Optional[int] = Form(None),
    search_enabled: bool = Form(False),
    conn=Depends(db_dep),
    user=Depends(get_current_user),
):
    """Handle receiving a message, prompting Gemini with search grounding if enabled, and saving history."""
    message = message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    session_title = ""
    # If no session ID is provided (new session), create one
    if not session_id:
        title = message[:35] + "..." if len(message) > 35 else message
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chat_sessions (user_id, title, created_at) VALUES (?, ?, ?)",
            (user["id"], title, utc_now_iso())
        )
        session_id = cursor.lastrowid
        session_title = title
    else:
        # Verify the session belongs to the user
        session = conn.execute(
            "SELECT id, title FROM chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user["id"])
        ).fetchone()
        if not session:
            raise HTTPException(status_code=403, detail="Unauthorized session access")
        session_title = session["title"]

    # Fetch prior history for the session (excluding the message we are about to insert)
    history_rows = conn.execute(
        "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,)
    ).fetchall()
    chat_history = [{"role": r["role"], "content": r["content"]} for r in history_rows]

    # Insert user's new message
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (session_id, "user", message, utc_now_iso())
    )

    # Load current user profile data
    profile_data = load_current_profile(conn, user["id"])

    # Query the persona chat client
    ai_res = await chat_with_persona_async(
        chat_history=chat_history,
        user_message=message,
        profile_data=profile_data,
        search_enabled=search_enabled
    )

    # Save Twin's response to DB
    sources_json = json.dumps(ai_res["sources"])
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, sources, created_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, "model", ai_res["content"], sources_json, utc_now_iso())
    )
    conn.commit()

    return JSONResponse({
        "status": "success",
        "session_id": session_id,
        "session_title": session_title,
        "user_message": message,
        "ai_message": ai_res["content"],
        "sources": ai_res["sources"]
    })
