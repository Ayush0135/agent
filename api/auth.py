from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from db.supabase_client import get_supabase_client
import asyncio

router = APIRouter(prefix="/auth", tags=["Authentication"])

# ── Request / Response schemas ──────────────────────────────────────────────

class SignUpRequest(BaseModel):
    email: EmailStr
    password: str

class SignInRequest(BaseModel):
    email: EmailStr
    password: str

class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str

# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignUpRequest):
    """
    Create a new account. Returns a JWT access_token on success.
    The user_id is their unique Supabase UUID — used as the Redis key for credits.
    """
    try:
        sb = get_supabase_client()
        def _signup():
            return sb.auth.sign_up({
                "email": payload.email,
                "password": payload.password
            })
        response = await asyncio.to_thread(_signup)

        if not response.user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sign-up failed. Check email/password requirements."
            )

        # Initialize their free credits in Redis immediately on signup
        from services.payment_service import initialize_user_if_needed
        await initialize_user_if_needed(response.user.id)

        return AuthResponse(
            access_token=response.session.access_token,
            user_id=response.user.id,
            email=response.user.email
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/login", response_model=AuthResponse)
async def login(payload: SignInRequest):
    """
    Sign in with email + password. Returns a fresh JWT access_token.
    """
    try:
        sb = get_supabase_client()
        def _login():
            return sb.auth.sign_in_with_password({
                "email": payload.email,
                "password": payload.password
            })
        response = await asyncio.to_thread(_login)

        if not response.user or not response.session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password."
            )

        return AuthResponse(
            access_token=response.session.access_token,
            user_id=response.user.id,
            email=response.user.email
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )

@router.post("/logout")
async def logout(token: str):
    """
    Signs the user out and invalidates the JWT on Supabase's side.
    Pass the access_token as a query param: /auth/logout?token=<jwt>
    """
    try:
        sb = get_supabase_client()
        def _logout():
            return sb.auth.sign_out()
        await asyncio.to_thread(_logout)
        return {"message": "Logged out successfully."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/me")
async def get_me(token: str):
    """
    Returns the authenticated user's profile and current credit balance.
    Pass the access_token as a query param: /auth/me?token=<jwt>
    """
    try:
        sb = get_supabase_client()
        def _get_user():
            return sb.auth.get_user(token)
        response = await asyncio.to_thread(_get_user)

        if not response or not response.user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

        user_id = response.user.id

        # Also pull their credit info from Redis
        from db.redis_client import redis_client
        plan   = await redis_client.get(f"user:{user_id}:plan") or "free"
        if plan == "free":
            credits = await redis_client.get(f"user:{user_id}:queries_left") or 0
        else:
            credits = await redis_client.get(f"user:{user_id}:credits") or 0

        return {
            "user_id": user_id,
            "email": response.user.email,
            "plan": plan,
            "credits_remaining": int(credits)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
