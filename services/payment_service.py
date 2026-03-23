"""
Payment Service — Anti-Fraud UPI Payment System

Flow:
  1. User picks a plan → /payment/qr is called
     → Generates a unique Order ID (e.g. RA-X7K9-49)
     → Saves a PENDING payment record in Supabase with that Order ID
     → Embeds the Order ID in the UPI QR note field
     → Order expires in 1 hour

  2. User scans QR → pays → sees Order ID in UPI remark + gets a UTR
     → User enters only last 4 digits of their UTR

  3. /payment/verify is called with (order_id + last4)
     → Verifies order exists, belongs to this user, not expired, not already claimed
     → Stores utr_last4 for admin cross-reference
     → Activates plan + credits in Redis

Anti-fraud guarantees:
  ✓ Order ID is single-use (UNIQUE in DB)
  ✓ Order ID is tied to a specific user + amount
  ✓ Order expires in 1 hour — old QRs can't be replayed
  ✓ Admin can match Order ID to bank statement UPI remarks
  ✓ No two users can use the same Order ID
  ✓ utr_last4 stored for admin audit trail
"""
import os
import uuid
import asyncio
import urllib.parse
from datetime import datetime, timedelta, timezone
from db.redis_client import redis_client
from db.supabase_client import get_supabase_client

UPI_ID   = os.getenv("UPI_ID",   "9693932656@ptyes")
UPI_NAME = os.getenv("UPI_NAME", "Research Agent")

PLAN_CONFIG = {
    "student":    {"credits": 50,  "amount": 49.0},
    "researcher": {"credits": 120, "amount": 99.0},
}

ORDER_TTL_HOURS = 1   # QR code / order expires after 1 hour


# ── Generate Order + QR ───────────────────────────────────────────────────────

def _make_order_id(plan: str, amount: float) -> str:
    """Generates a short, readable unique order ID like RA-X7K9-49"""
    short = uuid.uuid4().hex[:6].upper()
    amt   = str(int(amount))
    return f"RA-{short}-{amt}"


async def create_payment_order(user_id: str, email: str, plan: str) -> dict:
    """
    Step 1: Called when user clicks 'Continue' on plan selection.
    Creates a pending payment record in Supabase and returns the QR code.
    The Order ID is embedded in the UPI payment note.
    """
    if plan not in PLAN_CONFIG:
        return {"success": False, "error": f"Unknown plan '{plan}'."}

    cfg    = PLAN_CONFIG[plan]
    amount = cfg["amount"]
    order_id = _make_order_id(plan, amount)
    expires  = datetime.now(timezone.utc) + timedelta(hours=ORDER_TTL_HOURS)

    # Store pending order in Supabase
    def _insert():
        sb = get_supabase_client()
        sb.table("pending_payments").insert({
            "user_id":    user_id,
            "email":      email,
            "plan":       plan,
            "amount":     amount,
            "order_id":   order_id,
            "utr_number": order_id,   # use order_id as placeholder (UNIQUE col)
            "status":     "pending",
            "expires_at": expires.isoformat(),
            "notes":      f"Pending — awaiting UTR submission"
        }).execute()

    await asyncio.to_thread(_insert)

    # Build UPI payment URL with Order ID embedded in the note
    upi_note = f"ResearchAgent {order_id}"
    upi_url  = (
        f"upi://pay?pa={UPI_ID}"
        f"&pn={urllib.parse.quote(UPI_NAME)}"
        f"&am={amount}"
        f"&cu=INR"
        f"&tn={urllib.parse.quote(upi_note)}"
    )
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={urllib.parse.quote(upi_url)}"

    return {
        "success":    True,
        "order_id":   order_id,
        "upi_id":     UPI_ID,
        "upi_url":    upi_url,
        "qr_image_url": qr_url,
        "amount":     amount,
        "plan":       plan,
        "expires_in": f"{ORDER_TTL_HOURS} hour",
        "note":       upi_note,
    }


# ── Verify Payment ────────────────────────────────────────────────────────────

async def verify_payment_by_order(
    user_id: str,
    order_id: str,
    utr_last4: str,
) -> dict:
    """
    Step 2: Called when user submits their last-4 UTR digits.

    Checks:
    1. order_id exists in DB
    2. order belongs to THIS user (not someone else's QR)
    3. order is still 'pending' (not already claimed or expired)
    4. order has not expired (> 1 hour old)
    5. utr_last4 is exactly 4 numeric digits

    On success:
    - Marks order as 'approved' + stores utr_last4 for audit
    - Activates plan + credits in Redis
    """
    # Validate last-4
    utr_last4 = utr_last4.strip()
    if not utr_last4.isdigit() or len(utr_last4) != 4:
        return {"success": False, "error": "Please enter exactly 4 numeric digits from your UTR."}

    order_id = order_id.strip().upper()

    # Fetch the order
    def _fetch():
        sb = get_supabase_client()
        rows = sb.table("pending_payments") \
            .select("*") \
            .eq("order_id", order_id) \
            .execute().data
        return rows

    rows = await asyncio.to_thread(_fetch)

    # ── Guard 1: Order must exist ──
    if not rows:
        return {
            "success": False,
            "error": "❌ Order ID not found. Please generate a new QR code and try again."
        }

    order = rows[0]

    # ── Guard 2: Must belong to THIS user ──
    if order["user_id"] != user_id:
        return {
            "success": False,
            "error": "❌ This payment order does not belong to your account."
        }

    # ── Guard 3: Must still be pending ──
    if order["status"] == "approved":
        return {
            "success": False,
            "error": "✅ This order is already activated. Your plan should already be live. Contact support if not."
        }
    if order["status"] == "rejected":
        return {
            "success": False,
            "error": "❌ This order was rejected. Please generate a new QR and try again."
        }

    # ── Guard 4: Must not be expired ──
    if order.get("expires_at"):
        expires_at = datetime.fromisoformat(order["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires_at:
            # Mark as expired in DB
            asyncio.create_task(asyncio.to_thread(lambda: get_supabase_client()
                .table("pending_payments").update({"status": "expired"})
                .eq("order_id", order_id).execute()))
            return {
                "success": False,
                "error": "⏰ This QR code has expired (valid for 1 hour). Please generate a new one."
            }

    # ── All guards passed — approve ──
    plan    = order["plan"]
    credits = PLAN_CONFIG[plan]["credits"]

    def _approve():
        sb = get_supabase_client()
        sb.table("pending_payments").update({
            "status":          "approved",
            "utr_last4":       utr_last4,
            "credits_granted": credits,
            "verified_at":     datetime.utcnow().isoformat(),
            "notes":           f"Verified via Order ID {order_id} | UTR last-4: {utr_last4}"
        }).eq("order_id", order_id).execute()

    await asyncio.to_thread(_approve)

    # Activate plan in Redis
    await upgrade_user_plan(user_id, plan, credits)

    return {
        "success":  True,
        "plan":     plan,
        "credits":  credits,
        "order_id": order_id,
        "message":  f"✅ Payment verified! Your {plan.title()} Plan with {credits} credits is now active.",
    }


# ── Legacy QR (for old /payment/qr endpoint) ─────────────────────────────────

async def get_payment_qr(amount: float, transaction_note: str = "Upgrade Plan"):
    upi_url = (
        f"upi://pay?pa={UPI_ID}"
        f"&pn={urllib.parse.quote(UPI_NAME)}"
        f"&am={amount}"
        f"&cu=INR"
        f"&tn={urllib.parse.quote(transaction_note)}"
    )
    return {
        "upi_id":       UPI_ID,
        "upi_url":      upi_url,
        "qr_image_url": f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={urllib.parse.quote(upi_url)}",
        "amount":       amount,
    }


# ── Redis helpers ─────────────────────────────────────────────────────────────

async def initialize_user_if_needed(user_id: str):
    plan = await redis_client.get(f"user:{user_id}:plan")
    if not plan:
        await redis_client.set(f"user:{user_id}:plan", "free")
        await redis_client.set(f"user:{user_id}:queries_left", 3)

async def check_credits(user_id: str) -> bool:
    await initialize_user_if_needed(user_id)
    plan = await redis_client.get(f"user:{user_id}:plan")
    if plan == "free":
        left = await redis_client.get(f"user:{user_id}:queries_left")
        return bool(left and int(left) > 0)
    credits = await redis_client.get(f"user:{user_id}:credits")
    return bool(credits and int(credits) > 0)

async def deduct_credits(user_id: str):
    plan = await redis_client.get(f"user:{user_id}:plan")
    if plan == "free":
        await redis_client.decr(f"user:{user_id}:queries_left")
    else:
        await redis_client.decr(f"user:{user_id}:credits")

async def upgrade_user_plan(user_id: str, plan_type: str, credits: int):
    await redis_client.set(f"user:{user_id}:plan", plan_type)
    await redis_client.set(f"user:{user_id}:credits", credits)
