import os
import urllib.parse
from db.redis_client import redis_client

UPI_ID = os.getenv("UPI_ID", "agent.payment@upi")
UPI_NAME = os.getenv("UPI_NAME", "Research Agent Pro")

async def get_payment_qr(amount: float, transaction_note: str = "Upgrade Plan"):
    """
    Generates a UPI payment URL and a QR code image link via Google Charts API.
    """
    upi_payload = f"upi://pay?pa={UPI_ID}&pn={urllib.parse.quote(UPI_NAME)}&am={amount}&cu=INR&tn={urllib.parse.quote(transaction_note)}"
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={urllib.parse.quote(upi_payload)}"
    return {
        "upi_url": upi_payload,
        "qr_image_url": qr_url
    }

async def initialize_user_if_needed(user_id: str):
    plan = await redis_client.get(f"user:{user_id}:plan")
    if not plan:
        # Default to free tier with 3 queries
        await redis_client.set(f"user:{user_id}:plan", "free")
        await redis_client.set(f"user:{user_id}:queries_left", 3)
        
async def check_credits(user_id: str) -> bool:
    await initialize_user_if_needed(user_id)
    plan = await redis_client.get(f"user:{user_id}:plan")
    if plan == "free":
        left = await redis_client.get(f"user:{user_id}:queries_left")
        if left and int(left) > 0:
            return True
        return False
    else:
        # paid plan, check credit balance
        credits = await redis_client.get(f"user:{user_id}:credits")
        if credits and int(credits) > 0:
            return True
        return False

async def deduct_credits(user_id: str):
    plan = await redis_client.get(f"user:{user_id}:plan")
    if plan == "free":
        await redis_client.decr(f"user:{user_id}:queries_left")
    else:
        await redis_client.decr(f"user:{user_id}:credits")

async def upgrade_user_plan(user_id: str, plan_type: str, credits: int):
    """
    Called by the webhook to upgrade a user after payment success.
    """
    await redis_client.set(f"user:{user_id}:plan", plan_type)
    await redis_client.set(f"user:{user_id}:credits", credits)
