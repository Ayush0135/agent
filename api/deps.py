from fastapi import Depends, HTTPException, status, WebSocket
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from db.supabase_client import get_supabase_client

bearer_scheme = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    """
    FastAPI dependency: validates Supabase JWT from the Authorization: Bearer <token> header.
    Returns the authenticated user dict or raises 401.
    """
    token = credentials.credentials
    try:
        sb = get_supabase_client()
        response = sb.auth.get_user(token)
        if not response or not response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return {
            "id": response.user.id,
            "email": response.user.email,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def get_ws_user(websocket: WebSocket) -> dict | None:
    """
    WebSocket auth: reads token from the query param `?token=<jwt>`.
    Returns the user dict or None if not authenticated.
    """
    token = websocket.query_params.get("token")
    if not token:
        return None
    try:
        sb = get_supabase_client()
        response = sb.auth.get_user(token)
        if response and response.user:
            return {
                "id": response.user.id,
                "email": response.user.email,
            }
    except Exception:
        pass
    return None
