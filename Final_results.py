import json
import os
import streamlit as st
import time
import os
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import threading
import queue
import json
import streamlit as st
from supabase import Client, create_client
from bs4 import BeautifulSoup
import uuid
from datetime import datetime
from openai import OpenAI

papers = []
temp_papers = []
db_data = []
# --- CONFIGURATION ---
INPUT_FILE = "extracted_debate_cards.json"
SORTED_FILE = "sorted_debate_cards.json"
RAW_DB_FILE = "llm_ready_database.json"
PDF_DIR = "downloaded_pdfs"
try:
    supabase_url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
    supabase_key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
    supabase: Client = create_client(supabase_url, supabase_key)
except Exception as e:
    st.error(f" Database connection failed: {e}")
    st.stop()


st.set_page_config(page_title="Counterpoint", layout="wide")

st.title("Debate Evidence Ranking & Workspace")
st.caption("Sort, filter, and inspect your AI-graded academic cards for instant evidence cutting.")
st.divider()


if "session_id" not in st.session_state:
    current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    st.session_state.session_id = f"{current_time}_{str(uuid.uuid4())}"


session = st.session_state.session_id

try:
        file_bytes = supabase.storage.from_("paper_xmls").download(f"{session}/extracted_debate_cards.json")
        papers = json.loads(file_bytes.decode("utf-8"))
        
except Exception as e:
        st.warning("⚠️ No graded papers found in the cloud for this session! Please run your AI Grader pipeline first.")
        st.stop()

if not papers:
    st.warning("⚠️ No graded papers found in the cloud for this session! Please run your AI Grader pipeline first.")
    st.stop()
# ==========================================
# BACKEND: DEBATE-CENTRIC SORTING LOGIC
# ==========================================
def debate_sort_key(paper):
    scores = paper.get("scores", {}) # get the scores 
    stance = paper.get("overall_paper_stance", "Irrelevant")# get the paper stance
    is_useful = 0 if stance == "Irrelevant" else 1 # if the place where the paper stance would be is irreilvant set it to 0 or false
    
    # Extract the classic academic scores
    utility = scores.get("overall_utility_score", 0)
    relevance = scores.get("topic_relevance", 0)
    impact = scores.get("impact_magnitude", 0)
    methodology = scores.get("methodology_strength", 0)
    
    # Extract the new strategic debate scores (defaulting to 0 if viewing older files)
    lay = scores.get("lay_persuasiveness", 0)
    directness = scores.get("resolution_directness", 0)
    # extract all the scores that deepseek generated
    # --- DEBATE-FIRST FORMULA ---
    # We heavily prioritize:
    # 35% Lay Persuasiveness (Can a lay judge follow this?)
    # 30% Resolution Directness (Does it directly answer "Should we expand?")
    # 20% Academic Utility (Does it have good stats/cards?)
    # 15% Core Relevance (Is it on topic?)
    debate_composite_score = (lay * 0.35) + (directness * 0.30) + (utility * 0.20) + (relevance * 0.15) # take the 4 most important things and put that in our formula for the most important way we judge it (debate composit score)
    return (# returns the scores in the order that the teis will be broken in
        is_useful,                # Must be relevant to be at the top
        debate_composite_score,   # Main ranking factor
        directness,               # Secondary tie-breaker
        lay,                      # Tertiary tie-breaker
        utility,
        impact,
        methodology
    )

def execute_sorting():
    try:
        file_bytes = supabase.storage.from_("paper_xmls").download(f"{session}/extracted_debate_cards.json")
        papers = json.loads(file_bytes.decode("utf-8"))
        
    except Exception as e:
            st.error(f"[!] Could not find optimized database in cloud for this session. Run Context Optimizer first! Error: {e}")

    papers.sort(key=debate_sort_key, reverse=True)

    try:
            graded_json = json.dumps(papers, indent=4, ensure_ascii=False)
            
            # Upload the master list to the active session folder
            supabase.storage.from_("paper_xmls").upload(
                path=f"{session}/sorted_debate_cards.json",
                file=graded_json.encode("utf-8"),
                file_options={"content-type": "application/json", "upsert": "true"}
            )
            
    except Exception as e:
        st.error(f"[!] Failed to upload to Supabase: {e}") 
            
    
    return len(papers) 



# SIDEBAR: CONTROLS
# Allows the user to choosed how many papers to show and what side to show.
with st.sidebar:
    st.header(" Pipeline Controls")
    
    if st.button("Rank & Sort Cards", type="primary", use_container_width=True): 
        with st.spinner("Re-indexing database by utility..."):
            count = execute_sorting() 
            if count:
                st.success(f"Sorted {count} papers successfully!")
                st.toast("Database updated!", icon="🔥")
                
    st.divider()
    st.subheader(" Display Configuration")
    try:
        file_bytes2 = supabase.storage.from_("paper_xmls").download(f"{session}/sorted_debate_cards.json")
        temp_papers = json.loads(file_bytes2.decode("utf-8"))
        
    except Exception as e:
            st.error(f"[!] Could not find optimized database in cloud for this session. Run Context Optimizer first! Error: {e}")

    if temp_papers:
        total_papers = len(temp_papers)
        
        if total_papers > 1:
            num_to_show = st.slider( 
                "Show Top Ranked Papers", 
                min_value=1, 
                max_value=total_papers, 
                value=min(3, total_papers)
            )
        else:
            num_to_show = total_papers
            st.info("Showing the only graded paper in your database.")

        selected_stance = st.selectbox("Choose a stance for what type of papers you want to see", ["All", "Proposition", "Opposition"])
    else:
        st.info("Sort cards to enable the viewer configuration.")
        num_to_show = 0
        selected_stance = "All"
        st.info("👈 Your database is ready! Please click 'Rank & Sort Cards' in the sidebar to initialize your workspace.")
        st.stop()



# BACKGROUND DATA MAPPING

papers = temp_papers
database_by_title = {}
try:
        file_bytes = supabase.storage.from_("paper_xmls").download(f"{session}/llm_ready_database.json")
        db_data = json.loads(file_bytes.decode("utf-8"))
        
        
except Exception as e:
    st.error(f"[!] Could not find optimized database in cloud for this session. Run Context Optimizer first! Error: {e}")
    
 # we gotta take from the LLM ready data base to get titles so we dont rely on AI titles.
if db_data:
    for entry in db_data:
        clean_title_key = "".join(c for c in entry.get("title", "").lower() if c.isalnum())
        database_by_title[clean_title_key] = entry
    

# ==========================================
# MAIN WORKSPACE DISPLAY
# ==========================================


st.write(f"### Showing Top {num_to_show} of {len(papers)} Graded Academic Papers") # tell the user how many we are  dis
st.caption("Heavily prioritizing lay persuasiveness, resolution directness, and in-round strategic utility.")
st.divider()

selected_stance_papers = []


if selected_stance == "Proposition":
    for paper in papers:
        stance = paper.get("overall_paper_stance", "Unknown Stance")
        if stance == "Proposition" or stance == "Mixed":
            selected_stance_papers.append(paper)

elif selected_stance == "Opposition":
    for paper in papers:
        stance = paper.get("overall_paper_stance", "Unknown Stance")
        if stance == "Opposition" or stance == "Mixed":
            selected_stance_papers.append(paper)
else: selected_stance_papers = papers

for idx, paper in enumerate(selected_stance_papers[:num_to_show], start=1):
    title = paper.get("paper_title", "Untitled Paper")
    stance = paper.get("overall_paper_stance", "Unknown Stance")
    brief = paper.get("strategy_brief", "No strategy brief provided.")
    strength = paper.get("strength_explanation", "")
    
    scores = paper.get("scores", {})
    utility = scores.get("overall_utility_score", 0)
    relevance = scores.get("topic_relevance", 0)
    impact = scores.get("impact_magnitude", 0)
    methodology = scores.get("methodology_strength", 0)
    
    # Pull new dynamic values (safely)
    lay = scores.get("lay_persuasiveness", 0) 
    directness = scores.get("resolution_directness", 0)
    
    # Calculate and display a display-only weighted Debate score
    debate_score = (lay * 0.35) + (directness * 0.30) + (utility * 0.20) + (relevance * 0.15)
    
    st.markdown(f"#### {idx}. {title}")
    
    # Row 1: Academic Rigor Metrics
    m1, m2, m3 = st.columns(3) 
    m1.metric("Overall Utility", f"{utility}/10")
    m2.metric("Topic Relevance", f"{relevance}/10")
    m3.metric("Impact Magnitude", f"{impact}/10")
    
    # Row 2: Debate-Centric Strategic Metrics
    m4, m5, m6 = st.columns(3) 
    m4.metric("Methodology", f"{methodology}/10")
    m5.metric("Lay Persuasiveness", f"{lay}/10")
    m6.metric("Directness", f"{directness}/10")
    
    # Display the custom calculated Debate Composite Score
    st.write(f"🏆 **Debate-First Composite Score:** `{debate_score:.2f}/10.00`") 
    st.markdown(f"**Core Stance:** `{stance}`")
    st.write(brief)
    
    if strength:
        st.markdown(f"ℹ️ *{strength}*")
    
    # 1. Expandable Debate Cards
    cards = paper.get("debate_cards", []) 
    with st.expander(f"View Cut Cards ({len(cards)})"):
        if not cards:
            st.write("*No cards extracted for this item (classified as low relevance or technical).*") 
        else:
            for card in cards:
                card_stance = card.get("card_stance", "Stance Unknown")
                tagline = card.get("tagline", "No Tagline")
                quote = card.get("verbatim_quote", "")
                
                st.markdown(f"👉 **[{card_stance}] {tagline}**")
                st.info(quote)
    
    # 2. Dynamic Full Paper Viewer & PDF Downloader
    clean_paper_title = "".join(c for c in title.lower() if c.isalnum())
    db_entry = database_by_title.get(clean_paper_title)
    
    # this is where we get the full paper from the databse so the user cna read it if they choose.
    if db_entry:
        with st.expander(" Read Full Extracted Paper"):
            st.markdown(f"### {title}")
            st.markdown("**Abstract:**")
            st.write(db_entry.get("abstract", "No abstract provided."))
            st.markdown("---")
            st.markdown("**Cleaned Full-Text Body (With Injected Citations):**")
            
            
            st.text_area(
                label="Cleaned Body Text",
                value=db_entry.get("body", "No text body available."),
                height=350,
                disabled=True,
                key=f"text_area_{idx}"
            )
            
            # 1. Figure out the filename (INDENTED TO MATCH st.text_area)
            pdf_filename = db_entry.get("filename", "").replace("_output.xml", ".pdf")
            
            # 2. Ask Supabase for a temporary link
            try:
                # Creates a link valid for 3600 seconds (1 hour)
                signed_url_res = supabase.storage.from_("paper_pdfs").create_signed_url(f"{session}/{pdf_filename}", 3600)
                pdf_url = signed_url_res.get("signedURL")
                
                if pdf_url:
                    # Use a link button instead of a download button
                    st.link_button("📥 Open Original PDF in Browser", pdf_url)
                    
            except Exception as e:
                st.caption("ℹ️ Cloud PDF file not found. Full text above is parsed from the XML database.")

    st.divider()