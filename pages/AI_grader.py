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


# --- 1. PAGE SETUP & UI ---
st.set_page_config(page_title="Context Optimizer", layout="wide")
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

#st.session_state.session_id = "2026-08-20_14-20-07_32cd9074-bcd5-4b6d-9ce2-a960e4d03648"

session = st.session_state.session_id

#CONFIG like what llama to use grobids url where to put files ect
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
    # Allow custom model typing or override if desired
    active_model = st.text_input("Model ID", value=config["model"])
    user_api_key = st.text_input("API Key", type="password").strip()
    
    

    with st.expander("Advanced: Edit System Prompts"):
        SYSTEM_PROMPT = st.text_area(# this creats an expander where the user can edit the system prompt for deep seek
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
""",# i cant do ntoes like this on the prompt itself but feel free to ask questions!
        height=500
    )


    with st.expander("Advanced: Edit System Prompts"):
        gold_mine_prompt = st.text_area(# this creats an expander where the user can edit the system prompt for deep seek
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

log_queue = queue.Queue() # this makes the log queue a waiting room for the workers for them to put there stuff in there to wait to be safley put on the streamlit screen for the user

def log_message(msg):
    """Prints to terminal and logs to UI queue."""
    print(msg)
    log_queue.put(msg) # this puts the message in the "waiting room" to be later displayed for streamlit. A loop later down the line will constatly check this to display it for the user.



# =========================================================================
# PHASE 3: OPTIMIZER HELPER FUNCTIONS
# =========================================================================
def build_citation_map(soup):
    citation_map = {} # we need to build citation map to replace grobidfes b03 of whatever bibiography ids with instead the specific source where it game from EG smith 2019. this makes it so deepseek can know exactly who had what data.
    for bibl in soup.find_all('biblStruct'):# find the biblstuct wich is what grobid calles its bibliiographies
        bib_id = bibl.get('xml:id') # get the id of that specific papers
        if not bib_id: # if it has no id skip
            continue
            
        author = "Unknown Author"
        author_tag = bibl.find('author') # fin the author by first looking for the auther tag then look at the surname section and set the author eaqueat to the surname
        if author_tag and author_tag.find('surname'):
            author = author_tag.find('surname').text.strip()
            
        year = "Unknown Year"
        date_tag = bibl.find('date') # try to find the date tag
        if date_tag and date_tag.get('when'): # if theres a dat tage and a when(what year) then take the first 4 charators(the year eg 2016)
            year = date_tag.get('when')[:4]
            
        title = "Unknown Title"
        title_tag = bibl.find('title') # thne try to find the title
        if title_tag: # if it exists set the title to the title tag
            title = title_tag.text.strip()
            
        citation_map[bib_id] = f"{author}, {year} - '{title}'" # make an entry in the citation map dictionary with this specific bibl id haveing the uathor year and title
    return citation_map # finally return this dictionary.

def extract_title(soup, filename):
    analytic = soup.find('analytic')# grobid puts the title in the analytic field so we have to reach in there to get the title.
    if analytic:
        title_tag = analytic.find('title', level='a', type='main') or analytic.find('title', level='a') # use the a to go to the anylitical level so i get the paper and not the journal then the main grabs the main title not the sub title
        if title_tag and title_tag.text.strip():
            return title_tag.text.strip() # if it works return the title
            
    title_stmt = soup.find('titleStmt')# if the above doesnt work try looking in a place called title stmt
    if title_stmt:
        title_tag = title_stmt.find('title', type='main') or title_stmt.find('title') # if it existes try to find the titles it might be type main if not just search for title
        if title_tag and title_tag.text.strip():
            return title_tag.text.strip() # finally return that title
            
    return filename.replace("_output.xml", "").replace(".xml", "").replace("_", " ").strip() # if all else fails take the file name and remove all the stuff to reverse engineer the title fro mthe file name

def clean_and_inject_citations(element, citation_map):
    ''' this is where the citaiont map pays off and  elements are replaced with there real citations eg smith 2016 '''
    if not element:
        return ""# if therse no element return nothing
        
    for ref in element.find_all('ref', type='bibr'):# then find all the refrence notes grobid left
        target = ref.get('target') # i know the target is the right thing but what is the target?
        if target and target.startswith('#'): # if it starts with # then its a internal link
            bib_id = target[1:] # then take everything from index 1 on to remove the #
            if bib_id in citation_map: # if the bib id is in our citation map then replace it with thie source so deepseek can read it easily
                ref.replace_with(f" [SOURCE: {citation_map[bib_id]}] ")
            else:
                ref.replace_with(f" [{ref.text}] ") # else leave it how it is?
                
    text = element.get_text(separator=' ', strip=True)
    text = re.sub(r'\(\d{1,3}\)', '', text) # remove number fragments that grobid left behind
    text = re.sub(r'\s\d{1,3}\)\s', ' ', text) # removes framents like 12) from grobid
    text = re.sub(r'doi:\s*10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+', '', text, flags=re.IGNORECASE)# remove random DOIs to make it more readable for the AI
    return re.sub(r'\s{2,}', ' ', text).strip() # removes 2 or more spaces in a row

def parse_table_to_markdown(table_tag):
    """Converts a GROBID XML table into a clean, LLM-readable Markdown string."""
    if not table_tag: # if there no table tag return nothing
        return ""

    markdown_table = [] # make a mardown table so we can write this  table in markdown so AI cna read it
    # GROBID uses <row> and <cell> for its TEI-XML tables
    rows = table_tag.find_all('row')# take the table and find each row
    
    for i, row in enumerate(rows):
        cells = row.find_all('cell')# Cells are the inbeeded things in each row
        # Clean the text inside each cell
        row_data = [cell.get_text(separator=" ", strip=True) for cell in cells]# does this loop through each cell strip them and seperate them with a space?
        markdown_table.append("| " + " | ".join(row_data) + " |") # puts a starting | then joins each one with a | to format it as a cell
        
        # Add the Markdown header separator after the first row
        if i == 0:
            markdown_table.append("|" + "|".join(["---"] * len(cells)) + "|")# makes the headers have seperation so the AI can see that
            
    return "\n".join(markdown_table) # return our formatted table

def process_papers(active_session):
    log_message(f"[*] Accessing Supabase Storage for session: '{active_session}'...")

    try:
        # List all files in the session folder
        file_objects = supabase.storage.from_("paper_xmls").list(active_session,
        {"limit": 1000, "offset": 0}
)

    except Exception as e:
        log_message(f"[!] Supabase Storage Access Error: {e}")
        return

    # Filter for XMLs
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
            # 1. Download XML from Supabase into memory
            file_bytes = supabase.storage.from_("paper_xmls").download(f"{active_session}/{filename}")
            soup = BeautifulSoup(file_bytes.decode('utf-8'), 'xml')

            # 2. Clean and Parse
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

    # 3. UPLOAD THE OPTIMIZED JSON BACK TO SUPABASE
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


# Phase 4 AI grader

def grade_papers_with_ai(active_session, api_key, provider_url, model_name):
    if not api_key:
        log_message("[!] Error: Missing API Key for the selected provider!")
        return
    clean_key = api_key.strip() if api_key else ""

    if not clean_key:
        log_message("[!] Error: API Key is empty! Check your sidebar input.")
        return

    # Initialize the OpenAI client dynamically using the selected provider's URL
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
        title = paper.get('title', 'Unknown Title')# take out the title filname and the bofy text
        filename = paper.get('filename', 'Unknown_File.xml')
        raw_text = paper.get('body', '')

        safe_text = raw_text
        if len(raw_text) > 60000:
            safe_text = raw_text[:60000]
        user_content = f"DEBATE RESOLUTION:\n{topic}\n\nPAPER TITLE:\n{title}\n\nPAPER TEXT:\n{safe_text}"
        last_error = None# hold the last error to tell wht went down if all fails
        for attempt in range(1, 2 + 2):
            log_message(f"[*] Sending to DeepSeek ({attempt}/{2 + 1}): '{title[:60]}...'")
            try:
                response = client.chat.completions.create( # we attempt to get a reasponse from the client with the use and system prompt
                    model=model_name,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content}
                    ],
                    response_format={"type": "json_object"}, # it must be json this tells the AI to use jsomn
                    temperature=0,#temp 0 for objectivity
                    max_tokens=4000 # Force it to keep cards concise to avoid JSON cut-offs
                )
                
                raw_response = response.choices[0].message.content # take just deepseeks reasponse nothing else
                
                try:
                    ai_data = json.loads(raw_response)
                    
                    # INJECT METADATA SAFELY IN PYTHON (Never trust the LLM to do this)
                    ai_data["source_filename"] = filename # again dont trust the llm so we get the eaxt name and title
                    ai_data["original_title"] = title 
                    score = ai_data.get("scores", {}).get("overall_utility_score", 0)
                    gold_cards = ai_data["debate_cards"]
                    if score >= 8:
                        user_content = f"DEBATE RESOLUTION:\n{topic}\n\nPAPER TITLE:\n{title}\n\nPAPER TEXT:\n{safe_text} EXISTING CARDS: \n{gold_cards}"
                        last_error = None# hold the last error to tell wht went down if all fails
                        for attempt in range(1, 2 + 2):
                                log_message(f"[*] Sending to DeepSeek ({attempt}/{2 + 1}): '{title[:60]}...'")# tell the user we are senging to deepseek
                                try:
                                    response = client.chat.completions.create( # we attempt to get a reasponse from the client with the use and system prompt
                                        model=model_name,
                                        messages=[
                                            {"role": "system", "content": gold_mine_prompt},
                                            {"role": "user", "content": user_content}
                                        ],
                                        response_format={"type": "json_object"}, # it must be json this tells the AI to use jsomn
                                        temperature=0,#temp 0 for objectivity
                                        max_tokens=4000 # Force it to keep cards concise to avoid JSON cut-offs
                                    )
                                    
                                    raw_response = response.choices[0].message.content # take just deepseeks reasponse nothing else
                                    
                                    try:
                                        ai_data2 = json.loads(raw_response)
                                        new_cards = ai_data2.get("new_cards", [])
                                        ai_data["debate_cards"].extend(new_cards)
                                        log_message(f"  [+] Added {len(new_cards)} extra cards!")

                                        break
                                    except json.JSONDecodeError:
                                        raise ValueError("DeepSeek's response was too long and the JSON got cut off mid-sentence.")
                      
                                except Exception as e:
                                            last_error = str(e) # why have this last error part?
                                            log_message(f"[!] Attempt {attempt} failed for '{title[:40]}': {e}")
                                            if attempt <= 2:
                                                backoff = 5 * attempt # rety after  backoff*5 seconds
                                                log_message(f"    Retrying in {backoff}s...")
                                                time.sleep(backoff) # backoff to let the API cool off
                         

                    final_results.append(ai_data) # return the data finally
                    log_message(f"  [✓] Success! Graded: {title[:30]}")
                    break
                except json.JSONDecodeError:
                    raise ValueError("DeepSeek's response was too long and the JSON got cut off mid-sentence.")
    
            except Exception as e:
                last_error = str(e) # why have this last error part?
                log_message(f"[!] Attempt {attempt} failed for '{title[:40]}': {e}")
                if attempt <= 4:
                    backoff = 5 * attempt # rety after  backoff*5 seconds
                    log_message(f"    Retrying in {backoff}s...")
                    time.sleep(backoff) # backoff to let the API cool off
    # --- SAVE TO CLOUD ---
    log_message("\n[*] Uploading graded debate cards back to Supabase cloud...")
    try:
        graded_json = json.dumps(final_results, indent=4, ensure_ascii=False)
        
        # Upload the master list to the active session folder
        supabase.storage.from_("paper_xmls").upload(
            path=f"{active_session}/extracted_debate_cards.json",
            file=graded_json.encode("utf-8"),
            file_options={"content-type": "application/json", "upsert": "true"}
        )
        log_message("[✓] SUCCESS! All graded cards saved to cloud.")
    except Exception as e:
        log_message(f"[!] Failed to upload to Supabase: {e}")  
            

# =========================================================================
# UI INTERACTION LAYOUT
# =========================================================================
col1, col2 = st.columns(2) # create 2 collums 1 for snowball and 1 for context optimizing

with col1:
    st.write("### Step 1: Package Data for AI")
    run_optimize = st.button("Context Optimize", type="secondary", use_container_width=True)

with col2: # make a clear context optimize button for the user to use
    st.write("### Step 2 AI grader: Send to an AI  for grading.")
    run_grader = st.button("AI grade", type = "secondary", use_container_width=True)

st.write("###  Real-Time Pipeline Progress") # tell the user that we will track it real time and set the log placeholder an empty strealit text
log_placeholder = st.empty()



if run_optimize:
    log_message("★ PHASE 3: THE CONTEXT OPTIMIZER ★")
    
    # Grab the session ID on the main thread, then pass it to the background worker
    current_session = st.session_state.session_id
    optimizer_thread = threading.Thread(target=process_papers, args=(current_session,)) 
    optimizer_thread.start()
    
    displayed_lines = []
    max_lines_on_screen = 25# set the displaty limit
    
    while optimizer_thread.is_alive() or not log_queue.empty(): 
        while not log_queue.empty():
            try:
                msg = log_queue.get_nowait()
                displayed_lines.append(msg)
            except queue.Empty:
                break# then break when empty
        
        if len(displayed_lines) > max_lines_on_screen:
            displayed_lines = displayed_lines[-max_lines_on_screen:]
        
        log_placeholder.code("\n".join(displayed_lines), language="text")# 
        time.sleep(0.2)#pause si ut doesnt try to do like like 1million times so the user can see it celarly
    optimizer_thread.join()# what .join? what does this do?
    st.success(f" Optimization Complete! Context generated and ready for grading!")# thne tell the sucsess.

if run_grader:
    log_message("★ PHASE 4: THE AI GRADER ★")
    
    # Run in a background thread so the UI doesn't freeze!
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