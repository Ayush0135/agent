import json
import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

from orchestrator.pipeline import execute_pipeline
from services.payment_service import upgrade_user_plan, get_payment_qr, create_payment_order, verify_payment_by_order
from api.auth import router as auth_router
from api.deps import get_current_user, get_ws_user
from db.sqlite_client import get_history, delete_history_item

app = FastAPI(
    title="Automated Research Agent",
    description="AI-powered research pipeline with WebSocket streaming.",
    version="1.0.0"
)

# Include auth routes: /auth/signup, /auth/login, /auth/logout, /auth/me
app.include_router(auth_router)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Serve static frontend
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
FRONTEND_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# ── WebSocket Connection Manager ─────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

manager = ConnectionManager()

# ── Payment Schemas ───────────────────────────────────────────────────────────

class WebhookPayload(BaseModel):
    user_id: str
    transaction_status: str
    amount_paid: float

# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Health check for deployment services."""
    return {"status": "ok", "service": "Automated Research Agent"}

@app.get("/")
async def serve_ui():
    """Serve the main frontend UI."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Research Agent API v1.0.0"}

@app.get("/history")
async def get_research_history(current_user: dict = Depends(get_current_user)):
    """🔒 Returns the last 20 research queries from the local SQLite DB for this user."""
    return await get_history(current_user["id"])

@app.delete("/history/{item_id}")
async def delete_history(item_id: int, current_user: dict = Depends(get_current_user)):
    """🔒 Delete a specific history entry by ID."""
    await delete_history_item(item_id, current_user["id"])
    return {"status": "deleted", "id": item_id}

@app.get("/brain/stats")
async def brain_stats(current_user: dict = Depends(get_current_user)):
    """
    🧠 Returns the self-learning brain's current knowledge stats:
    - Total memories stored
    - Top trusted source domains
    - Average quality score of all research
    """
    from db.knowledge_base import get_trusted_domains
    from db.supabase_client import get_supabase_client
    import asyncio

    async def _get_stats():
        def _query():
            sb = get_supabase_client()
            rows = sb.table("research_memory") \
                .select("quality_score, access_count") \
                .execute().data or []
            return rows
        rows = await asyncio.to_thread(_query)
        total = len(rows)
        avg_quality = round(sum(r["quality_score"] for r in rows) / total, 3) if total else 0
        total_recalls = sum(r["access_count"] for r in rows)
        return total, avg_quality, total_recalls

    total_memories, avg_quality, total_recalls = await _get_stats()
    trusted_domains = await get_trusted_domains(top_n=5)

    return {
        "total_memories": total_memories,
        "avg_quality_score": avg_quality,
        "total_memory_recalls": total_recalls,
        "top_trusted_domains": trusted_domains,
        "message": f"Brain has memorized {total_memories} research sessions and recalled them {total_recalls} times."
    }

@app.get("/payment/qr")
async def generate_qr(
    amount: float = 99.0,
    current_user: dict = Depends(get_current_user)
):
    """🔒 Legacy QR endpoint — use /payment/create-order instead."""
    plan_name = "Researcher Plan" if amount >= 99.0 else "Student Plan"
    return await get_payment_qr(amount, transaction_note=plan_name)


class CreateOrderRequest(BaseModel):
    plan: str  # 'student' | 'researcher'

@app.post("/payment/create-order")
async def create_order(
    payload: CreateOrderRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    🔒 Step 1 of payment flow.
    Creates a unique Order ID, stores a pending payment record in Supabase,
    and returns a QR code with the Order ID embedded in the UPI note.
    The QR expires in 1 hour.
    """
    result = await create_payment_order(
        user_id = current_user["id"],
        email   = current_user["email"],
        plan    = payload.plan.lower(),
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to create order."))
    return result


class VerifyPaymentRequest(BaseModel):
    order_id:  str  # Order ID from the QR code (e.g. RA-X7K9-49)
    utr_last4: str  # Last 4 digits of the UPI Transaction Reference

@app.post("/payment/verify")
async def verify_payment(
    payload: VerifyPaymentRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    🔒 Step 2 of payment flow.
    Verifies the Order ID + last-4 UTR digits:
    - order_id must exist, belong to this user, be pending, and not expired
    - utr_last4 must be exactly 4 numeric digits
    - On success: marks order approved, activates plan + credits in Redis
    """
    result = await verify_payment_by_order(
        user_id   = current_user["id"],
        order_id  = payload.order_id.strip().upper(),
        utr_last4 = payload.utr_last4.strip(),
    )
    if not result["success"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return result


@app.post("/webhook/payment")
async def payment_webhook(payload: WebhookPayload):
    """Legacy webhook — kept for compatibility."""
    if payload.transaction_status.lower() == "success":
        if payload.amount_paid >= 99.0:
            await upgrade_user_plan(payload.user_id, "researcher", 120)
            return {"status": "success", "message": "Upgraded to Researcher. 120 credits added."}
        elif payload.amount_paid >= 49.0:
            await upgrade_user_plan(payload.user_id, "student", 50)
            return {"status": "success", "message": "Upgraded to Student. 50 credits added."}
    return {"status": "failed", "message": "Transaction invalid or failed"}

@app.websocket("/ws/research")
async def websocket_endpoint(websocket: WebSocket):
    # Step 0: Always accept first so we can communicate errors over the socket
    await manager.connect(websocket)

    # Step 1: Authenticate via query param token
    try:
        user = await get_ws_user(websocket)
        if not user:
            # Send explicit error before closing
            await manager.send_personal_message(
                json.dumps({"status": "⚠️ Session expired. Please sign in again.", "stage": "error", "result": "Unauthorized"}),
                websocket
            )
            await websocket.close(code=4001)
            manager.disconnect(websocket)
            return
        
        user_id = user["id"]
        print(f"✅ WS Client connected: {user['email']}")
    except Exception as e:
        print(f"❌ WS Handshake logic failed: {e}")
        await websocket.close(code=1011) # Internal error
        manager.disconnect(websocket)
        return

    try:
        while True:
            data = await websocket.receive_json()
            query          = data.get("query", "")
            request_format = data.get("format", "detailed report")

            if not query.strip():
                await manager.send_personal_message(
                    json.dumps({"status": "Error", "stage": "error", "result": "Query cannot be empty."}),
                    websocket
                )
                continue

            # Stream research pipeline updates back to user
            async for status_update in execute_pipeline(query, user_id, request_format):
                await manager.send_personal_message(status_update, websocket)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
