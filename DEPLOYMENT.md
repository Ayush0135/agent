# 🚀 Deployment Guide: Automated Research Agent

This guide outlines the steps to take your agent from local development to a globally accessible production environment.

## 📦 1. Pre-Deployment Readiness
We have already optimized the following for you:
- [x] **Dynamic API URL**: Frontend automatically detects its own origin via `window.location.origin`.
- [x] **Universal Dockerfile**: Respects the `$PORT` variable (required for Render/Railway).
- [x] **Performance**: GZip compression is enabled for smaller payloads.
- [x] **Cold Start Optimization**: Machine learning models are baked into the image.

---

## ☁️ 2. Recommended Hosting Platforms

### **Option A: Render.com (Easiest)**
1. Connect this GitHub repository to Render.
2. Choose **Web Service**.
3. Render will detect the `Dockerfile`.
4. Add all **Environment Variables** (see section 3).
5. (Optional) Add a **Disk** (Mount: `/app/local_research.db`) if you want to keep the local SQLite history persistent.

### **Option B: Fly.io (Best for Global Speed)**
1. Run `fly launch` in the root directory.
2. Fly.io will configure your `fly.toml`.
3. Set your secrets: `fly secrets set SUPABASE_URL=... REDIS_URL=...`
4. Deploy: `fly deploy`.

---

## 🔑 3. Environment Variables Registry
Ensure these are set in your cloud provider's dashboard:

| Variable | Source | Description |
|---|---|---|
| `SUPABASE_URL` | Supabase Dashboard | Main DB URL |
| `SUPABASE_KEY` | Supabase Dashboard | Anon/Public API Key |
| `REDIS_URL` | Redislabs / Upstash | For credit tracking & cache |
| `GOOGLE_API_KEY` | Google Console | For Search API |
| `SEARCH_ENGINE_ID` | Google Console | Programmable Search ID |
| `GROQ_API_KEY` | Groq Console | Primary LLM (Fast/Deep) |
| `HF_API_KEY` | Hugging Face | Fallback models |
| `UPI_ID` | Your Wallet | For payments (e.g. 9693932656@ptyes) |
| `UPI_NAME` | Your Name | Displayed on QR code |

---

## 🏗️ 4. Scalability Architecture
In a high-traffic production scenario, consider:
1. **Vertical Scaling (RAM)**: This app uses ~1GB RAM due to the local `SentenceTransformer` and `NLI` models. Ensure your server has at least **2GB RAM**.
2. **Horizontal Scaling**: If using multiple Docker instances, use a shared **Redis (Upstash/Redislabs)** so credit usage is synced across all servers.
3. **Database Persistence**: Move the SQLite history to a **Postgres/Supabase** table entirely to avoid managing Docker volumes for `.db` files.

---

## ✅ 5. Post-Deployment Verification
After deploying, visit your domain and verify:
1. **Auth**: Sign up as a new user (check if Supabase sends email).
2. **WebSocket**: Run a query and ensure the "Checking credits..." dot turns green instantly.
3. **Payments**: Generate a QR code to ensure `api.qrserver.com` fetches correctly.
4. **Downloads**: Test if the "Download PDF" button works correctly on the live domain.

**Ready for deployment? Just push to main!** 🚀
