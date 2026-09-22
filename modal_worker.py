# this script is meant to operate as the backend. 
# for the heavy lifting like grobid that streamlit can handle thats done here.
# files made in this script are saved to Supa base.
# this is the meat of the app handling the most important part:XMLs made by grobid of relivant scientific papers.
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
from openai import OpenAI
import uuid
from supabase import create_client, Client
import modal
import supabase
import random
from datetime import datetime
import threading
import queue
import time



# all setup like scraper headers and what modal needs to download.
scraper_image = (
    modal.Image.debian_slim()
    .pip_install("requests", "cloudscraper", "beautifulsoup4", "playwright", "openai", "supabase")
    .run_commands(
        "playwright install-deps chromium", 
        "playwright install chromium"
    )
)

app = modal.App("debate-scraper")


grobid_image = (
    modal.Image.from_registry("lfoppiano/grobid:0.8.0", add_python="3.11")
    .pip_install("requests","lxml", "cloudscraper", "beautifulsoup4", "playwright", "openai", "supabase")
)

@app.function(image=grobid_image, cpu=4.0, memory=8192)
@modal.web_server(port=8070, startup_timeout=300) 
def grobid_cloud_service():
    import subprocess
    subprocess.Popen(["./grobid-service/bin/grobid-service"])

OPENALEX_API_URL = "https://api.openalex.org/works"
CURRENT_MODEL_IDX = 0
GROBID_URL = "https://crjonte-spec--debate-scraper-grobid-cloud-service.modal.run/api/processFulltextDocument"
OUTPUT_DIR = "extracted_xmls" 
SNOWBALL_FILE = "snowballed_targets.txt"


    
print(" Modal Cloud Server initialized with OpenAI SDK -> Groq Endpoint!")
    
LLAMA_MODEL = "llama3.1"
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)

scraper.headers.update({
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://google.com"
})

# keyword Batch. This function is meant to minimize groq usage well also varifing the reliablitiy of papers.
# it handles 10 papers at ones because the biggest roadblock is not tokens its requests so we get 10 request for the price of 1
# also there fallback modles so users dont have to worry about usage limits(all modles are free with groq API)
def batch_verify_with_local_llama(papers_batch, template_string="", client=None):
    """Screens a batch of up to 10 papers in a single API call to save Groq rate limits."""
    global CURRENT_MODEL_IDX
    if not papers_batch: return []

    
    FALLBACK_MODELS = [
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.6-27b"
]
    
    
    prompt = "Evaluate the following academic papers based on this criteria:\n"
    prompt += template_string + "\n\n"
    prompt += "Respond ONLY with a JSON array of true/false values corresponding exactly to the order of the papers. Example: [true, false, true]\n\n"
    prompt += "Respond ONLY with a raw JSON array of booleans. Do NOT include markdown formatting blocks, intro text, or explanations.\n\n"
    
    for idx, paper in enumerate(papers_batch, start=1):
        short_abstract = str(paper[4])[:400] if paper[4] else f"Evaluate based on title: {paper[1]}"
        prompt += f"Paper {idx}:\nTitle: {paper[1]}\nAbstract: {short_abstract}...\n\n"
            
    max_attempts = 8
    for attempt in range(max_attempts):
        MODLE = FALLBACK_MODELS[CURRENT_MODEL_IDX]
        try:
            response = client.chat.completions.create(
                model=MODLE,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0
            )
            raw_text = response.choices[0].message.content.strip()
            print(f"DEBUG RAW TEXT: {raw_text}")
            if raw_text.startswith("```json"): raw_text = raw_text[7:-3]
            elif raw_text.startswith("```"): raw_text = raw_text[3:-3]
            
            import json
            decisions = json.loads(raw_text)
            
           
            approved = []
            for paper, is_good in zip(papers_batch, decisions):
                if is_good:
                    print(f"    [Groq Batch: YES] -> Title: {paper[1][:500]}...")
                    approved.append((paper[0], paper[1], paper[2], paper[3]))
                else:
                    print(f"    [Groq Batch: NO]  -> Title: {paper[1][:500]}...")
            return approved
            
        except Exception as e:
            if "TPD" in str(e) or "RPD" in str(e):
                print(f"  [!] {MODLE} is OUT of daily tokens. Rotating to next model...")
                CURRENT_MODEL_IDX = CURRENT_MODEL_IDX + 1
                if CURRENT_MODEL_IDX > len(FALLBACK_MODELS) - 1:
                    CURRENT_MODEL_IDX = 0
            elif "429" in str(e) or "Rate limit" in str(e):
                time.sleep(random.uniform(8, 15))
            else:
                print(f"    [!] JSON Parse error on batch attempt: {e}")
                time.sleep(2)
                
    return []


# this is an old unused feature mean to make boolean keywords (like HSR or highspeed rail) wich is somtimes better but I found this to be too gimicy.
def translate_keyword_to_boolean(basic_keyword, custom_template=None):
    return basic_keyword
    """Uses Llama to translate a simple keyword into an advanced academic Boolean query."""
    boolean_prompt = custom_template.replace("{basic_keyword}", basic_keyword)

    try:
        response = ollama_client.generate(
            model=LLAMA_MODEL,
            prompt=boolean_prompt,
            options={"temperature": 0.1} 
        )
        refined_query = response.get("response", "").strip().strip('"')
        
        print(f"\n  [AI Translator] Basic: '{basic_keyword}'")
        print(f"  [AI Translator] Advanced: {refined_query}\n")
        
        return refined_query
        
    except Exception as e:
        print(f"  [AI Translator Error] Fallback to basic keyword due to: {e}")
        return basic_keyword
# openalex acually makes it really easy to see what papers a paper cited with .get refrence works so this funtion finds the 15 most cited of that appers bibliography
# this is useful because it lets me explode say 1 paper from a keyword into 15 making a mssive net to get great papers
def get_native_backward_citations(openalex_id, max_backward=3):
    """Natively retrieves the top cited papers from a seed paper's bibliography with safe guards."""
    print(f"    [Snowball] Running Backward Citation lookup for seed {openalex_id}...")
    url = f"https://api.openalex.org/works/{openalex_id}"
    
    try:
        res = scraper.get(url, timeout=15)
        if res.status_code != 200:
            return []

        res_json = res.json() or {}
        referenced_ids = res_json.get("referenced_works") or []
        referenced_ids = referenced_ids[:max_backward*2]
        if not referenced_ids:
            return []
        
        filter_string = "|".join(referenced_ids)
       
        query_url = f"https://api.openalex.org/works?filter=openalex:{filter_string}"
       
        batch_res = scraper.get(query_url, timeout=15)
        candidates = []
       
        if batch_res.status_code == 200:
            batch_data = batch_res.json() or {}
            results = batch_data.get("results") or []
            for paper in results:
               
                if not paper or not isinstance(paper, dict):
                    continue
                
                title = paper.get("title")
                abstract = paper.get("abstract") or ""
                doi = (paper.get("doi") or "").replace("https://doi.org/", "")
               
                pdf_url = (paper.get("open_access") or {}).get("oa_url")
                
               
                paper_id = paper.get("id", "").split("/")[-1] if paper.get("id") else ""
                
                if title and pdf_url and paper_id:
                    candidates.append((doi, title, pdf_url, paper_id, abstract))
                    
        return candidates#return that big list
    
    except Exception as e:
        print(f"    [Snowball Error] Backward traversal failed: {e}")
        return []
# Very simular here execpt it finds papers that have cited that paper
# this has pretty much the same use as the fuction above it lets this app cast a wider net for more papers
def get_native_forward_citations(openalex_id, max_forward=3):
    """Natively finds newer academic works that have cited our seed paper with safe guards."""
    print(f"    [Snowball] Running Forward Citation lookup for seed {openalex_id}...")
   
    url = f"https://api.openalex.org/works?filter=cites:{openalex_id}&sort=cited_by_count:desc"
    
    try:
        
        res = scraper.get(url, timeout=15)
        candidates = []
        
        if res.status_code == 200:
            res_data = res.json() or {}
            results = res_data.get("results") or []
            for paper in results[:max_forward*2]:
                
                if not paper or not isinstance(paper, dict):
                    continue
                title = paper.get("title")
                abstract = paper.get("abstract") or ""
                doi = (paper.get("doi") or "").replace("https://doi.org/", "")
                pdf_url = (paper.get("open_access") or {}).get("oa_url")
                paper_id = paper.get("id", "").split("/")[-1] if paper.get("id") else ""
                
                if title and pdf_url and paper_id:
                    candidates.append((doi, title, pdf_url, paper_id, abstract))
               
        return candidates
    
    except Exception as e:
        print(f"    [Snowball Error] Forward traversal failed: {e}")
        return []


# this is what tunrs a keyword from keyword maker into paper URLS that we can follow later
# some things to notice is hat we give it multiple trys and we check a papers locations(wich open alex gives us) we try to find its PDF URL which lets us check all the places it could be
# this is where the batching pays off as we batch verfy our papers we foudn if we can get our hands on the title and abstract.
# the end goal of this fuction is to return a working PDF url title abract and other things in a touple
# an important on is prestige score so if it has the correct ISSN(if its from a reputable journal) then it gets boosted so that we can beter select what papers to explode with backword citations.
def fetch_openalex_papers(search_query=None, filter_query=None, max_papers=5, is_phase_1=False, llama_template="", client=None, debate_sectors = None):
    """Fetches papers from OpenAlex with clean API parameter handling and safe fallback guards."""
    
    fetch_limit = max_papers * 4 if not is_phase_1 else max_papers * 3
    
    
    params = {
        "per-page": fetch_limit,
        "mailto": "joesticky48@gmail.com"  
    }
    
   
    if filter_query:
        params["filter"] = filter_query
        
    if search_query:
        params["search"] = search_query

    
    max_retries = 2
    response = None
    
    for attempt in range(max_retries):
        try:
            # Give OpenAlex a full 60 seconds to respond
            response = scraper.get(OPENALEX_API_URL, params=params, timeout=60)
            
            if response.status_code == 200:
                break # Success! Break out of the retry loop and process the data.
                
            if response.status_code == 429:
                print(f"  [System] OpenAlex Rate Limit hit (Attempt {attempt+1}/{max_retries}). Waiting 10s...")
                time.sleep(10)
            else:
                print(f"  [System] OpenAlex returned status {response.status_code}. Retrying...")
                time.sleep(5)
                
        except requests.exceptions.Timeout:
            print(f"  [System] OpenAlex timed out on attempt {attempt + 1}/{max_retries}. Retrying in 5 seconds...")
            time.sleep(5)
            
        except Exception as e:
            print(f"  [System] OpenAlex connection error: {e}")
            break # If it's a completely different error, stop trying and move on
            
    
    if not response or response.status_code != 200:
        print(f"  [System] OpenAlex failed after {max_retries} attempts. Skipping to next keyword...")
        return []

    # If it succeeded, load the data!
    try:
        data = response.json() or {}
        raw_results = data.get('results', []) or []
        
        
        elite_issns = set()
        for sector_info in debate_sectors.values():
            elite_issns.update(sector_info.get("issns", []))
            
        scored_pool = []
        for paper in raw_results:
            if not paper or not isinstance(paper, dict):
                continue
            title = paper.get('title') or ''
            abstract = paper.get('abstract') or ''
            doi = (paper.get('doi') or '').replace('https://doi.org/', '')
            openalex_id = paper.get('id', '').split('/')[-1] if paper.get('id') else ''
            if not title or not openalex_id:
                continue
            base_relevance = paper.get("relevance_score") or 1.0
            

            locations = paper.get("locations") or []
            if not locations:
                locations = [paper.get("primary_location") or {}]
                
            pdf_url = None
            paper_issns = []

            for loc in locations:
                if not loc or not isinstance(loc, dict):
                    continue
                source = loc.get("source") or {}
                issn_list = source.get("issn") or []
                if isinstance(issn_list, list):
                    paper_issns.extend(issn_list)
                elif isinstance(issn_list, str):
                    paper_issns.append(issn_list)
                target_url = loc.get("pdf_url") or loc.get("landing_page_url") or ""
                
                if target_url and loc.get("is_oa"):
                    repo_domains = ["arxiv", "hal.", "zenodo", "repository", "purl", "nih.gov", "citeseerx", ".edu/"]
                    if any(domain in target_url.lower() for domain in repo_domains):
                        pdf_url = loc.get("pdf_url") or target_url 
                        break  # Found a clean repository link! Skip commercial paywalls.

            # Step 2: Fallback to the standard Open Access URL if no repository was found
            if not pdf_url:
                pdf_url = (paper.get('open_access') or {}).get('oa_url')
                
            if not pdf_url: 
                continue
            
            has_prestige_boost = any(issn in elite_issns for issn in paper_issns) 
            boosted_score = base_relevance * 1.5 if has_prestige_boost else base_relevance 
            primary_loc = paper.get("primary_location") or {}
            source_info = primary_loc.get("source") or {}
            journal_name = source_info.get("display_name", "Unknown Journal")
            scored_pool.append({
                "doi": doi,
                "title": title,
                "abstract": abstract,
                "pdf_url": pdf_url,
                "openalex_id": openalex_id, 
                "local_score": boosted_score,
                "boosted": has_prestige_boost,
                "journal_name": journal_name
            })
            
        scored_pool.sort(key=lambda x: x["local_score"], reverse=True)
        all_candidates = [(item["doi"], item["title"], item["pdf_url"], item["openalex_id"], item["abstract"]) for item in scored_pool]
        
        found_papers = []
        chunk_size = 15
        max_failed_batches = 2
        consecutive_failed_batches = 0
        for i in range(0, len(all_candidates), chunk_size):
            if len(found_papers) >= max_papers:
                break
                
            batch = all_candidates[i:i + chunk_size]
            print(f"  [*] Screening OpenAlex batch of {len(batch)} via Llama...")
            
            approved_batch = batch_verify_with_local_llama(batch, llama_template, client)

            if approved_batch:
                consecutive_failed_batches = 0
                for paper in approved_batch:
                    found_papers.append((paper[0], paper[1], paper[2], paper[3]))
                    if len(found_papers) >= max_papers:
                        break
            else:
                consecutive_failed_batches += 1
                print(f"  [!] 0 papers approved in this batch. ({consecutive_failed_batches}/{max_failed_batches} empty batches)")

            # --- THE KILL SWITCH ---
            if consecutive_failed_batches >= max_failed_batches:
                print(f"  [!] {max_failed_batches} batches rejected in a row. Stopping candidate search early!")
                return found_papers        
        return found_papers

    except Exception as e:
        print(f"  [System] Connection or parsing failed: {e}")
        return []
# this is out fallback if we cant find a PDF URL we go to the actual publisher page with playwright and have it look for things that look like pdf URLs   
def scrape_publisher_page(target_url, log_prefix): 
    "Navigates to the page and attemps to find the URL"
    import asyncio
    import sys
    
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print(f"    {log_prefix} [-] Deploying Headless Browser to bypass JS at {target_url[:40]}...")
    html_content = ""
    final_url = target_url

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = None
        try:
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page.goto(target_url, timeout=10000, wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            html_content = page.content()
            final_url = page.url
        
        except Exception as e:
            if "Download is starting" in str(e):
                print(f"    {log_prefix} [✓] Playwright intercepted a direct PDF download!")
                return target_url
         
            else:
                print(f"    {log_prefix} [X] Playwright error: {e}")
                return None
        finally:
            if page is not None:
                page.close()
            if browser is not None:
                browser.close()
    

    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        meta_tags = ['citation_pdf_url', 'eprints.document_url', 'dc.identifier']
        for tag in meta_tags:
            pdf_meta = soup.find('meta', {'name': tag}) or soup.find('meta', {'property': tag})
            if pdf_meta and pdf_meta.get('content') and 'pdf' in pdf_meta.get('content').lower():
                return urljoin(final_url, pdf_meta.get('content'))
                
        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.lower().endswith('.pdf') or 'downloadpdf' in href.lower() or '/pdf/' in href.lower():
                return urljoin(final_url, href)

        matches = re.compile(r'(https?://[^\s"\'<>]+?\.pdf)', re.IGNORECASE).findall(html_content)
        if matches:
            return matches[0] 
            
        print(f"    {log_prefix} [X] Publisher site fully rendered, but no free PDF is accessible.") 
        return None
    except Exception:
        return None

def download_and_process(pdf_url, title, safe_title, log_prefix, check_list = None, is_anchor=False, depth=0, client=None, session_id = None, llama_template = None):
    "This is what we needed the PDF url for. This function uses that url to send the paper to grobid and save it to supabase."
    if not pdf_url or depth > 1: return 
    PDF_DIR = "downloaded_pdfs"
    os.makedirs(PDF_DIR, exist_ok=True) 
    pdf_path = os.path.join(PDF_DIR, f"{safe_title}.pdf")


    if not pdf_url.startswith("http"):
        print(f"    {log_prefix} [X] Invalid URL detected. Skipping.")
        return {"status": "skipped", "reason": "Invalid URL"}

    try:
        response_pdf = scraper.get(pdf_url, timeout=8) 
    except Exception as e:
        print(f"    {log_prefix} [X] File download timed out or failed: {e}")
        return {"status": "failed", "error": str(e)}

    

    content_type = response_pdf.headers.get('Content-Type', '').lower() 
    
    if response_pdf.status_code == 200 and 'application/pdf' in content_type: 
        try:
            with open(pdf_path, 'wb') as f:
                f.write(response_pdf.content)
        except Exception as e:
            print(f"    {log_prefix} [!] Warning: Could not write PDF file to disk: {e}")

        file = io.BytesIO(response_pdf.content) 
        files = {'input': ('paper.pdf', file, 'application/pdf')}
        try:
            response_grobid = requests.post(GROBID_URL, files=files, timeout=300)
            
            if response_grobid.status_code == 200: 
                file_path_in_cloud = f"{session_id}/{safe_title}_output.xml"
                try:
                    
                    supabase.storage.from_("paper_xmls").upload(
                        path=file_path_in_cloud,
                        file=response_grobid.text.encode("utf-8"),
                        file_options={
                            "content-type": "application/xml",
                            "upsert": "true" 
                        }
                    )
                    
                except Exception as upload_err:
                    
                    if "409" in str(upload_err) or "Duplicate" in str(upload_err):
                        print(f"    [i] Paper already exists in this run's folder. Skipping duplicate.")
                    else:
                        print(f"    [!] Storage Upload Error: {upload_err}")
                if is_anchor:
                    print(f"    {log_prefix} [!] Anchor Analysis Complete. Unpacking snowball references...")
                    
            else:
                print(f"    {log_prefix} [!] GROBID returned status code {response_grobid.status_code}")
               
        except requests.exceptions.Timeout:
            print(f"    {log_prefix} [!!!] GROBID TIMEOUT: Paper took > 45s to parse. Skipped XML extraction.")
        except requests.exceptions.ConnectionError:
            print(f"    {log_prefix} [!!!] FATAL: Cannot connect to GROBID Docker!")
        except Exception as e:
            print(f"    {log_prefix} [!] Unexpected GROBID error: {e}")
            
    elif 'text/html' in content_type: 
        final_pdf_link = scrape_publisher_page(pdf_url, log_prefix)
        if final_pdf_link and final_pdf_link != pdf_url:
            download_and_process(
    final_pdf_link,
    title,
    safe_title,
    log_prefix,
    is_anchor=is_anchor,
    depth=depth + 1,
    client=client,
    session_id=session_id,
    llama_template=llama_template
)

#
@app.function(image=scraper_image, cpu=1.0, memory=2048, timeout=600, max_containers=15)
def worker_thread(doi, title, pdf_url, index, total_papers, is_anchor, groq_api_key, session_id, llama_template, supabase_url, supabase_key): 
    " now put it all together. This owrker will get called TONS once per papers to safley save it in supabase."
    
    ai_client = OpenAI(
        api_key=groq_api_key,
        base_url="https://api.groq.com/openai/v1",
        timeout=30.0
    )
    
    
    
    global supabase
    supabase = create_client(supabase_url, supabase_key)
    
    # kill switch here checks supa base status table and if it says to kill it does.
    try:
        status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
        if status_check.data and status_check.data[0]["status"] == "killed":
            print(f" 🚨 Kill signal received in worker. Aborting paper {title[:30]}")
            return
    except Exception as e:
        print(f"and error occured {e}")
        
    
   
    try:
        log_prefix = f"[P{index}/{total_papers}]"
        safe_doi = doi.replace("/", "_").replace(".", "_")
        clean_snippet = "".join([c if c.isalnum() or c == "_" else "" for c in title[:100].replace(" ", "_")])
        safe_title = f"{clean_snippet}_{safe_doi}"
        expected_filepath = os.path.join(OUTPUT_DIR, f"{safe_title}_output.xml") 
        
        if os.path.exists(expected_filepath): 
            print(f"  {log_prefix} [!] SKIPPED: Already have XML for '{title[:30]}...'")
            return 
            
        print(f"  {log_prefix} Thread started: {title[:40]}...")
        time.sleep(1) 
        
        if pdf_url:
            if "pdf" not in pdf_url.lower():
                pdf_url = scrape_publisher_page(pdf_url, log_prefix) or pdf_url 
                
            download_and_process(pdf_url, title, safe_title, log_prefix, is_anchor=is_anchor, client=ai_client, session_id = session_id, llama_template = llama_template)
            
    except Exception as e:
        print(f"  [!] Worker thread completely failed for paper '{title[:30]}': {e}")
@app.function(image=scraper_image, cpu=1.0, timeout=600, max_containers=15)
def extra_worker_thread(openalex_id, title, groq_api_key, llama_template):
    " this is sctrictly here to check the backword snowballs from meta anylisis' in phase 1."
  
    ai_client = OpenAI(api_key=groq_api_key, base_url="https://api.groq.com/openai/v1",timeout=30.0)
    
    
    Final_snowball_papers = []
    backword_potentials = get_native_backward_citations(openalex_id, max_backward=15)
    print(f"fetching backword citations for {title}")
    
    
    chunk_size = 10
    batches = [backword_potentials[i:i + chunk_size] for i in range(0, len(backword_potentials), chunk_size)]
    
    for batch in batches:
        approved_in_batch = batch_verify_with_local_llama(batch, llama_template, ai_client)
        if approved_in_batch:
            Final_snowball_papers.extend(approved_in_batch)
            
    return Final_snowball_papers




@app.function(image=scraper_image, cpu=1.0, timeout=600, max_containers=15)
def phase2_citation_worker(openalex_id, title, groq_api_key, llama_template):
    """Runs Phase 2 citation screening natively in the cloud using Batch Prompting."""
    ai_client = OpenAI(api_key=groq_api_key, base_url="https://api.groq.com/openai/v1",timeout=30.0)
    
    snowballed_additions = []
    
    backword_citations = get_native_backward_citations(openalex_id, max_backward= 15)
    forwar_citaionts = get_native_forward_citations(openalex_id, max_forward= 15)
    total_citaions = backword_citations + forwar_citaionts

    if not total_citaions:
        return []

    batches = []
    chunk_size = 10
    for i in range(0, len(total_citaions),chunk_size):
        chunk = total_citaions[i : i + chunk_size]
        batches.append(chunk)

    for batch in batches:
        approved_in_batch = batch_verify_with_local_llama(batch, llama_template, ai_client)


        
        if approved_in_batch:
            snowballed_additions.extend(approved_in_batch)

    return snowballed_additions


@app.function(
    image=scraper_image,
    memory=8192,     
    timeout=3600,    
    cpu=2.0          
)

def run_full_pipeline(keywords, groq_api_key, max_anchors, max_papers, max_threads, phase1_search, phase1_filter, llama_template, debate_sectors, session_id, supabase_url, supabase_key):
    "Generall idea put everything together and when you do heavly lifting work in modal use starmap to spin up tons of containing to do it in paralel(this saves tons of time)"
    print(" Modal Cloud Server has woken up!")

    worker_thread.update_autoscaler(max_containers=max_threads)
    extra_worker_thread.update_autoscaler(max_containers=max_threads)
    phase2_citation_worker.update_autoscaler(max_containers=max_threads)

    ai_client = OpenAI(
        api_key=groq_api_key,
        base_url="https://api.groq.com/openai/v1"
        ,timeout=30.0
    )

    global supabase  
    supabase = create_client(supabase_url, supabase_key)
    print("🗄️ Supabase client successfully initialized in the cloud!")
    
    print(f"Keywords received: {keywords}")

    ai_client = OpenAI(
        api_key=groq_api_key,
        base_url="https://api.groq.com/openai/v1",
        timeout= 30
    )
    print(" Modal Server initialized with user's Groq key!")
    
    if not keywords:
        yield {"type": "error", "message": "You must have at least one keyword checked!"}
        return  
        
    os.makedirs(OUTPUT_DIR, exist_ok=True) 
    
    # Phase 1
    yield {"type": "ui", "action": "subheader", "text": "Phase 1: Meta-Analyses & Snowballing", "phase": "1"}
    
    
    phase1_search = phase1_search 
    phase1_filter = phase1_filter
    # Filters for meta anlisis from the phase one filter related to the topic.
    anchor_papers = fetch_openalex_papers( 
    search_query=phase1_search,
    filter_query=phase1_filter, 
    max_papers=max_anchors, 
    is_phase_1=True, 
    llama_template=llama_template,
    client=ai_client, 
    debate_sectors = debate_sectors
)

    total_anchors = len(anchor_papers) 
    
    if total_anchors > 0: 

        
        completed_p1 = 0
        # create arguments to send to the jobs
        Final_snowball_papers_phase_1 = []
        worker_args_p1 = []
        for index, (doi, title, pdf_url, openalex_id) in enumerate(anchor_papers, start=1):
            args_tuple = (doi, title, pdf_url, index, total_anchors, True, groq_api_key, session_id, llama_template, supabase_url, supabase_key)
            worker_args_p1.append(args_tuple)

        citation_args = []
        for index, (doi, title, pdf_url, openalex_id) in enumerate(anchor_papers, start=1):
            citation_args.append((openalex_id, title, groq_api_key, llama_template))
        # starts the porssesing of the MEta anilsis at the same time as it verifies if the papers that they cite are relivant.
        anchor_job = worker_thread.starmap(worker_args_p1)
        citation_job = extra_worker_thread.starmap(citation_args)
        
        # sends yeilds back to update the progress bars on the frontend.
        for _ in anchor_job:
            
            status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
            if status_check.data and status_check.data[0]["status"] == "killed":
                yield {"type": "log", "phase": "1", "message": "🚨 Kill signal received. Aborting Phase 1."}
                return
           
            completed_p1 += 1
            yield {"type": "progress", "message": "Verifying and Dowloading Anchors", "current": completed_p1, "total": total_anchors, "phase": "1"}

        
        completed_p1point5 = 0   
        

        for approved_citations in citation_job:
            
            status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
            if status_check.data and status_check.data[0]["status"] == "killed":
                yield {"type": "log", "phase": "1", "message": "🚨 Kill signal received. Aborting Citation Harvest."}
                return
            
            if approved_citations:
                Final_snowball_papers_phase_1.extend(approved_citations)
            
            completed_p1point5 += 1 
            yield {"type": "progress", "message": "Harvesting Citations", "current": completed_p1point5, "total": total_anchors, "phase": "1"}

            
        completed_3 = 0  
        Final_snowball_papers_phase_1 = list(set(Final_snowball_papers_phase_1))
        total_snowballs = len(Final_snowball_papers_phase_1)
        if total_snowballs > 0:
            worker_args_snowballs = []
            for index, (doi, title, pdf_url, openalex_id) in enumerate(Final_snowball_papers_phase_1, start=1):
                args_tuple = (doi, title, pdf_url, index, total_snowballs, False, groq_api_key, session_id, llama_template, supabase_url, supabase_key)
                worker_args_snowballs.append(args_tuple)

            
            # finally take the papers that the MAs cited and prosess them.
            for _ in worker_thread.starmap(worker_args_snowballs):
                
                status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
                if status_check.data and status_check.data[0]["status"] == "killed":
                    yield {"type": "log", "phase": "1", "message": "🚨 Kill signal received. Aborting Snowball Downloads."}
                    return
                
                completed_3 += 1 
                yield {"type": "progress", "message": "Downloading Snowballs", "current": completed_3, "total": total_snowballs, "phase": "1"}

                
                    
        time.sleep(2)
        
        yield {"type": "done", "message": f"Phase 1 Complete!", "phase": "1"}
        
        
       #Phase 2
        yield {"type": "ui", "action": "subheader", "text": "Phase 2: Targeted Keyword Strike", "phase": "2"}
        ui_queue = queue.Queue()
        def process_keyword(query, idx):
            "define expactly what it takes to prosses one keyword so we cna then have each of those get threaded then each of those uses starmap to make tons of contaners"
            try:
                ui_queue.put({"type": "log", "message": f" 🚀 Launching Parallel Thread for: {query}", "phase": "2"})
                target_filter = 'has_pdf_url:true'
                # fill the pool of papers to download add the seeds snowballed addditions to it and proccess them all.
                papers_to_download = fetch_openalex_papers( 
                        search_query=query, 
                        filter_query=target_filter, 
                        max_papers=max_papers, 
                        is_phase_1=False, 
                        llama_template=llama_template,
                        client=ai_client, 
                        debate_sectors = debate_sectors
                    )
                if not papers_to_download:
                        ui_queue.put({"type": "log", "message": f" ⏭️ No relevant papers found for '{query}'. Skipping...", "phase": "2"})
                        return
                snowballed_additions = []
                seeds_to_process = papers_to_download[:7]  
                
                
                citation_args_p2 = []
                for s_idx, (doi, title, pdf_url, openalex_id) in enumerate(seeds_to_process, start=1):
                    ui_queue.put({"type": "log", "message": f" Deploying Modal cloud workers for elite seed #{s_idx}...", "phase": "2"})
                    citation_args_p2.append((openalex_id, title, groq_api_key, llama_template))

                for approved_batch in phase2_citation_worker.starmap(citation_args_p2):
                   
                    status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
                    if status_check.data and status_check.data[0]["status"] == "killed":
                        ui_queue.put({"type": "log", "phase": "2", "message": "🚨 Kill signal received. Aborting Phase 2 Seed Processing."})
                        return
                    
                    if approved_batch:
                        snowballed_additions.extend(approved_batch)

                papers_to_download.extend(snowballed_additions)
                total_papers = len(papers_to_download)

                if total_papers > 0: 
                                
                    completed_p2 = 0
                    
                    
                    worker_arguments = []
                    for index, (doi, title, pdf_url, openalex_id) in enumerate(papers_to_download, start=1):
                        
                        
                        args_tuple = (doi, title, pdf_url, index, total_papers, False, groq_api_key, session_id, llama_template, supabase_url, supabase_key)
                        
                        worker_arguments.append(args_tuple)

                    for _ in worker_thread.starmap(worker_arguments):
                       
                        status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
                        if status_check.data and status_check.data[0]["status"] == "killed":
                            ui_queue.put( {"type": "log", "phase": "2", "message": "🚨 Kill signal received. Aborting Phase 2 Downloads."})
                            return
                        # ---------------------------
                        completed_p2 += 1
                        ui_queue.put( {"type": "progress", "message": f"Scraping '{query}'", "current": completed_p2, "total": total_papers, "phase": "2"})
                                            
                        time.sleep(1) 
                else:
                    print("No papers found for this keyword")   
                    return
            except Exception as e:
                ui_queue.put({"type": "log", "message": f" ⚠️ Error processing keyword '{query}': {e}", "phase": "2"})
        
        
        active_threads = []
        for idx, query in enumerate(keywords, start=1):
            t = threading.Thread(target=process_keyword, args=(query, idx))
            t.start()
            active_threads.append(t)
            
            time.sleep(12)


        while any(t.is_alive() for t in active_threads) or not ui_queue.empty():
            try:
                msg = ui_queue.get(timeout=0.5)
                yield msg
            except queue.Empty:
                pass

        if os.path.exists(OUTPUT_DIR):
            yield {"type": "done", "message": f"★ Master Pipeline Complete! XMLs saved in '{OUTPUT_DIR}'.", "phase": "2"}
        if os.path.exists(OUTPUT_DIR):
            yield {"type": "done", "message": f"★ Master Pipeline Complete! XMLs saved in '{OUTPUT_DIR}'.", "phase": "2"} 
            
    
    return "Scraping complete!"