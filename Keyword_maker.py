import streamlit as st
import re
from openai import OpenAI
import openai
import uuid
from supabase import create_client, Client 
from datetime import datetime

# Config

st.set_page_config(page_title="Counterpoint", layout="wide")
st.title(" AI Keyword Generator Workspace")


try:
   
    supabase_url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
    supabase_key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
    supabase: Client = create_client(supabase_url, supabase_key)
except Exception as e:
    st.error(f" Database connection failed: {e}")
    st.stop()

# set a specfic topic here in session state so we can access it in other page.
if "session_id" not in st.session_state:
    current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    st.session_state.session_id = f"{current_time}_{str(uuid.uuid4())}"

if "saved_topic" not in st.session_state:
    st.session_state.saved_topic = "The United States Federal Government should enact a policy to establish a comprehensive, high-speed rail network in the USA."

current_topic_input = st.session_state.saved_topic
if "final_keywords" not in st.session_state:
    st.session_state.final_keywords = []


with st.sidebar:
    st.header(" Generation Settings")
    
    # AI will generat sub niches related to the topic then those will be use to generate keywords. This is an easy way to get tons of relivant specfic keywords.
    num_sub_categories = st.slider("Number of Sub-Niches", min_value=1, max_value=10, value=6)
    num_keywords_per_niche = st.slider("Keywords per Niche", min_value=1, max_value=10, value=5)

    st.divider()
    
   
    # these are all the expanders that let the user change the AIs prompt if they want - all of these are optional
    with st.expander("Advanced: Edit System Prompts"):
        niche_system = st.text_area(
            "Sub-Niche System Prompt", 
            value="""You are an expert academic researcher. \nYour ONLY job is to output a list. Provide ZERO conversational filler. DO NOT say "Here are the categories".""",
            height=100
        )
        
        keyword_system = st.text_area(
            "Keyword System Prompt",
            value="""You are an expert academic researcher. \nYour ONLY job is to output a single, highly optimized search query for academic databases based on the user's topic.\nProvide ZERO conversational filler.""",
            height=120
        )
    user_api_key = st.text_input("Groq API Key:", type="password") 

        


  
    with st.expander("Advanced: Edit User Prompts"):
        st.caption("⚠️ Keep the curly brace placeholders like {topic} or {sub_niche} intact so the app can insert your settings variables dynamically.")
        
        niche_user_template = st.text_area(
            "Sub-Niche User Prompt Template",
            value="""Generate EXACTLY {num_sub_categories} highly specific sub-categories of this topic: "{topic}".

Zero Context. Return ONLY a list with each sub-category on a new line. Do not use commas or bullet points.

MOST IMPORTANT RULE: Make sure someone could read your sub-categories and immediately identify that they are specifically about "{topic}". 

Good Sub-Categories Example:
Specific, self-contained sub-topics that explicitly mention "{topic}" or its core components (e.g., "Economic Feasibility and Capital Costs of {topic} Deployment" or "Regulatory Challenges and Licensing Frameworks for {topic}").

Bad Sub-Categories Example:
'Economic development', 'Infrastructure funding', 'Policy frameworks'. (These are too broad and not easily recognizable as specifically belonging to {topic}).

Extra Rule: Do NOT use ambiguous or confusing acronyms. Always spell out full technical terms.

YOU MUST MAKE SURE YOUR RESPONSES ARE DIRECTLY AND CLEARLY RELATED TO: "{topic}".""",
            height=300
        )
       
        keyword_user_template = st.text_area(
            "Keyword User Prompt Template",
            value="""Content Focus: Generate EXACTLY {num_keywords_per_niche} concise, highly specific search phrases related to this sub-niche: "{sub_niche}".

Zero Context Policy: Provide ONLY the keywords. Do not include any introductory text, numbers, or conversational filler. 

MOST IMPORTANT RULE: Do NOT repeat the full sub-niche name in your output. These need to be short, punchy queries designed to be typed into academic databases like JSTOR or Google Scholar (3 to 15 words maximum).

Examples of good keywords:
'economic feasibility high-speed rail', 'regulatory policy impact transit', 'empirical cost-benefit analysis rail', 'deployment timeline regression'.

Examples of bad keywords:
'Regional economic growth indicators', 'Investment impact assessment', 'Policy framework analysis'. (These are too broad).

Formatting: Output your response matching the exact number of lines in this template below. Do not include extra lines.

Template to follow:
{dynamic_example}

YOUR RESPONSE MUST CONTAIN EXACTLY {num_keywords_per_niche} LINES FOR THE TOPIC: {sub_niche}""",
            height=350
        )
# maybe the most important few lines in the entire project without these each page would have no clue what the topic is and it would all break.
current_topic_input = st.text_input(
    " Main Topic or Resolution. Make sure to click enter to save.", 
    value=st.session_state.saved_topic
)

st.session_state.saved_topic = current_topic_input
# allow the user to put in keywords or subniches they really want covered. these will be tagged on to the end.
col1, col2 = st.columns(2)
with col1:
    manual_niches = st.text_area(
        "Add Custom Sub-Niches (Optional)", 
        placeholder="Eminent domain issues\nHigh-speed rail environmental benefits\n(Type each on a new line)",
        height=120
    )

with col2:
    manual_input = st.text_area(
        "Add Custom Keywords (Optional)", 
        placeholder="Specific author name 2024\nExact phrase search\n(Type each on a new line)",
        height=120
    )

if st.button(" Generate AI Keywords", type="primary"):
    if not user_api_key:
        st.error(" you dont have a API key! Go get one!")
        st.stop()

   
    custom_keywords = [k.strip() for k in manual_input.split('\n') if k.strip()]
    temp_keyword_list = custom_keywords.copy()
    custom_niches = [n.strip() for n in manual_niches.split('\n') if n.strip()]
    client = OpenAI(api_key=user_api_key, base_url="https://api.groq.com/openai/v1")
    MODEL = "openai/gpt-oss-20b"
    # make sub niches
    with st.spinner("Generating AI sub-niches..."):
        niche_prompt = niche_user_template.format(
            num_sub_categories=num_sub_categories, 
            topic=current_topic_input
        )

        try:
            niche_response = client.chat.completions.create(
                model=MODEL, 
                messages=[
                    {"role": "system", "content": niche_system},
                    {"role": "user", "content": niche_prompt}
                ]
            )

            ai_niche_text = niche_response.choices[0].message.content.strip()
            ai_sub_niches = [line.replace("- ", "").replace("* ", "").strip() for line in ai_niche_text.split('\n') if line.strip()]
            ai_sub_niches = ai_sub_niches[:num_sub_categories]
        
        except openai.RateLimitError as e:
            error_str = str(e)
            match = re.search(r"try again in\s+([^.]+)", error_str, re.IGNORECASE)
            wait_info = f"\n\n⏱️ **Time until reset / retry:** `{match.group(1).strip()}`" if match else ""
            
            st.error(f"⚠️ **Daily Token Limit Reached!**\n\nYour Groq API key has used up its free quota.{wait_info}\n\nPlease wait for the reset, or switch to a new API key in the sidebar.")
            st.stop()  
                    
        except Exception as e:
            st.error(f"API Connection Error: {e}")
            st.stop()

    all_sub_niches = ai_sub_niches + custom_niches # this how the user input gets seemlesly added.
    st.success(f"Combined {len(ai_sub_niches)} AI Niches and {len(custom_niches)} Custom Niches!")

    progress_bar = st.progress(0)
    
    # finally generate the keywords
    for i, sub_niche in enumerate(all_sub_niches):
        with st.spinner(f"Generating keywords for: {sub_niche}..."):
            dynamic_example = "\n".join([f"keyword {j+1}" for j in range(num_keywords_per_niche)])

            keyword_prompt = keyword_user_template.format(
                num_keywords_per_niche=num_keywords_per_niche,
                sub_niche=sub_niche,
                dynamic_example=dynamic_example
            )
            try:
                response = client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": keyword_system},
                        {"role": "user", "content": keyword_prompt} 
                    ]
                )
            except openai.RateLimitError as e:
                st.error("⚠️ **Daily Token Limit Reached!** You ran out of tokens while generating keywords. Please wait for the daily reset or use a new API key.")
                st.stop() 
            except Exception as e:
                st.error(f"API Connection Error during keyword generation: {e}")
                st.stop()
            ai_text = response.choices[0].message.content.strip()
            clean_lines = [line.strip() for line in ai_text.split('\n') if line.strip()]
            final_lines = clean_lines[:num_keywords_per_niche]
            
            for line in final_lines:

                cleaned_line = line.replace("- ", "").replace("* ", "").strip()
                cleaned_line = re.sub(r'^\d+\.\s*', '', cleaned_line)
                temp_keyword_list.append(cleaned_line)
                

        progress_bar.progress((i + 1) / len(all_sub_niches))

    st.session_state.final_keywords = temp_keyword_list
    
    with st.spinner(" Saving keywords to your cloud database..."):
  
        supabase.table("user_keywords").delete().eq("session_id", st.session_state.session_id).execute()
        
        
        for kw in temp_keyword_list:
            supabase.table("user_keywords").insert({
                "keyword": kw,
                "session_id": st.session_state.session_id
            }).execute()
            
    st.success(" Generation Complete and saved to the cloud!")

if st.session_state.final_keywords:
    st.divider()
    
    st.subheader(f" Final Keyword List ({len(st.session_state.final_keywords)} total)")
    
    formatted_list = "\n".join(st.session_state.final_keywords)
    
    st.code(formatted_list, language="text")
    # extra butons here for the user use.
    st.download_button(
        label=" Download Keywords as TXT",
        data=formatted_list,
        file_name="test_key.txt",
        mime="text/plain"
    )
    if st.button(" Clear Workspace", type="primary"):
        st.session_state.final_keywords = []
        st.rerun()
         

    