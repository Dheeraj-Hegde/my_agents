from fastmcp import FastMCP
import httpx
import json
import os
from datetime import datetime

mcp = FastMCP("CurrencyConverter")

NEWS_LOG = "currency_news.json"

def load_news():
    if os.path.exists(NEWS_LOG):
        with open(NEWS_LOG, "r") as f:
            try: return json.load(f)
            except: return {}
    return {}

def save_news(data):
    with open(NEWS_LOG, "w") as f:
        json.dump(data, f, indent=4)

@mcp.tool()
async def get_exchange_rate(base_currency: str = "USD", target_currency: str = "EUR") -> str:
    """Get the latest exchange rate between two currencies."""
    url = f"https://open.er-api.com/v6/latest/{base_currency.upper()}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code != 200:
            return f"Error: Could not fetch rates for {base_currency}."
        data = response.json()
        rate = data.get("rates", {}).get(target_currency.upper())
        return str(rate) if rate else "Error: Rate not found."

@mcp.tool()
async def capture_currency_news(currency: str, news_items: list[str]) -> str:
    """
    Capture and save the top 5 news items for a specific currency. (CREATE/UPDATE)
    Ensures only the latest 5 unique news items are kept in the file.
    """
    currency = currency.upper()
    all_news = load_news()
    current_currency_news = all_news.get(currency, [])
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Process news: Filter duplicates and maintain Top 5
    for article in news_items:
        if article not in [n['text'] for n in current_currency_news]:
            current_currency_news.insert(0, {"text": article, "time": timestamp})
    
    # Keep only top 5
    current_currency_news = current_currency_news[:5]
    all_news[currency] = current_currency_news
    save_news(all_news)
    
    return f"Successfully captured top {len(current_currency_news)} news items for {currency}."

@mcp.tool()
async def log_forex_data(base: str, target: str, price: float) -> str:
    """Log the requested forex pair and its current price."""
    log_file = "forex_history.json"
    data = []
    if os.path.exists(log_file):
        with open(log_file, "r") as f:
            try: data = json.load(f)
            except: data = []
    data.append({"pair": f"{base}/{target}", "price": price, "time": datetime.now().isoformat()})
    with open(log_file, "w") as f:
        json.dump(data, f, indent=4)
    return "Forex data logged."

@mcp.tool()
async def log_source_currency(currency: str) -> str:
    """Log the 'from' currency used in a request."""
    with open("from_currencies.txt", "a") as f:
        f.write(currency.upper() + "\n")
    return "Source currency logged."

@mcp.tool()
async def log_target_currency(currency: str) -> str:
    """Log the 'to' currency used in a request."""
    with open("to_currencies.txt", "a") as f:
        f.write(currency.upper() + "\n")
    return "Target currency logged."

if __name__ == "__main__":
    mcp.run()
