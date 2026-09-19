
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




scraper_image = (
    modal.Image.debian_slim()
    .pip_install("requests", "cloudscraper", "beautifulsoup4", "playwright", "openai", "supabase")
    .run_commands(
        "playwright install-deps chromium", 
        "playwright install chromium"
    )
)

app = modal.App("debate-scraper")

# --- 1. DEFINE GROBID IN THE CLOUD ---
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
# tells ollama that thats what ur running an maybe starting a server loclaly? lemme know also tells what model

    
print(" Modal Cloud Server initialized with OpenAI SDK -> Groq Endpoint!")
    
LLAMA_MODEL = "llama3.1"
# so this is like some scraping stuff and it makes a scraper. Thing is though im not 100% what all this means so please explain why we need a scraper what it is and what the browing being set to does
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)
#again headers not exactly sure what they are idk
scraper.headers.update({
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://google.com"
})
# now we will get TONS of papers and many will have absolutly nopthign to do wit the topic is llama audits are the best way to filter them this is the local llama funtion
# --- 3. HELPER FUNCTIONS ---adw SCZX
# so here are the input varibles to this funtion so the llama cna decide if its a relivant paper based on title and abstract. 2 questions though why set abstract="" when you dont do that to title?
# second why set template_string=llama_prompt_template when you could jsut do that when you call the function? is this because it will always be that? if so does that effect all the other times its called?

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
    
    # 1. Build the master prompt with all abstracts
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
            # 2. Clean markdown and parse JSON
            if raw_text.startswith("```json"): raw_text = raw_text[7:-3]
            elif raw_text.startswith("```"): raw_text = raw_text[3:-3]
            
            import json
            decisions = json.loads(raw_text)
            
            # 3. Match the True/False decisions back to the original papers
            approved = []
            for paper, is_good in zip(papers_batch, decisions):
                if is_good:
                    print(f"    [Groq Batch: YES] -> Title: {paper[1][:50]}...")
                    # Drop the abstract (paper[4]) so it stays a 4-item tuple!
                    approved.append((paper[0], paper[1], paper[2], paper[3]))
                else:
                    print(f"    [Groq Batch: NO]  -> Title: {paper[1][:50]}...")
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

def verify_with_local_llama(title, abstract="", template_string="", client=None):
    # STRICT AI-ONLY SCREENING (No keyword bypass!)
    prompt = template_string.format(title=title, abstract=abstract)
    max_attempts = 6 
    
    for attempt in range(max_attempts):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0
            )
            
            decision = response.choices[0].message.content.strip().upper()
            
            if "YES" in decision:
                print(f"    [Groq Llama: YES] -> Title: {title[:50]}...")
                return True
                
            if "NO" in decision:
                print(f"    [Groq Llama: NO]  -> Title: {title[:50]}...")
                return False
                
        except Exception as e:
            if "429" in str(e) or "Rate limit" in str(e):
                # THE JITTER FIX: Random sleep between 3 and 10 seconds.
                # This stops the 15 containers from waking up and crashing all at once.
                staggered_sleep = random.uniform(8, 15)
                print(f"    [Groq Rate Limit] Thread sleeping for {staggered_sleep:.1f}s to stagger load...")
                time.sleep(staggered_sleep)
                continue
            else:
                print(f"    [Groq Error] Unhandled error: {e}")
                break 
                
    print(f"    [Groq Failed] Exhausted retries for: {title[:50]}... Skipping to protect data quality.")
    # If the AI can't verify it, we REJECT it. We do NOT fall back to simple keywords!
    return False
#this as said above it not in use anymore and jsut return the keword and weill bea masive headache to remove and im busy RN so who cares
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

def get_native_backward_citations(openalex_id, max_backward=3):
    """Natively retrieves the top cited papers from a seed paper's bibliography with safe guards."""
    print(f"    [Snowball] Running Backward Citation lookup for seed {openalex_id}...")
    #dont know what an open alex ID is or why we put it at the end of url. WAIT is the open alex ID the ID of the paper we wnat to bakcword cite? by added the ID to the end then does it get that exact paper for requests?
    url = f"https://api.openalex.org/works/{openalex_id}"
    
    try:
        # scraper.get? what exactly deos this do? does it send a request to the URL? why do we need the URL dont we already have it?
        #if this fails whatecver it is it returns and empty list? OH cus if the error code isnt 200 it failed so dont send anything
        #NOW I KNOW scraper.get(url) sends the request with out headers from above
        res = scraper.get(url, timeout=15)
        if res.status_code != 200:
            return []
        #so i get res.json but why have {}? I know now its a backup for diffrent formating  then it takes tha json block and fins the refrence works wich is problably the part of open alex with th refreances
        res_json = res.json() or {}
        referenced_ids = res_json.get("referenced_works") or []
        #then only take the first max_backward*2 of these ids
        referenced_ids = referenced_ids[:max_backward*2]
        #if theres no refrence also return nothing
        if not referenced_ids:
            return []
        #why have filter string? why join with "|" dont you already have the ids?
        #I know now you join it with that because the means or and so you search al lthe papers at ones
        filter_string = "|".join(referenced_ids)
        # this is how you get all the works that this paper cited
        query_url = f"https://api.openalex.org/works?filter=openalex:{filter_string}"
        # then you request the scraper for the aper with the query URL with all the IDs of the papers you wnat?
        batch_res = scraper.get(query_url, timeout=15)
        candidates = []
        # if batch res worked get its json results(again why have {}?) then loop through each paper in results. 
        if batch_res.status_code == 200:
            batch_data = batch_res.json() or {}
            results = batch_data.get("results") or []
            for paper in results:
                # if not paper or not isinstance(paper, dict): this line double confuses me. one wont it always be paper because paper was jsut defined abovein for paper in results: and second what does this do? or not isinstance(paper, dict):
                # this just checks if the paper is in the format we wnat(a dict)
                if not paper or not isinstance(paper, dict):
                    continue
                #take the papers title it abstract if it has one if not just leave it empty get the doi and remove the doi.org par for some reson??
                title = paper.get("title")
                abstract = paper.get("abstract") or ""
                doi = (paper.get("doi") or "").replace("https://doi.org/", "")
                # i get this one below is for the PDF but not exactly sure how
                pdf_url = (paper.get("open_access") or {}).get("oa_url")
                
                #you split to jsut get the end of the ID wich is what we want
                paper_id = paper.get("id", "").split("/")[-1] if paper.get("id") else ""
                #if it has a pdf url and a paper id and a title then add a big touple to a big list?
                if title and pdf_url and paper_id:
                    candidates.append((doi, title, pdf_url, paper_id, abstract))
                    
        return candidates#return that big list
    
    except Exception as e:
        print(f"    [Snowball Error] Backward traversal failed: {e}")
        return []

def get_native_forward_citations(openalex_id, max_forward=3):
    """Natively finds newer academic works that have cited our seed paper with safe guards."""
    print(f"    [Snowball] Running Forward Citation lookup for seed {openalex_id}...")
    # this below is the URL that tell open alex to find all the papers the cited this paper: openalex_id
    url = f"https://api.openalex.org/works?filter=cites:{openalex_id}&sort=cited_by_count:desc"
    
    try:
        #sends the request with the open alex id of the orignal paper
        res = scraper.get(url, timeout=15)
        candidates = []
        # if it works then get the fay with json iterate through the first :max_forward*2] 
        if res.status_code == 200:
            res_data = res.json() or {}
            results = res_data.get("results") or []
            for paper in results[:max_forward*2]:
                #check if formatted right then ger title abstract doi and paper id jsut like how the backword got it
                if not paper or not isinstance(paper, dict):
                    continue
                title = paper.get("title")
                abstract = paper.get("abstract") or ""
                doi = (paper.get("doi") or "").replace("https://doi.org/", "")
                pdf_url = (paper.get("open_access") or {}).get("oa_url")
                paper_id = paper.get("id", "").split("/")[-1] if paper.get("id") else ""
                # if the title exist append it to the canidantes
                if title and pdf_url and paper_id:
                    candidates.append((doi, title, pdf_url, paper_id, abstract))
               #return those canidits     
        return candidates
    
    except Exception as e:
        print(f"    [Snowball Error] Forward traversal failed: {e}")
        return []

def extract_citations_from_xml(xml_text):
    """Extracts all cited titles from GROBID TEI-XML, including articles, books, and reports."""
    #sets soup to edit XML
    try:
        soup = BeautifulSoup(xml_text, 'xml')
    except Exception:
        soup = BeautifulSoup(xml_text, 'html.parser')
    extracted_titles = []
    # takes the XML text and finds all the bibliographies
    for bibl in soup.find_all(['biblStruct', 'biblstruct']):
        # Grabs article titles (a), book/monograph titles (m), or generic title tags
        # above explains pretty well even though i didnt write it level = "a" is artucle  m is  book and then there generic titles
        title_tag = bibl.find('title', level='a') or bibl.find('title', level='m') or bibl.find('title')
        # checks if the title exists and if it has anything in it
        if title_tag and title_tag.text.strip():
            #  but i know the rest strips the title and replaces all the newlins with spaces?
            clean_title = title_tag.text.strip().replace("\n", " ")
            if len(clean_title) > 12:  # Lowered length threshold to capture short titles WHY? why do we only have short titles? doesnt this make less papers? am i missing smt?
                extracted_titles.append(clean_title)
                #appends and returns all the titles! in a set to remove dulicates.
    return list(set(extracted_titles))

def fetch_openalex_papers(search_query=None, filter_query=None, max_papers=5, is_phase_1=False, llama_template="", client=None, debate_sectors = None):
    """Fetches papers from OpenAlex with clean API parameter handling and safe fallback guards."""
    
    fetch_limit = max_papers * 4 if not is_phase_1 else max_papers * 3
    
    # 1. Set up the Polite Pool with my email
    params = {
        "per-page": fetch_limit,
        "mailto": "joesticky48@gmail.com"  
    }
    
    # 2. Add filters and search queries if they exist
    if filter_query:
        params["filter"] = filter_query
        
    if search_query:
        params["search"] = search_query

    # 3. Send the request
    # 3. Send the request with a Stubborn Retry Loop
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
            
    # --- THE "SKIP TO NEXT KEYWORD" FIX ---
    # If after all 3 retries we STILL don't have a successful response, return an empty list.
    # Returning [] tells your main pipeline: "Nothing found here, skip to the next keyword!"
    if not response or response.status_code != 200:
        print(f"  [System] OpenAlex failed after {max_retries} attempts. Skipping to next keyword...")
        return []

    # If it succeeded, load the data!
    try:
        data = response.json() or {}
        raw_results = data.get('results', []) or []
        
        
        elite_issns = set()
        #take the ISSNS(whatever those are) from the elit list of journal and put them in a set
        for sector_info in debate_sectors.values():
            elite_issns.update(sector_info.get("issns", []))
            
        scored_pool = []
        #iterate through each paper
        for paper in raw_results:
            if not paper or not isinstance(paper, dict):
                continue
                # get the title abstrac doi and openalex id you must split by / and take the last one because that is here the id is
            title = paper.get('title') or ''
            abstract = paper.get('abstract') or ''
            doi = (paper.get('doi') or '').replace('https://doi.org/', '')
            openalex_id = paper.get('id', '').split('/')[-1] if paper.get('id') else ''
            #if theres no title skip
            if not title or not openalex_id:
                continue
            # get the relevance score from open alex and if there is none set it to 1
            base_relevance = paper.get("relevance_score") or 1.0
            
            # --- LOCATION PRIORITY SORTING (REPOSITORIES FIRST) ---
            # so i think this is where you get all the locations of the paper and if there are non you get the primary location whatever that is
            locations = paper.get("locations") or []
            if not locations:
                locations = [paper.get("primary_location") or {}]
                
            pdf_url = None
            paper_issns = []
            
            # Step 1: Scan all locations for ISSNs and prioritize direct Repositories
            for loc in locations:
                #iterate through each location for each paper and check if it exists and if it is formated right if not skip it
                if not loc or not isinstance(loc, dict):
                    continue
                    # so im not too familiar with open allexes json but i think the source is like the journal and linked to the soucre is th issn
                source = loc.get("source") or {}
                issn_list = source.get("issn") or []
                # if the issn is a list(idk why it would) you extent the lsit whereas if its a string you append
                if isinstance(issn_list, list):
                    paper_issns.extend(issn_list)
                elif isinstance(issn_list, str):
                    paper_issns.append(issn_list)
                # dont know exactly what target URL does
                target_url = loc.get("pdf_url") or loc.get("landing_page_url") or ""
                
                # Check if this location is a known un-paywalled repository
                if target_url and loc.get("is_oa"):
                    repo_domains = ["arxiv", "hal.", "zenodo", "repository", "purl", "nih.gov", "citeseerx", ".edu/"]
                    # i know this line below  checks if any of the locations are the free ones listed above but idk exactly how it works
                    if any(domain in target_url.lower() for domain in repo_domains):
                        pdf_url = loc.get("pdf_url") or target_url # if theres a free one we take its pdf url?
                        break  # Found a clean repository link! Skip commercial paywalls.

            # Step 2: Fallback to the standard Open Access URL if no repository was found
            if not pdf_url:# if the anti payway flailed take it from open alexes OA url section of the json
                pdf_url = (paper.get('open_access') or {}).get('oa_url')
                
            if not pdf_url: # if still nothing then continue
                continue
            
            has_prestige_boost = any(issn in elite_issns for issn in paper_issns) # it juts a prestige boost if the paper has has an issn thats in the elite issn list
            boosted_score = base_relevance * 1.5 if has_prestige_boost else base_relevance # if it has the prestige boost it gets multiplied by 1.5 if not it gets its base
            # then find the primary location(why?) and the journal name or if its an unkown journal find that
            primary_loc = paper.get("primary_location") or {}
            source_info = primary_loc.get("source") or {}
            journal_name = source_info.get("display_name", "Unknown Journal")
            # then take the scored pool list and add in a big dictionary of all the info about this paper
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
            # not sure what this does maybe it sorts by score but not sure how?
        scored_pool.sort(key=lambda x: x["local_score"], reverse=True)
        # now this is the final lsit to get all the papers 
        # 1. Convert the dictionary pool into tuples so it works with your batch function
        all_candidates = [(item["doi"], item["title"], item["pdf_url"], item["openalex_id"], item["abstract"]) for item in scored_pool]
        
        found_papers = []
        chunk_size = 15
        max_failed_batches = 2
        consecutive_failed_batches = 0
        # 2. Loop through them in batches of 10
        for i in range(0, len(all_candidates), chunk_size):
            if len(found_papers) >= max_papers:
                break
                
            batch = all_candidates[i:i + chunk_size]
            print(f"  [*] Screening OpenAlex batch of {len(batch)} via Llama...")
            
            # Use your batch function!
            approved_batch = batch_verify_with_local_llama(batch, llama_template, client)
            
            # Add approved ones to the final list
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
    
def scrape_publisher_page(target_url, log_prefix): 
    import asyncio
    import sys
    
    if sys.platform == 'win32':# so if im on windows 32 somthing happends? i really dont know I know now it allows playwrite to communicate with iths brower wich noramllt cant be dont in windows
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    # this loop here start an loop event so that the threads can comunicate   
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print(f"    {log_prefix} [-] Deploying Headless Browser to bypass JS at {target_url[:40]}...")
    html_content = ""
    final_url = target_url
    # okay it uses sync_playwright to tell headless browser what to do
    # then it sets a "page" thats goes to a'newpage' wich is baically an empty chrome tabb then it navigates to the website
    #then it dowloads the content? if it doesnt do that within the timeout it skips?
    # with that  content it then finds a url? somehow? 
    #then it closes the browser this is the part i understand the least.
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = None
        try:
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page.goto(target_url, timeout=10000, wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            # it takes the contents of the website and dowloads it and saves it to a variable
            html_content = page.content()
            final_url = page.url
            #return html_content
        
        except Exception as e:
            if "Download is starting" in str(e):
                print(f"    {log_prefix} [✓] Playwright intercepted a direct PDF download!")
                return target_url
                # Handle your download logic here
            else:
                print(f"    {log_prefix} [X] Playwright error: {e}")
                return None
        finally:
            if page is not None:
                page.close()
            # Always close the browser, no matter what happened above
            if browser is not None:
                browser.close()
    

    try:
        # then it takes the content from the html content varible from above and looks for specfic meta tags
        #what are these tags? do i need to know then?
        soup = BeautifulSoup(html_content, 'html.parser')# tells sup to use its html parser"mode"
        # this for stament below confuses me please walk me through it
        meta_tags = ['citation_pdf_url', 'eprints.document_url', 'dc.identifier']
        for tag in meta_tags:
            #searches for common meta tags that are associated with
            pdf_meta = soup.find('meta', {'name': tag}) or soup.find('meta', {'property': tag})
            if pdf_meta and pdf_meta.get('content') and 'pdf' in pdf_meta.get('content').lower():
                return urljoin(final_url, pdf_meta.get('content'))
                
        for link in soup.find_all('a', href=True):# does this check if there is a pdf in the soup?
            href = link['href']
            if href.lower().endswith('.pdf') or 'downloadpdf' in href.lower() or '/pdf/' in href.lower():
                return urljoin(final_url, href)

        matches = re.compile(r'(https?://[^\s"\'<>]+?\.pdf)', re.IGNORECASE).findall(html_content)# this is fallbakc number two by mimicing a PDF link it scans it
        if matches:
            return matches[0] # return the first match from the regex
            
        print(f"    {log_prefix} [X] Publisher site fully rendered, but no free PDF is accessible.") # tells the user it failed
        return None
    except Exception:
        return None

def download_and_process(pdf_url, title, safe_title, log_prefix, check_list = None, is_anchor=False, depth=0, client=None, session_id = None, llama_template = None):
    if not pdf_url or depth > 1: return # if the depth(still need more explination on why depth would go up) is to high or if there isnt a pdf url jsut return
    # these three lines below confuse me what is dowloaded pdfs?
    PDF_DIR = "downloaded_pdfs"# name of the folder where pdfs are stored
    os.makedirs(PDF_DIR, exist_ok=True) # makes the aformentioned folder
    pdf_path = os.path.join(PDF_DIR, f"{safe_title}.pdf")# name of the file that the paper is in

    # Add this safety check before downloading
    if not pdf_url.startswith("http"):
        print(f"    {log_prefix} [X] Invalid URL detected. Skipping.")
        return {"status": "skipped", "reason": "Invalid URL"}

    try:
        response_pdf = scraper.get(pdf_url, timeout=8) 
    except Exception as e:
        print(f"    {log_prefix} [X] File download timed out or failed: {e}")
        return {"status": "failed", "error": str(e)}

    

    content_type = response_pdf.headers.get('Content-Type', '').lower() # gets "content type" to determine if its PDF and if it is then feed it to grobid
    
    if response_pdf.status_code == 200 and 'application/pdf' in content_type: # ohhh os it its a PDF and the scraping worked  go forward
        try:
            with open(pdf_path, 'wb') as f:# makes the filepath for where the pdf is. uses Write Binary because we are handling PDFs
                f.write(response_pdf.content)# then write the content of the PDF into the file to save for later
        except Exception as e:
            print(f"    {log_prefix} [!] Warning: Could not write PDF file to disk: {e}")

        file = io.BytesIO(response_pdf.content) # creates a ram based copy of the PDF for grobid 
        files = {'input': ('paper.pdf', file, 'application/pdf')}#packages in maw
        try:
            response_grobid = requests.post(GROBID_URL, files=files, timeout=300)# try to send a request to grobid with the file
            
            if response_grobid.status_code == 200: 
                file_path_in_cloud = f"{session_id}/{safe_title}_output.xml"
                try:
                    # ONE clean upload command with the session_id path and upsert turned on
                    supabase.storage.from_("paper_xmls").upload(
                        path=file_path_in_cloud,
                        file=response_grobid.text.encode("utf-8"),
                        file_options={
                            "content-type": "application/xml",
                            "upsert": "true"  # This tells Supabase to overwrite instead of throwing a 409 crash
                        }
                    )
                    
                except Exception as upload_err:
                    # If a 409 duplicate error somehow still happens, catch it and print a friendly message!
                    if "409" in str(upload_err) or "Duplicate" in str(upload_err):
                        print(f"    [i] Paper already exists in this run's folder. Skipping duplicate.")
                    else:
                        print(f"    [!] Storage Upload Error: {upload_err}")
                if is_anchor:# not sure what anchor is but is it the boolean that tell it wheather or not to snowball?
                    print(f"    {log_prefix} [!] Anchor Analysis Complete. Unpacking snowball references...")
                    
            else:
                print(f"    {log_prefix} [!] GROBID returned status code {response_grobid.status_code}")
         # now notify the user of erros execpt idke exactly how lones like except requests.exceptions.Timeout: work       
        except requests.exceptions.Timeout:
            print(f"    {log_prefix} [!!!] GROBID TIMEOUT: Paper took > 45s to parse. Skipped XML extraction.")
        except requests.exceptions.ConnectionError:
            print(f"    {log_prefix} [!!!] FATAL: Cannot connect to GROBID Docker!")
        except Exception as e:
            print(f"    {log_prefix} [!] Unexpected GROBID error: {e}")
            
    elif 'text/html' in content_type: # this is speculation so feel free to correct me but if its text scrape the page and we get a new pdf link dowload nad prossess that?
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

# Add the app.function decorator so Modal knows this is a cloud endpoint
@app.function(image=scraper_image, cpu=1.0, memory=2048, timeout=600, max_containers=15)
def worker_thread(doi, title, pdf_url, index, total_papers, is_anchor, groq_api_key, session_id, llama_template, supabase_url, supabase_key): # <--- Added Supabase variables here
    
    # 1. Initialize the Groq client INSIDE the worker
    ai_client = OpenAI(
        api_key=groq_api_key,
        base_url="https://api.groq.com/openai/v1",
        timeout=30.0
    )
    
    
    # 2. Initialize Supabase INSIDE the worker
    global supabase
    supabase = create_client(supabase_url, supabase_key)
    
    # --- KILL SWITCH CHECK 1 ---
    try:
        status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
        if status_check.data and status_check.data[0]["status"] == "killed":
            print(f" 🚨 Kill signal received in worker. Aborting paper {title[:30]}")
            return
    except Exception as e:
        print(f"and error occured {e}")
        
    # ---------------------------
   
    try:
        log_prefix = f"[P{index}/{total_papers}]"
        # Clean the title safely, but prepend the doi  so it's ALWAYS unique!
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
        # If ANYTHING goes catastrophically wrong with this single paper, it fails safely here.
        print(f"  [!] Worker thread completely failed for paper '{title[:30]}': {e}") # <--- Added client=client # thne take th pdf url from sracpe pulisher page and attempt to dowload it?

@app.function(image=scraper_image, cpu=1.0, timeout=600, max_containers=15)
def extra_worker_thread(openalex_id, title, groq_api_key, llama_template):
    # Initialize the Groq client here too!
    ai_client = OpenAI(api_key=groq_api_key, base_url="https://api.groq.com/openai/v1",timeout=30.0)
    
    
    Final_snowball_papers = []
    backword_potentials = get_native_backward_citations(openalex_id, max_backward=15)
    print(f"fetching backword citations for {title}")
    
    # Batch them!
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
    
    # 1. Pull both backward and forward citations
    backword_citations = get_native_backward_citations(openalex_id, max_backward= 15)
    forwar_citaionts = get_native_forward_citations(openalex_id, max_forward= 15)
    total_citaions = backword_citations + forwar_citaionts

    if not total_citaions:
        return []

    # 2. STRUCTURE THE BATCHES: Break the list into chunks of 10
    batches = []
    chunk_size = 10
    for i in range(0, len(total_citaions),chunk_size):
        chunk = total_citaions[i : i + chunk_size]
        batches.append(chunk)
    
    # 3. CALL THE BATCH VERIFY: Send each chunk of 10 to Groq all at once
    for batch in batches:
        approved_in_batch = batch_verify_with_local_llama(batch, llama_template, ai_client)

        
        # Add the approved papers to our final list
        
        if approved_in_batch:
            snowballed_additions.extend(approved_in_batch)

    return snowballed_additions

 # Gives it a 1-hour timeout so it doesn't crash on huge jobs
# Inside modal_worker.py

@app.function(
    image=scraper_image,
    memory=8192,     # Increase RAM allocation to 8GB (8192 MB)
    timeout=3600,    # Extend execution timeout to 1 hour (3600 seconds)
    cpu=2.0          # Give it 2 full CPU cores
)

def run_full_pipeline(keywords, groq_api_key, max_anchors, max_papers, max_threads, phase1_search, phase1_filter, llama_template, debate_sectors, session_id, supabase_url, supabase_key):
    
    print(" Modal Cloud Server has woken up!")

    worker_thread.update_autoscaler(max_containers=max_threads)
    extra_worker_thread.update_autoscaler(max_containers=max_threads)
    phase2_citation_worker.update_autoscaler(max_containers=max_threads)

    ai_client = OpenAI(
        api_key=groq_api_key,
        base_url="https://api.groq.com/openai/v1"
        ,timeout=30.0
    )

    # Initialize Supabase inside Modal using the credentials Streamlit passed over
    # Initialize Supabase inside Modal using the credentials Streamlit passed over
    global supabase  # <--- ADD THIS LINE
    supabase = create_client(supabase_url, supabase_key)
    print("🗄️ Supabase client successfully initialized in the cloud!")
    
    print(f"Keywords received: {keywords}")

    # Initialize the AI client INSIDE the function so it has the key!
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
    #yield {"type": "ui", "action": "info", "message": "Pipeline active!"}
    
    yield {"type": "ui", "action": "subheader", "text": "Phase 1: Meta-Analyses & Snowballing", "phase": "1"}
    
    # Enforces strict nuclear keywords while explicitly excluding medical/biology terms
    phase1_search = phase1_search # add in the nessisary filters so responses stay on topic
    phase1_filter = phase1_filter
    
    anchor_papers = fetch_openalex_papers( 
    search_query=phase1_search,
    filter_query=phase1_filter, 
    max_papers=max_anchors, 
    is_phase_1=True, 
    llama_template=llama_template,
    client=ai_client, # <--- Added here
    debate_sectors = debate_sectors
)

    total_anchors = len(anchor_papers) # find the length for use in the progress bar and to notify the user
    
    if total_anchors > 0: # if there are more than 0 papers set the progress to 0 and the amount completed to zero.

        
        completed_p1 = 0
        
        Final_snowball_papers_phase_1 = []
        # 1. Package all arguments including Supabase keys for Phase 1 anchors
        worker_args_p1 = []
        for index, (doi, title, pdf_url, openalex_id) in enumerate(anchor_papers, start=1):
            args_tuple = (doi, title, pdf_url, index, total_anchors, True, groq_api_key, session_id, llama_template, supabase_url, supabase_key)
            worker_args_p1.append(args_tuple)

        citation_args = []
        for index, (doi, title, pdf_url, openalex_id) in enumerate(anchor_papers, start=1):
            citation_args.append((openalex_id, title, groq_api_key, llama_template))

        anchor_job = worker_thread.starmap(worker_args_p1)
        citation_job = extra_worker_thread.starmap(citation_args)
        
        # 2. Run in parallel using Modal's starmap
        for _ in anchor_job:
            # --- KILL SWITCH CHECK 2 ---
            status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
            if status_check.data and status_check.data[0]["status"] == "killed":
                yield {"type": "log", "phase": "1", "message": "🚨 Kill signal received. Aborting Phase 1."}
                return
            # ---------------------------
            completed_p1 += 1
            yield {"type": "progress", "message": "Verifying and Dowloading Anchors", "current": completed_p1, "total": total_anchors, "phase": "1"}

        
        completed_p1point5 = 0   
       # 1. Build the list of arguments
        

        
        # 2. Run in parallel on Modal using .starmap()
        for approved_citations in citation_job:
            # --- KILL SWITCH CHECK 3 ---
            status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
            if status_check.data and status_check.data[0]["status"] == "killed":
                yield {"type": "log", "phase": "1", "message": "🚨 Kill signal received. Aborting Citation Harvest."}
                return
            # ---------------------------
            if approved_citations:
                Final_snowball_papers_phase_1.extend(approved_citations)
            
            completed_p1point5 += 1 
            yield {"type": "progress", "message": "Harvesting Citations", "current": completed_p1point5, "total": total_anchors, "phase": "1"}

            
        completed_3 = 0  
        Final_snowball_papers_phase_1 = list(set(Final_snowball_papers_phase_1))
        total_snowballs = len(Final_snowball_papers_phase_1)
        if total_snowballs > 0:
            # 1. Package all arguments including Supabase keys for the snowball papers
            worker_args_snowballs = []
            for index, (doi, title, pdf_url, openalex_id) in enumerate(Final_snowball_papers_phase_1, start=1):
                args_tuple = (doi, title, pdf_url, index, total_snowballs, False, groq_api_key, session_id, llama_template, supabase_url, supabase_key)
                worker_args_snowballs.append(args_tuple)

            
            # 2. Run in parallel using Modal's starmap
            for _ in worker_thread.starmap(worker_args_snowballs):
                # --- KILL SWITCH CHECK 4 ---
                status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
                if status_check.data and status_check.data[0]["status"] == "killed":
                    yield {"type": "log", "phase": "1", "message": "🚨 Kill signal received. Aborting Snowball Downloads."}
                    return
                # ---------------------------
                completed_3 += 1 
                yield {"type": "progress", "message": "Downloading Snowballs", "current": completed_3, "total": total_snowballs, "phase": "1"}

                
                    
        time.sleep(2)
        
        yield {"type": "done", "message": f"Phase 1 Complete!", "phase": "1"}
        #yield {"type": "ui", "action": "divider"}# anoice that phse 1 is dont then split the screen for a visual diffrence between the pahses
        
        # --- PHASE 2 ---
        yield {"type": "ui", "action": "subheader", "text": "Phase 2: Targeted Keyword Strike", "phase": "2"}
        ui_queue = queue.Queue()
        def process_keyword(query, idx):
            try:
                ui_queue.put({"type": "log", "message": f" 🚀 Launching Parallel Thread for: {query}", "phase": "2"})
                target_filter = 'has_pdf_url:true'
                papers_to_download = fetch_openalex_papers( 
                        search_query=query, 
                        filter_query=target_filter, 
                        max_papers=max_papers, 
                        is_phase_1=False, 
                        llama_template=llama_template,
                        client=ai_client, # <--- Added here
                        debate_sectors = debate_sectors
                    )
                if not papers_to_download:
                        ui_queue.put({"type": "log", "message": f" ⏭️ No relevant papers found for '{query}'. Skipping...", "phase": "2"})
                        return
                snowballed_additions = []
                seeds_to_process = papers_to_download[:7]  # Evaluate top 3 elite seeds per keyword
                
                # 1. Package the OpenAlex IDs into arguments
                citation_args_p2 = []
                for s_idx, (doi, title, pdf_url, openalex_id) in enumerate(seeds_to_process, start=1):
                    ui_queue.put({"type": "log", "message": f" Deploying Modal cloud workers for elite seed #{s_idx}...", "phase": "2"})
                    citation_args_p2.append((openalex_id, title, groq_api_key, llama_template))

                for approved_batch in phase2_citation_worker.starmap(citation_args_p2):
                    # --- KILL SWITCH CHECK 5 ---
                    status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
                    if status_check.data and status_check.data[0]["status"] == "killed":
                        ui_queue.put({"type": "log", "phase": "2", "message": "🚨 Kill signal received. Aborting Phase 2 Seed Processing."})
                        return
                    # ---------------------------
                    if approved_batch:
                        snowballed_additions.extend(approved_batch)

                papers_to_download.extend(snowballed_additions)
                total_papers = len(papers_to_download)

                if total_papers > 0: # if there are any start a progress bar for this keyword
                                
                    completed_p2 = 0
                    
                    # 1. Package all your arguments into a list of tuples
                    worker_arguments = []
                    for index, (doi, title, pdf_url, openalex_id) in enumerate(papers_to_download, start=1):
                        
                        # Add supabase_url and supabase_key to the very end of this list!
                        args_tuple = (doi, title, pdf_url, index, total_papers, False, groq_api_key, session_id, llama_template, supabase_url, supabase_key)
                        
                        worker_arguments.append(args_tuple)

                    for _ in worker_thread.starmap(worker_arguments):
                        # --- KILL SWITCH CHECK 6 ---
                        status_check = supabase.table("run_status").select("status").eq("session_id", session_id).execute()
                        if status_check.data and status_check.data[0]["status"] == "killed":
                            ui_queue.put( {"type": "log", "phase": "2", "message": "🚨 Kill signal received. Aborting Phase 2 Downloads."})
                            return
                        # ---------------------------
                        completed_p2 += 1
                        ui_queue.put( {"type": "progress", "message": f"Scraping '{query}'", "current": completed_p2, "total": total_papers, "phase": "2"})
                                            
                        time.sleep(1) # wait 1 second between papers
                else:
                    print("No papers found for this keyword")   
                    return
            except Exception as e:
                ui_queue.put({"type": "log", "message": f" ⚠️ Error processing keyword '{query}': {e}", "phase": "2"})
        # start phase 2 and anoince it
        
        active_threads = []
        for idx, query in enumerate(keywords, start=1):
            t = threading.Thread(target=process_keyword, args=(query, idx))
            t.start()
            active_threads.append(t)
            
            time.sleep(12)

        # ==========================================
        # YIELD FROM THE QUEUE UNTIL FINISHED
        # ==========================================
        # This keeps Streamlit alive and updating while the background threads run wild
        while any(t.is_alive() for t in active_threads) or not ui_queue.empty():
            try:
                # Grab a message from the queue and instantly send it to the Streamlit UI
                msg = ui_queue.get(timeout=0.5)
                yield msg
            except queue.Empty:
                pass

        if os.path.exists(OUTPUT_DIR):
            yield {"type": "done", "message": f"★ Master Pipeline Complete! XMLs saved in '{OUTPUT_DIR}'.", "phase": "2"}
        if os.path.exists(OUTPUT_DIR):
            #yield {"type": "ui", "action": "divider"}
            yield {"type": "done", "message": f"★ Master Pipeline Complete! XMLs saved in '{OUTPUT_DIR}'.", "phase": "2"} # if the output dir exest celibrate with balloon and tell the user its done!!
            
    
    return "Scraping complete!"