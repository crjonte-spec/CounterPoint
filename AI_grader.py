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



st.set_page_config(page_title="Counterpoint", layout="wide")
st.title("Context Optimizer & AI Grader")

topic = st.session_state.get("saved_topic", "Nuclear power deployment and energy policy")

try:
    supabase_url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
    supabase_key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
    supabase: Client = create_client(supabase_url, supabase_key)
except Exception as e:
    st.error(f" Database connection failed: {e}")
    st.stop()

if "session_id" not in st.session_state:
    current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    st.session_state.session_id = f"{current_time}_{str(uuid.uuid4())}"



session = st.session_state.session_id


with st.sidebar:
    dry_run = st.checkbox("Dry Run Mode (Process 1 paper only)", value=True)
    st.divider()
    st.header("🤖 AI Provider & Model")
    
    PROVIDER_CONFIGS = {
        "DeepSeek (Recommended for Cards)": {
            "url": "https://api.deepseek.com", 
            "model": "deepseek-chat"
        },
        "Groq (Llama 3.3 70B - Ultra Fast)": {
            "url": "https://api.groq.com/openai/v1", 
            "model": "llama-3.3-70b-versatile"
        },
        "OpenAI (GPT-4o)": {
            "url": "https://api.openai.com/v1", 
            "model": "gpt-4o"
        },
        "OpenRouter (Any Model)": {
            "url": "https://openrouter.ai/api/v1", 
            "model": "anthropic/claude-3.5-sonnet"
        },
         "Meta (Together AI / Native)": {
        "url": "https://together.xyz", 
        "model": "muse-spark-1.3"
    }
    }
    
    selected_provider = st.selectbox("Choose AI Provider:", options=list(PROVIDER_CONFIGS.keys()))
    config = PROVIDER_CONFIGS[selected_provider]
    provider_url = config["url"]
    active_model = st.text_input("Model ID", value=config["model"])
    user_api_key = st.text_input("API Key", type="password").strip()
    
    

    with st.expander("Advanced: Edit System Prompts"):
        SYSTEM_PROMPT = st.text_area(
        "SYSTEM_PROMPT", 
        value="""
You are an elite, uncompromising policy debate coach and data validator. Your sole objective is to read academic papers and extract VERBATIM debate cards for the topic.

Input Variables:
1. DEBATE RESOLUTION: The specific policy topic being debated stated above.
2. PAPER TEXT: The cleaned text of an academic paper.

YOUR STRICT RULES FOR EXTRACTION:

RULE 1: NO HALLUCINATIONS UNDER ANY CIRCUMSTANCES.
- You must NOT invent, paraphrase, or generate text that does not exist in the PAPER TEXT.
- Every single character in the "verbatim_quote" must be a direct copy-paste from the PAPER TEXT.
- LANGUAGE REQUIREMENT: All extracted verbatim quotes MUST be in English. Do NOT extract quotes in foreign languages (e.g., Danish, German, French).

RULE 2: THE RELEVANCE THRESHOLD.
- Read the paper. If the paper is highly technical (e.g., theoretical subatomic physics, chemical isotope modeling, quantum mechanics, or structural stress testing of minor components) OR if it is not directly about the economic, environmental, grid reliability, waste management, weapons proliferation, safety, or geopolitical policy impacts of Nuclear Energy expansion, it is USELESS for debate.
- If the paper is useless, you MUST set the "overall_paper_stance" to "Irrelevant", score everything below a 5, and return an empty list `[]` for "debate_cards". 
- DO NOT invent a fake paper to satisfy the user.
- You must see both sides do not call a paper irrelicant just because it argues the otherside of a topic instead lable it as the other other side and grade it the same.

RULE 3: CARD CUTTING (Only if relevant).
- If the paper is highly relevant (Score 6+), extract 1 to 4 strategic quotes.
- Quotes must contain hard empirical data or strong causal warrants. 
- You MUST append the author and year tag at the end of the quote.
- You must pick the best most usful quotes and data from the papers for the topic.
- Even if a paper doesnt directly answer the question in the opic you should read it for data that could be usful to one side. EG in a debate manula vs automatic a bunch of stats about autmobile fatalities from the DMV might not directly answer the quesiton but it mihg have info on how many of the accidents involved manula vs automatic then you can extrapolate based on that that which is safer.

RULE 4: DEBATE-SPECIFIC METRICS.
You must grade the paper from 0 to 10 on the following six metrics:
1. "topic_relevance": How closely the paper's subject aligns with the resolution.
2. "impact_magnitude": The scale of the real-world impacts discussed (e.g., global climate vs local issues).
3. "methodology_strength": The scientific rigor, sample size, or causal identification of the study.
4. "lay_persuasiveness": Is this argument intuitive, compelling, and easy to explain to a non-expert judge in under 45 seconds? (Highly technical jargon drops this score).
5. "resolution_directness": Does this paper directly answer the core question of whether we SHOULD or SHOULD NOT expand nuclear power?
6. "overall_utility_score": A holistic assessment balancing academic quality with practical, in-round debate utility.

RULE 5: METHODOLOGY STRENGTH EXPLANATION.
- If a paper receives an overall_utility_score of 8 or higher, you MUST provide a 1-3 sentence explanation of WHY the paper is analytically strong. 
- For example, point out if it uses a "Natural Experiment", "avoids endogeneity", "staggered Difference-in-Differences (DID)", or is a "comprehensive meta-analysis". This helps the debater explain why this evidence is better than their opponent's evidence. 

Output Format:
Respond ONLY with a valid JSON object matching this exact structure:
{
  "paper_title": "String (Must be the EXACT title from the text)",
  "overall_paper_stance": "Proposition" | "Opposition" | "Mixed" | "Irrelevant",
  "scores": {
    "topic_relevance": 0,
    "impact_magnitude": 0,
    "methodology_strength": 0,
    "lay_persuasiveness": 0,
    "resolution_directness": 0,
    "overall_utility_score": 0
  },
  "strategy_brief": "A 2-sentence summary of the paper's utility, OR a brief explanation of why it was deemed irrelevant.",
  "strength_explanation": "String. If score is 8+, explain the methodological strength here. Otherwise, return an empty string.",
  "debate_cards": [
    {
      "card_stance": "Proposition" | "Opposition",
      "tagline": "String",
      "verbatim_quote": "String"
    }
  ]
}
""",
        height=500
    )


    with st.expander("Advanced: Edit System Prompts"):
        gold_mine_prompt = st.text_area(
            "GOLD_MINE_PROMPT", 
            value=  """
    You are an elite debate coach. I am handing you an academic paper that has already been verified as highly relevant. 
    Your ONLY job is to extract 3 to 5 ADDITIONAL verbatim debate cards.
    Do NOT extract quotes that have already been found.
    Respond ONLY with a JSON list of objects: [{"card_stance": "...", "tagline": "...", "verbatim_quote": "..."}]
    Your reasponses should add to the existing cards and be formatted exactly the same.
    DO NOT PASTE THE PREVIOUS CARDS YOUR REASPONSES SHOULD ONLY BE YOUR ADDITION 3 TO 5 CARDS FORMATTED EXACTLY AS THE FIRST 4 SO YOURS CAN EASILY BE ADDE ON TOP OF THOSE.
    Respond ONLY with a JSON object matching this exact structure:
    {
    "new_cards": [
        {
        "card_stance": "...",
        "tagline": "...",
        "verbatim_quote": "..."
        }
    ]
    }
    """,
            height=500
        )

log_queue = queue.Queue() 
def log_message(msg):
    """Prints to terminal and logs to UI queue."""
    print(msg)
    log_queue.put(msg)


def build_citation_map(soup):
    citation_map = {} 
    for bibl in soup.find_all('biblStruct'):
        bib_id = bibl.get('xml:id') 
        if not bib_id: 
            continue
            
        author = "Unknown Author"
        author_tag = bibl.find('author') 
        if author_tag and author_tag.find('surname'):
            author = author_tag.find('surname').text.strip()
            
        year = "Unknown Year"
        date_tag = bibl.find('date') 
        if date_tag and date_tag.get('when'): 
            year = date_tag.get('when')[:4]
            
        title = "Unknown Title"
        title_tag = bibl.find('title') 
        if title_tag: 
            title = title_tag.text.strip()
            
        citation_map[bib_id] = f"{author}, {year} - '{title}'" 
    return citation_map 

def extract_title(soup, filename):
    analytic = soup.find('analytic')
    if analytic:
        title_tag = analytic.find('title', level='a', type='main') or analytic.find('title', level='a') 
        if title_tag and title_tag.text.strip():
            return title_tag.text.strip() 
            
    title_stmt = soup.find('titleStmt')
    if title_stmt:
        title_tag = title_stmt.find('title', type='main') or title_stmt.find('title') 
        if title_tag and title_tag.text.strip():
            return title_tag.text.strip() 
            
    return filename.replace("_output.xml", "").replace(".xml", "").replace("_", " ").strip() 

def clean_and_inject_citations(element, citation_map):
    ''' this is where the citaiont map pays off and  elements are replaced with there real citations eg smith 2016 '''
    if not element:
        return ""
        
    for ref in element.find_all('ref', type='bibr'):
        target = ref.get('target') 
        if target and target.startswith('#'): 
            bib_id = target[1:] 
            if bib_id in citation_map: 
                ref.replace_with(f" [SOURCE: {citation_map[bib_id]}] ")
            else:
                ref.replace_with(f" [{ref.text}] ") 
                
    text = element.get_text(separator=' ', strip=True)
    text = re.sub(r'\(\d{1,3}\)', '', text) 
    text = re.sub(r'\s\d{1,3}\)\s', ' ', text) 
    text = re.sub(r'doi:\s*10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+', '', text, flags=re.IGNORECASE)
    return re.sub(r'\s{2,}', ' ', text).strip() 

def parse_table_to_markdown(table_tag):
    """Converts a GROBID XML table into a clean, LLM-readable Markdown string."""
    if not table_tag:
        return ""

    markdown_table = [] 
    rows = table_tag.find_all('row')
    
    for i, row in enumerate(rows):
        cells = row.find_all('cell')
        row_data = [cell.get_text(separator=" ", strip=True) for cell in cells]
        markdown_table.append("| " + " | ".join(row_data) + " |") 
        
        
        if i == 0:
            markdown_table.append("|" + "|".join(["---"] * len(cells)) + "|")
            
    return "\n".join(markdown_table) 

def process_papers(active_session):
    log_message(f"[*] Accessing Supabase Storage for session: '{active_session}'...")

    try:
        file_objects = supabase.storage.from_("paper_xmls").list(active_session,
        {"limit": 1000, "offset": 0}
)

    except Exception as e:
        log_message(f"[!] Supabase Storage Access Error: {e}")
        return

    
    xml_files = [f for f in file_objects if f.get("name", "").endswith(".xml")]

    if not xml_files:
        log_message(f"[!] No XML files found for session '{active_session}'.")
        return

    log_message(f"\n[+] Found {len(xml_files)} cloud XML files. Commencing Context Optimization...\n")
    database = []

    for idx, file_info in enumerate(xml_files, start=1):
        filename = file_info["name"]
        log_message(f"  [-] Optimizing [{idx}/{len(xml_files)}]: {filename[:50]}...")
        
        try:
            
            file_bytes = supabase.storage.from_("paper_xmls").download(f"{active_session}/{filename}")
            soup = BeautifulSoup(file_bytes.decode('utf-8'), 'xml')

            
            for tag in soup.find_all(['note']): tag.decompose()
            citation_map = build_citation_map(soup)
            title = extract_title(soup, filename)

            abstract_tag = soup.find('abstract')
            abstract = clean_and_inject_citations(abstract_tag, citation_map) if abstract_tag else "No abstract provided."

            body_text = ""
            body_tag = soup.find('body')
            if body_tag:
                for element in body_tag.find_all(['p', 'figure']):
                    if element.name == 'figure' and element.get('type') == 'table':
                        caption_tag = element.find('head') or element.find('figDesc')
                        caption = caption_tag.get_text(strip=True) if caption_tag else "Data Table"
                        if table_node := element.find('table'):
                            md_table = parse_table_to_markdown(table_node)
                            body_text += f"\n\n[START TABLE: {caption}]\n{md_table}\n[END TABLE]\n\n"
                    elif element.name == 'p':
                        cleaned_p = clean_and_inject_citations(element, citation_map)
                        if len(cleaned_p.split()) > 8: body_text += cleaned_p + "\n\n"

            database.append({
                "filename": filename,
                "title": title,
                "abstract": abstract,
                "body": body_text.strip()
            })

        except Exception as e:
            log_message(f"  [!] Failed to process '{filename}': {e}")

    
    log_message("\n[*] Uploading optimized database back to Supabase cloud...")
    try:
        json_data = json.dumps(database, indent=4, ensure_ascii=False)
        supabase.storage.from_("paper_xmls").upload(
            path=f"{active_session}/llm_ready_database.json",
            file=json_data.encode("utf-8"),
            file_options={"content-type": "application/json", "upsert": "true"}
        )
        log_message(f"[✓] SUCCESS! {len(database)} optimized papers saved to cloud.")
    except Exception as e:
        log_message(f"[!] Failed to upload to Supabase: {e}")




def grade_papers_with_ai(active_session, api_key, provider_url, model_name):
    if not api_key:
        log_message("[!] Error: Missing API Key for the selected provider!")
        return
    clean_key = api_key.strip() if api_key else ""

    if not clean_key:
        log_message("[!] Error: API Key is empty! Check your sidebar input.")
        return

    
    client = OpenAI(api_key=clean_key, base_url=provider_url)

    try:
        file_bytes = supabase.storage.from_("paper_xmls").download(f"{active_session}/llm_ready_database.json")
        papers = json.loads(file_bytes.decode("utf-8"))
        if dry_run:
            papers = papers[:5]
        
    except Exception as e:
        log_message(f"[!] Could not find optimized database in cloud for this session. Run Context Optimizer first! Error: {e}")
        return

    log_message(f"[+] Loaded {len(papers)} optimized papers from cloud. Starting AI Grading...")
    final_results = []

    for idx, paper in enumerate(papers):
        title = paper.get('title', 'Unknown Title')
        filename = paper.get('filename', 'Unknown_File.xml')
        raw_text = paper.get('body', '')

        safe_text = raw_text
        if len(raw_text) > 60000:
            safe_text = raw_text[:60000]
        user_content = f"DEBATE RESOLUTION:\n{topic}\n\nPAPER TITLE:\n{title}\n\nPAPER TEXT:\n{safe_text}"
        last_error = None
        for attempt in range(1, 2 + 2):
            log_message(f"[*] Sending to DeepSeek ({attempt}/{2 + 1}): '{title[:60]}...'")
            try:
                response = client.chat.completions.create( 
                    model=model_name,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content}
                    ],
                    response_format={"type": "json_object"}, 
                    temperature=0,
                    max_tokens=4000 
                )
                
                raw_response = response.choices[0].message.content 
                
                try:
                    ai_data = json.loads(raw_response)
                    
                    
                    ai_data["source_filename"] = filename 
                    ai_data["original_title"] = title 
                    score = ai_data.get("scores", {}).get("overall_utility_score", 0)
                    gold_cards = ai_data["debate_cards"]
                    if score >= 8:
                        user_content = f"DEBATE RESOLUTION:\n{topic}\n\nPAPER TITLE:\n{title}\n\nPAPER TEXT:\n{safe_text} EXISTING CARDS: \n{gold_cards}"
                        last_error = None
                        for attempt in range(1, 2 + 2):
                                log_message(f"[*] Sending to DeepSeek ({attempt}/{2 + 1}): '{title[:60]}...'")
                                try:
                                    response = client.chat.completions.create( 
                                        model=model_name,
                                        messages=[
                                            {"role": "system", "content": gold_mine_prompt},
                                            {"role": "user", "content": user_content}
                                        ],
                                        response_format={"type": "json_object"}, 
                                        temperature=0,
                                        max_tokens=4000 
                                    )
                                    
                                    raw_response = response.choices[0].message.content 
                                    
                                    try:
                                        ai_data2 = json.loads(raw_response)
                                        new_cards = ai_data2.get("new_cards", [])
                                        ai_data["debate_cards"].extend(new_cards)
                                        log_message(f"  [+] Added {len(new_cards)} extra cards!")

                                        break
                                    except json.JSONDecodeError:
                                        raise ValueError("DeepSeek's response was too long and the JSON got cut off mid-sentence.")
                      
                                except Exception as e:
                                            last_error = str(e) 
                                            log_message(f"[!] Attempt {attempt} failed for '{title[:40]}': {e}")
                                            if attempt <= 2:
                                                backoff = 5 * attempt 
                                                log_message(f"    Retrying in {backoff}s...")
                                                time.sleep(backoff) 
                         

                    final_results.append(ai_data) 
                    log_message(f"  [✓] Success! Graded: {title[:30]}")
                    break
                except json.JSONDecodeError:
                    raise ValueError("DeepSeek's response was too long and the JSON got cut off mid-sentence.")
    
            except Exception as e:
                last_error = str(e) 
                log_message(f"[!] Attempt {attempt} failed for '{title[:40]}': {e}")
                if attempt <= 4:
                    backoff = 5 * attempt 
                    log_message(f"    Retrying in {backoff}s...")
                    time.sleep(backoff) 
    # --- SAVE TO CLOUD ---
    log_message("\n[*] Uploading graded debate cards back to Supabase cloud...")
    try:
        graded_json = json.dumps(final_results, indent=4, ensure_ascii=False)
        
        
        supabase.storage.from_("paper_xmls").upload(
            path=f"{active_session}/extracted_debate_cards.json",
            file=graded_json.encode("utf-8"),
            file_options={"content-type": "application/json", "upsert": "true"}
        )
        log_message("[✓] SUCCESS! All graded cards saved to cloud.")
    except Exception as e:
        log_message(f"[!] Failed to upload to Supabase: {e}")  
            


col1, col2 = st.columns(2) 

with col1:
    st.write("### Step 1: Package Data for AI")
    run_optimize = st.button("Context Optimize", type="secondary", use_container_width=True)

with col2: 
    st.write("### Step 2 AI grader: Send to an AI  for grading.")
    run_grader = st.button("AI grade", type = "secondary", use_container_width=True)

st.write("###  Real-Time Pipeline Progress") 
log_placeholder = st.empty()



if run_optimize:
    log_message("★ PHASE 3: THE CONTEXT OPTIMIZER ★")
    
    
    current_session = st.session_state.session_id
    optimizer_thread = threading.Thread(target=process_papers, args=(current_session,)) 
    optimizer_thread.start()
    
    displayed_lines = []
    max_lines_on_screen = 25
    
    while optimizer_thread.is_alive() or not log_queue.empty(): 
        while not log_queue.empty():
            try:
                msg = log_queue.get_nowait()
                displayed_lines.append(msg)
            except queue.Empty:
                break# then break when empty
        
        if len(displayed_lines) > max_lines_on_screen:
            displayed_lines = displayed_lines[-max_lines_on_screen:]
        
        log_placeholder.code("\n".join(displayed_lines), language="text")
        time.sleep(0.2)
    optimizer_thread.join()
    st.success(f" Optimization Complete! Context generated and ready for grading!")

if run_grader:
    log_message("★ PHASE 4: THE AI GRADER ★")
    
    
    grader_thread = threading.Thread(
        target=grade_papers_with_ai, 
        args=(session, user_api_key, provider_url, active_model)
    )
    grader_thread.start()
    
    displayed_lines = []
    max_lines_on_screen = 25
    
    while grader_thread.is_alive() or not log_queue.empty(): 
        while not log_queue.empty():
            try:
                msg = log_queue.get_nowait()
                displayed_lines.append(msg)
            except queue.Empty:
                break
        
        if len(displayed_lines) > max_lines_on_screen:
            displayed_lines = displayed_lines[-max_lines_on_screen:]
        
        log_placeholder.code("\n".join(displayed_lines), language="text")
        time.sleep(0.2)
        
    grader_thread.join()
    st.success(" AI Grading Complete! Cards extracted and saved to Supabase!")