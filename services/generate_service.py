"""
Generate Service — Deep Content Edition

Features:
  - Smart Priority: Uses Groq 70B for deep formats (Research/Reports), HF for fast ones.
  - Multi-Token Budget: Allows up to 4,096 tokens of output (approx. 3,000 words).
  - Anti-Truncate Instructions: Explicitly commands verbosity per section.
"""
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

# ── Model Config ──────────────────────────────────────────────────────────────

HF_MODELS = [
    "mistralai/Mistral-7B-Instruct-v0.3",
    "HuggingFaceH4/zephyr-7b-beta",
    "Qwen/Qwen2.5-7B-Instruct",
]

GROQ_MODELS = [
    "llama-3.3-70b-versatile",   # Primary for Elaborate Content
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
]

HF_API_BASE = "https://api-inference.huggingface.co/v1/chat/completions"

# ── Elaborate Token Budgets ───────────────────────────────────────────────────
# (max_input, max_output)
# Setting output to 4096 (max possible for most free APIs)
MODEL_BUDGETS = {
    "llama-3.3-70b-versatile": (10000, 4096), # 128k context, huge output
    "llama-3.1-70b-versatile": (10000, 4096),
    "mistralai/Mistral-7B-Instruct-v0.3": (6000, 3000),
    "llama-3.1-8b-instant": (6000, 2500),
    "default": (4000, 2000),
}

def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def _trim_context(parts: list[str], max_input: int, overhead: int) -> str:
    budget = max_input - overhead
    selected, used = [], 0
    for part in parts:
        toks = _estimate_tokens(part)
        if used + toks > budget:
            chars = (budget - used) * 4
            if chars > 400:
                selected.append(part[:chars] + "\n...[trimmed context]")
            break
        selected.append(part)
        used += toks
    return ("\n\n" + "─" * 40 + "\n\n").join(selected)

# ── Exhaustive Format Instructions ───────────────────────────────────────────

FORMAT_PROMPTS = {
    "detailed report": (
        "Write an EXHAUSTIVE, IN-DEPTH research report. Each section must be at least 3-4 paragraphs long.\n"
        "# [Title]\n## 1. Executive Summary\n## 2. Comprehensive Background\n"
        "## 3. Detailed Findings (at least 5 detailed subsections with specific data)\n"
        "### 📊 Key Statistics & Trends (Include a detailed table or Mermaid chart)\n"
        "## 4. Deep Analysis & Strategic Implications\n## 5. Challenges & Roadblocks\n"
        "## 6. Future Projections (data-backed)\n## 7. Extended Conclusion\n## 8. References\n"
        "USE MAXIMUM VERBOSITY. Provide deep explanations. Cite [Source X] for every claim."
    ),
    "research paper": (
        "Develop a complete, multi-page ACADEMIC RESEARCH PAPER. Aim for maximal depth.\n"
        "# [Title]\n**Abstract** (Detailed explanation of scope)\n"
        "## 1. Introduction (Historical context, Problem Statement, Methodology used)\n"
        "## 2. Comprehensive Literature Review (Group findings by theme, compare sources)\n"
        "## 3. Results & Quantitative Analysis (Highlight statistics, cite [Source X])\n"
        "## 4. Nuanced Discussion (Interpretations, broader impact, secondary effects)\n"
        "## 5. Critical Limitations & Scope Gaps\n"
        "## 6. Formal Conclusions (numbered, evidence-based)\n## 7. Detailed References\n"
        "Formal academic tone. DO NOT TRUNCATE. Be exhaustive."
    ),
    "comparison table": (
        "Generate a DETAILED COMPARISON. Must include multiple tables and deep analysis.\n"
        "# Comparison: [Topic]\n## 1. Landscape Overview\n"
        "## 2. Feature Comparison Matrix (Mandatory pipe-table, at least 10 rows)\n"
        "| Feature | Option A | Option B | Option C |\n|---|---|---|---|\n"
        "## 3. Pros & Cons Analysis (Deep dive per option)\n"
        "## 4. Cost/Performance Analysis Table\n"
        "## 5. Technical Verdict & Best Use Cases\n"
        "## 6. References\n"
        "Every table cell must contain specific data, not yes/no."
    ),
    "bullet point summary": (
        "Generate a MASSIVE BULLET SUMMARY. Group by deep themes.\n"
        "# Comprehensive Summary: [Topic]\n## 🎯 Key Takeaways (Top 8)\n"
        "## 📊 Extensive Facts & Stats Dashboard\n## ✅ Positives & Opportunities\n"
        "## ❌ Critical Risks & Threats\n## 📈 Market/Scientific Trends\n"
        "## 💡 Experts Insights\n## 🔮 Future Scenarios\n## Sources\n"
        "Every bullet should be a full, detailed sentence with a citation."
    ),
}

DEFAULT_FORMAT_PROMPT = (
    "Generate a highly detailed, elaborate response. Each section must be thorough. "
    "Do not provide brief summaries; provide deep analysis. Cite [Source X] always."
)

# ── Core LLM caller ───────────────────────────────────────────────────────────

async def _call_llm(client: httpx.AsyncClient, endpoint: str, key: str, model: str, messages: list, max_tokens: int) -> str:
    resp = await client.post(
        endpoint,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": 0.35, "max_tokens": max_tokens},
        timeout=150.0,
    )
    if resp.status_code == 429: raise Exception(f"429 rate-limited on '{model}'")
    if resp.status_code != 200: raise Exception(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]

# ── Main entry point ──────────────────────────────────────────────────────────

async def generate_report(query: str, format_type: str, ranked_chunks: list[dict], images: list[dict] = None) -> str:
    HF_KEY    = os.getenv("HF_API_KEY", "")
    GROQ_KEY  = os.getenv("GROQ_API_KEY", os.getenv("LLM_API_KEY", ""))
    GROQ_URL  = os.getenv("LLM_API_URL", "https://api.groq.com/openai/v1/chat/completions")
    
    if not ranked_chunks: return "No verifiable sources found."

    fmt_key   = format_type.lower().strip()
    fmt_instr = FORMAT_PROMPTS.get(fmt_key, DEFAULT_FORMAT_PROMPT)

    # Use Top 12 Chunks for context
    context_parts = [f"[Source {i+1}] {c.get('title','Unknown')}\nURL: {c.get('url','')}\n{c.get('text','')}" for i, c in enumerate(ranked_chunks[:12])]
    prompt_overhead = _estimate_tokens(fmt_instr) + _estimate_tokens(query) + 200

    hf_ready   = bool(HF_KEY and not HF_KEY.startswith("hf_YOUR"))
    groq_ready = bool(GROQ_KEY)

    # SMART PRIORITY:
    # If the format is 'Research Paper' or 'Detailed Report', put Groq 70B (Best Quality) FIRST.
    # Otherwise, put HF first (Fastest/Free).
    attempts = []
    
    is_deep_format = fmt_key in ["research paper", "detailed report"]

    # 1. Groq Chain
    groq_attempts = [(GROQ_URL, GROQ_KEY, m) for m in GROQ_MODELS]
    # 2. HF Chain
    hf_attempts = [(HF_API_BASE, HF_KEY, m) for m in HF_MODELS]

    if is_deep_format:
        attempts = groq_attempts + hf_attempts
    else:
        attempts = hf_attempts + groq_attempts

    async with httpx.AsyncClient() as client:
        for endp, key, model in attempts:
            if not key: continue
            provider = "Groq" if "groq" in endp else "HF"
            try:
                max_in, max_out = MODEL_BUDGETS.get(model, MODEL_BUDGETS["default"])
                trimmed_ctx     = _trim_context(context_parts, max_in, prompt_overhead)
                
                system_prompt = (
                    f"Format: '{format_type}'. MODE: EXHAUSTIVE VERBOSITY.\n\n"
                    f"{fmt_instr}\n\n"
                    "RULES:\n"
                    "- DO NOT summarize; write deep, multi-paragraph sections.\n"
                    "- Every claim must have an inline [Source X] citation.\n"
                    "- Use specific names, numbers, data points, and percentages.\n"
                    "- Format with ## headers, tables, and bold for key terms.\n"
                    "- 📊 IMPORTANT: Use Mermaid diagrams (```mermaid ... ```) for flows, hierarchies, or timelines where relevant. DO NOT USE [Source X] OR **BOLD** INSIDE MERMAID BLOCKS as it breaks the syntax.\n"
                    "- 📈 IMPORTANT: Always include at least one detailed markdown table for statistics or comparisons. Use the standard pipe `|` syntax for tables.\n"
                    "- 🖼️ IMPORTANT: Prioritize images from the 'ATTACHED IMAGES' section below. These are extracted directly from the research sources. Embed them using `![Source-Context Image](URL)` and explicitly describe how they relate to the text."
                )
                
                img_section = "\n\n=== ATTACHED IMAGES (Use these URLs to embed in report) ===\n"
                if images:
                    for img in images:
                        img_section += f"- Title: {img['title']}\n  URL: {img['url']}\n"
                else:
                    img_section += "(No images available for this query)"

                user_prompt = f"Research Query: {query}\n\n{img_section}\n\n=== VERIFIED SOURCE DATA ===\n{trimmed_ctx}\n\nGenerate the complete, elaborate '{format_type}' now."

                print(f"🤖 Generating elaborate output with [{provider}] {model}...")
                result = await _call_llm(client, endp, key, model, [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], max_tokens=max_out)
                return result

            except Exception as e:
                print(f"❌ [{provider}] {model} failed: {e}")
                continue

    return "⚠️ AI generation failed. No providers available."
