import streamlit as st
import requests
import cloudscraper
from bs4 import BeautifulSoup
import io
import time
import os
import re
import json
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright
import ollama
import uuid
from supabase import create_client, Client
import modal
from datetime import datetime
import openai
from openai import OpenAI
import time

if 'start_time' not in st.session_state:
    st.session_state.start_time = None
# --- CLOUD DATABASE SETUP ---
try:
    supabase_url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
    supabase_key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
    supabase: Client = create_client(supabase_url, supabase_key)
except Exception as e:
    st.error(f" Database connection failed: {e}")
    st.stop()

MODEL = "openai/gpt-oss-20b"

# Ensure we have a session ID
if "session_id" not in st.session_state:
    current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    st.session_state.session_id = f"{current_time}_{str(uuid.uuid4())}"

# --- 1. PAGE SETUP & UI ---
st.set_page_config(page_title="Argument Flow", layout="wide")
st.title(" My Evidence Tracker & Scraper")

# --- CUSTOM CSS ---
st.markdown(
    """
    <style>
        /* This sets the sidebar width to a custom pixel size */
        [data-testid="stSidebar"] {
            min-width: 450px;
            max-width: 450px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# Safe topic fallback handling
topic = st.session_state.get("saved_topic", "Nuclear power deployment and energy policy")

# --- Base Prompts ---
base_verfication_prompt = """Task: Does the following academic paper evaluate, discuss, or contain evidence regarding nuclear power, nuclear energy, small modular reactors (SMRs), energy policy, public perception, economics, or nuclear risk/governance?

Guidelines:
- Respond 'YES' if the paper touches on nuclear power, reactors, nuclear policy, public perception, economics, risk, or clean energy transition scenarios.
- Respond 'NO' only if the paper is completely unrelated to nuclear power (e.g., pure medicine, cell biology, software algorithms, or non-nuclear renewables).

Respond with exactly one word: 'YES' or 'NO'. Do not include punctuation or explanations.
Title: {title}
Abstract: {abstract}
Response:"""

base_anchor_search_input = '("nuclear power" OR "nuclear energy" OR "small modular reactor" OR "SMR") AND ("meta-analysis" OR "systematic review" OR "techno-economic" OR "comprehensive review") NOT ("cellular" OR "genome" OR "protein" OR "patient" OR "clinical" OR "parton" OR "quantum")'


# Initialize prompt in session state if not present
if "generated_prompt" not in st.session_state:
    st.session_state.generated_prompt = base_verfication_prompt

if "generated_filter" not in st.session_state:
    st.session_state.generated_filter = base_anchor_search_input



# Notice double brackets {{title}} and {{abstract}} so .format(topic=topic) doesn't throw a KeyError!
verfication_prompt_maker_prompt_template = """You are an expert prompt engineer. Your task is to rewrite the verification prompt below so it is directly related to this new topic: "{topic}"

INSTRUCTIONS:
1. You MUST keep the exact placeholders {{title}} and {{abstract}} intact at the bottom.
2. Maintain the strict 'YES' or 'NO' logic and formatting.
3. Replace all nuclear-specific references with detailed, highly specific sub-topics related directly to "{topic}".
4. DO NOT output any of these instructions. Output ONLY the finalized prompt ready to be used. Zero conversational filler.
5. Keep the primary topic broad. DO NOT add excessive "AND" clauses for specific countries, regions, or hyper-specific sub-niches. Focus only on the core technology or concept.
6. Make sure that your prompt makes it clear that any paper on topic whether it agrees with the topic or not should be let through we want a wide net anything on topic sohuld get a YES.

ORIGINAL PROMPT TO REWRITE:
Task: Does the following academic paper evaluate, discuss, or contain evidence regarding nuclear power, nuclear energy, small modular reactors (SMRs), energy policy, public perception, economics, or nuclear risk/governance?

Guidelines:
- Respond 'YES' if the paper touches on nuclear power, reactors, nuclear policy, public perception, economics, risk, or clean energy transition scenarios. Reaspond yes if the paper has to do with the topic at all it doesnt matter if it agree with the topic or not, just that it is relivant.
- Respond 'NO' only if the paper is completely unrelated to nuclear power (e.g., pure medicine, cell biology, software algorithms, or non-nuclear renewables).

Respond with exactly one word: 'YES' or 'NO'. Do not include punctuation or explanations.
Title: {{title}}
Abstract: {{abstract}}
Response:"""

base_anchor_search_input_prompt_template = """You are an expert academic data scientist. Your task is to generate a strict Boolean search query for an API database based on the topic: "{topic}".

INSTRUCTIONS:
1. The query MUST be formatted exactly like the example below, using standard Boolean operators (AND, OR, NOT) in ALL CAPS.
2. You MUST include a mandatory AND section filtering for meta-analyses, systematic reviews, or comprehensive reviews.
3. You MUST include a NOT section to aggressively filter out irrelevant scientific disciplines (e.g., medicine, biology, or unrelated physics terms).
4. DO NOT output any explanations, formatting ticks, or conversational filler. Output ONLY the raw Boolean string.

EXAMPLE FORMAT TO EMULATE:
("nuclear power" OR "nuclear energy" OR "small modular reactor" OR "SMR") AND ("meta-analysis" OR "systematic review" OR "techno-economic" OR "comprehensive review") NOT ("cellular" OR "genome" OR "protein" OR "patient" OR "clinical" OR "parton" OR "quantum")

RAW QUERY OUTPUT:"""
with st.sidebar:
    user_groq_key = st.text_input("Groq API Key", type="password")

    
    if user_groq_key:
        client = OpenAI(api_key=user_groq_key, base_url="https://api.groq.com/openai/v1", timeout=30.0)

    st.header(" Scraper Settings")

    MAX_ANCHOR_PAPERS = st.slider("Meta-Analyses (Phase 1)", min_value=1, max_value=20, value=10)
    MAX_PAPERS_PER_KEYWORD = st.slider("Papers per keyword (Phase 2)", min_value=1, max_value=20, value=10)
    MAX_THREADS = st.slider("Max Threads", min_value=1, max_value=20, value=15)

    

    if st.button("IMPORTANT: Auto-Generate AI Prompt"):
        if not user_groq_key:
            st.error("Please enter a Groq API Key first!")
        else:
            with st.spinner("Crafting topic-specific verification prompt..."):
                verfication_prompt_maker_prompt = verfication_prompt_maker_prompt_template.format(topic=topic)
                
                try:
                    niche_response = client.chat.completions.create(
                        model=MODEL, 
                        messages=[{"role": "user", "content": verfication_prompt_maker_prompt}]
                    )
                    ai_prompt_text = niche_response.choices[0].message.content.strip()
                    st.session_state.generated_prompt = ai_prompt_text
                    st.success("Prompt generated for topic!")
                    st.rerun()

                except openai.RateLimitError as e:
                    error_str = str(e)
                    match = re.search(r"try again in\s+([^.]+)", error_str, re.IGNORECASE)
                    wait_info = f"\n\n⏱️ **Time until reset / retry:** `{match.group(1).strip()}`" if match else ""
                    st.error(f"⚠️ **Daily Token Limit Reached!**{wait_info}")
                    st.stop()
                except Exception as e:
                    st.error(f"Error generating prompt: {e}")

    with st.expander("Advanced: Edit Verification Prompt"):
        llama_prompt_template = st.text_area(
            "Llama Verification Prompt", 
            value=st.session_state.generated_prompt,
            height=250
        )
    if st.button("IMPORTANT: Auto-Generate AI Anchor"):
        if not user_groq_key:
            st.error("Please enter a Groq API Key first!")
        else:
            with st.spinner("Crafting topic-specific anchor filter..."):
                base_anchor_search_input_prompt = base_anchor_search_input_prompt_template.format(topic=topic)
                try:
                    niche_response = client.chat.completions.create(
                        model=MODEL, 
                        messages=[{"role": "user", "content": base_anchor_search_input_prompt}]
                    )
                    ai_prompt_text = niche_response.choices[0].message.content.strip()
                    st.session_state.generated_filter = ai_prompt_text
                    st.success("Anchor generated for topic!")
                    st.rerun()

                except openai.RateLimitError as e:
                    error_str = str(e)
                    match = re.search(r"try again in\s+([^.]+)", error_str, re.IGNORECASE)
                    wait_info = f"\n\n⏱️ **Time until reset / retry:** `{match.group(1).strip()}`" if match else ""
                    st.error(f"⚠️ **Daily Token Limit Reached!**{wait_info}")
                    st.stop()
                except Exception as e:
                    st.error(f"Error generating prompt: {e}")

    with st.expander("Advanced: Phase 1 Anchor Filter Settings"):
        anchor_search_input = st.text_area(
            "Phase 1 Search Query", 
            value=st.session_state.generated_filter,
            height=120,
            help="This goes to OpenAlex's search endpoint where full Booleans and NOT exclusions work."
        )
        anchor_filter_input = st.text_area(
            "Phase 1 OpenAlex Filter Tags", 
            value='has_pdf_url:true',
            height=60,
            help="Simple tags only (e.g., has_pdf_url:true)."
        )

    default_sectors_json = """{
    "economics_and_finance": {
        "journals": ["Quarterly Journal of Economics", "American Economic Review", "Journal of Political Economy", "Journal of Public Economics"],
        "issns": ["0033-5533", "0002-8282", "0022-3808", "0047-2727"]
    },
    "energy_and_environment": {
        "journals": ["Energy Policy", "Joule", "Nature Energy", "Nature Climate Change"],
        "issns": ["0301-4215", "2542-4351", "2058-7546", "1758-678X"]
    },
    "infrastructure_and_transport": {
        "journals": ["Transport Policy", "Transportation Research Part A", "Journal of Public Economics"],
        "issns": ["0967-070X", "0965-8564", "0047-2727"]
    },
    "security_and_foreign_policy": {
        "journals": ["International Security", "Foreign Affairs", "Security Studies"],
        "issns": ["0162-2889", "0015-7120", "0963-6412"]
    }
}"""

    with st.expander("Advanced: Edit Journal Whitelist & Sectors (JSON)"):
        sectors_json_str = st.text_area(
            "Debate Sectors Catalog (JSON format)",
            value=default_sectors_json,
            height=200
        )
        try:
            DEBATE_SECTORS = json.loads(sectors_json_str)
        except Exception as json_err:
            st.error(" Invalid JSON Format! Falling back to default journal configurations.")
            DEBATE_SECTORS = json.loads(default_sectors_json)

# Keyword Management UI
selected_keywords = []
with st.spinner("Fetching keywords from cloud database..."):
    response = supabase.table("user_keywords").select("keyword").eq("session_id", st.session_state.session_id).execute()

    for r in response.data:
        keyword = r["keyword"]
        selected_keywords.append(keyword)

if len(selected_keywords) >= 1:
    st.success(f"Found {len(selected_keywords)} keywords from the cloud!")
else:
    st.warning("No keywords found. You must generate keywords first.")
    st.stop()

st.divider()

# --- MAIN PIPELINE EXECUTION ---
if "is_running" not in st.session_state:
    st.session_state.is_running = False

# 2. Dynamic button placeholder
button_placeholder = st.empty()

# ==========================================
# STATE A: IDLE (SHOW START BUTTON)
# ==========================================
if not st.session_state.is_running:
    if button_placeholder.button(" Start Scraping Pipeline", type="primary"):
        if not selected_keywords:
            st.error("You must have at least one keyword checked to start scraping!")
            st.stop()
        if not user_groq_key:
            st.error(" Please provide a Groq API Key to run the scraper!")
            st.stop()

        st.session_state.start_time = time.time()

        # Update Supabase to 'running'
        supabase.table("run_status").upsert({
            "session_id": st.session_state.session_id, 
            "status": "running"
        }).execute()
        
        # Swap button state and reload UI
        st.session_state.is_running = True
        st.rerun()

# ==========================================
# STATE B: RUNNING (SHOW STOP BUTTON)
# ==========================================
else:
    # Button is now swapped to the Emergency Stop!
    if button_placeholder.button("🚨 Emergency Stop Pipeline", use_container_width=True):
        # Update Supabase to 'killed'
        if st.session_state.start_time:
            elapsed = time.time() - st.session_state.start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            st.warning(f"Pipeline aborted. Total runtime: {mins}m {secs}s")
        st.session_state.is_running = False
        st.session_state.start_time = None

        supabase.table("run_status").upsert({
            "session_id": st.session_state.session_id, 
            "status": "killed"
        }).execute()
        
        st.session_state.is_running = False
        st.error("🛑 Kill signal sent! Cloud workers are safely shutting down.")
        st.stop()

    if not selected_keywords:
        st.error("You must have at least one keyword checked to start scraping!")
        st.stop()

    if not user_groq_key:
        st.error(" Please provide a Groq API Key to run the scraper!")
        st.stop()

    scraper_worker = modal.Function.from_name("debate-scraper", "run_full_pipeline")

    st.info(" Job sent to the cloud! Modal is spinning up server workers right now.")
    
    sub_header_phase_1 = st.empty()
    status_log_phase_1 = st.empty()
    progress_bar_phase_1 = st.empty()
    progress_text_phase_1 = st.empty()

    sub_header_phase_2 = st.empty()
    status_log_phase_2 = st.empty()
    progress_bar_phase_2 = st.empty()
    progress_text_phase_2 = st.empty()
    
    for update in scraper_worker.remote_gen(
        keywords=selected_keywords,
        groq_api_key=user_groq_key,
        max_anchors=MAX_ANCHOR_PAPERS,
        max_papers=MAX_PAPERS_PER_KEYWORD,
        max_threads=MAX_THREADS,
        phase1_search=anchor_search_input,
        phase1_filter=anchor_filter_input,
        llama_template=llama_prompt_template,
        debate_sectors=DEBATE_SECTORS,
        session_id=st.session_state.session_id,
        supabase_url=supabase_url,
        supabase_key=supabase_key
    ):
        if isinstance(update, dict):
            
            if update["type"] == "error":
                st.error(update["message"])
                st.session_state.is_running = False # <--- PREVENTS THE TRAP!
                button_placeholder.empty()          # <--- CLEARS THE STOP BUTTON
                st.stop()
                
            elif update["type"] == "ui":
                if update["action"] == "divider":
                    st.divider()
                elif update["action"] == "subheader":
                    if update["phase"] == "1":
                        sub_header_phase_1.subheader(update["text"])
                    elif update["phase"] == "2":
                        sub_header_phase_2.subheader(update["text"])
                elif update["action"] == "info":
                    st.info(update["message"])
                elif update["action"] == "caption":
                    st.caption(update["text"])

            elif update["type"] == "log":
                if update["phase"] == "1":
                    status_log_phase_1.info(update["message"])
                elif update["phase"] == "2":
                    status_log_phase_2.info(update["message"])

            elif update["type"] == "progress":
                current = update["current"]
                total = update["total"]
                pct = min(current / total, 1.0) if total > 0 else 0.0

                if update["phase"] == "1":
                    progress_bar_phase_1.progress(pct)
                    msg = update.get("message", "Processing papers...")
                    progress_text_phase_1.text(f" {msg} ({current}/{total})")

                elif update["phase"] == "2":
                    progress_bar_phase_2.progress(pct)
                    msg = update.get("message", "Processing papers...")
                    progress_text_phase_2.text(f" {msg} ({current}/{total})")

            elif update["type"] == "done":
                if update["phase"] == "1":
                    progress_bar_phase_1.progress(1.0)
                    progress_text_phase_1.success(" Phase 1 Complete!")
                    status_log_phase_1.empty()

                elif update["phase"] == "2":
                    progress_bar_phase_2.progress(1.0)
                    progress_text_phase_2.success("★ Master Pipeline Complete!")
                    status_log_phase_2.empty()
                    if st.session_state.start_time:
                        elapsed = time.time() - st.session_state.start_time
                        mins = int(elapsed // 60)
                        secs = int(elapsed % 60)
                        st.warning(f"Pipeline complete!. Total runtime: {mins}m {secs}s")
                    st.session_state.is_running = False
                    st.session_state.start_time = None

    button_placeholder.empty()
    st.session_state.is_running = False