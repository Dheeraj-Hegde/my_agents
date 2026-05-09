import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import google.generativeai as genai
from dotenv import load_dotenv
import requests
import traceback
import logging
import json
import re
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("app.log"), logging.StreamHandler()]
)
logger = logging.getLogger("CurrAI")

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- TOOLS (Matching mcp_server.py logic) ---

def capture_currency_news(currency: str, news_items: list[str]) -> str:
    """Capture and save the top 5 news items for a specific currency."""
    logger.info(f"Tool Call: capture_currency_news({currency}, {news_items})")
    currency = currency.upper()
    news_file = "currency_news.json"
    all_news = {}
    if os.path.exists(news_file):
        try:
            with open(news_file, "r") as f: all_news = json.load(f)
        except: all_news = {}
    
    current_currency_news = all_news.get(currency, [])
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for article in news_items:
        if article not in [n['text'] for n in current_currency_news]:
            current_currency_news.insert(0, {"text": article, "time": timestamp})
    
    current_currency_news = current_currency_news[:5]
    all_news[currency] = current_currency_news
    with open(news_file, "w") as f: json.dump(all_news, f, indent=4)
    result = f"Captured {len(current_currency_news)} news items for {currency}."
    logger.info(f"Tool Result: {result}")
    return result

def log_forex_data(base: str, target: str, price: float) -> str:
    logger.info(f"Tool Call: log_forex_data({base}, {target}, {price})")
    data = []
    if os.path.exists("forex_history.json"):
        try:
            with open("forex_history.json", "r") as f: data = json.load(f)
        except: data = []
    data.append({"pair": f"{base}/{target}", "price": price, "time": datetime.now().isoformat()})
    with open("forex_history.json", "w") as f: json.dump(data, f, indent=4)
    result = "Forex data logged."
    logger.info(f"Tool Result: {result}")
    return result

def log_source_currency(currency: str) -> str:
    logger.info(f"Tool Call: log_source_currency({currency})")
    with open("from_currencies.txt", "a") as f: f.write(currency.upper() + "\n")
    result = "Source currency logged."
    logger.info(f"Tool Result: {result}")
    return result

def log_target_currency(currency: str) -> str:
    logger.info(f"Tool Call: log_target_currency({currency})")
    with open("to_currencies.txt", "a") as f: f.write(currency.upper() + "\n")
    result = "Target currency logged."
    logger.info(f"Tool Result: {result}")
    return result

def get_exchange_rate(base_currency: str, target_currency: str) -> str:
    logger.info(f"Tool Call: get_exchange_rate({base_currency}, {target_currency})")
    url = f"https://open.er-api.com/v6/latest/{base_currency.upper()}"
    try:
        res = requests.get(url)
        rate = res.json().get("rates", {}).get(target_currency.upper())
        result = f"{rate:.4f}" if rate else "Rate not found."
    except Exception as e:
        result = f"Error: {str(e)}"
    logger.info(f"Tool Result: {result}")
    return result

# --- MODEL SETUP ---
model = genai.GenerativeModel(
    model_name="models/gemma-4-26b-a4b-it",
    tools=[get_exchange_rate, log_forex_data, log_source_currency, log_target_currency, capture_currency_news],
    system_instruction=(
        "You are a professional currency expert with real-time news-capturing capabilities. "
        "CRITICAL: Do NOT show any thinking, planning, or logging confirmations. Only output the JSON."
        "MANDATORY WORKFLOW for every conversion request: "
        "1. Fetch the rate using get_exchange_rate. "
        "2. Provide exactly 5 relevant news headlines for the 'from' currency and call capture_currency_news. "
        "3. Provide exactly 5 relevant news headlines for the 'to' currency and call capture_currency_news. "
        "4. Log the forex data, source, and target currencies using their respective tools. "
        "5. Your final response MUST be a valid JSON object: {'answer': 'your response'}. "
        "CRITICAL: Do NOT show any thinking, planning, or logging confirmations. Only output the JSON."
    )
)

chat = model.start_chat(enable_automatic_function_calling=True)

class ChatRequest(BaseModel):
    message: str

def filter_response(text: str) -> str:
    text = text.strip()
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            json_str = match.group().strip().strip('`')
            data = json.loads(json_str)
            return data.get('answer', text)
    except: pass
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    for p in reversed(paragraphs):
        p_l = p.lower()
        if not any(p_l.startswith(w) for w in ["plan:", "thinking:", "i should", "the user", "workflow", "according to"]):
            return p
    return text.split('\n')[-1].strip()

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    try:
        logger.info(f"Received: {request.message}")
        response = chat.send_message(request.message)
        clean_text = filter_response(response.text)
        logger.info(f"Raw: {response.text}")
        logger.info(f"Cleaned: {clean_text}")
        return {"response": clean_text}
    except Exception as e:
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history/formatted")
async def get_history_formatted():
    history = load_history()
    return [
        {
            "time": h.get('time', 'N/A'),
            "pair": h.get('pair', 'Unknown'),
            "price": str(h.get('price', 'N/A')),
            "status": "Verified"
        }
        for h in reversed(history)
    ]

@app.get("/api/stats/formatted")
async def get_stats_formatted():
    # Load raw stats
    from_currs = []
    if os.path.exists("from_currencies.txt"):
        with open("from_currencies.txt", "r") as f:
            from_currs = [line.strip() for line in f if line.strip()]
    
    to_currs = []
    if os.path.exists("to_currencies.txt"):
        with open("to_currencies.txt", "r") as f:
            to_currs = [line.strip() for line in f if line.strip()]
            
    from_counts = {}
    for c in from_currs: from_counts[c] = from_counts.get(c, 0) + 1
    
    to_counts = {}
    for c in to_currs: to_counts[c] = to_counts.get(c, 0) + 1
    
    return {
        "from": [{"name": k, "value": v} for k, v in from_counts.items()],
        "to": [{"name": k, "value": v} for k, v in to_counts.items()]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
