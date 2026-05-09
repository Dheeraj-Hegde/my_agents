# CurrAI | Intelligent Forex Command Center

CurrAI is a high-performance currency conversion and market intelligence suite powered by **Gemma 4 (26B)** and the **Model Context Protocol (MCP)**. It provides a real-time conversational interface for currency operations alongside a premium executive dashboard for data-driven market insights.

## 🚀 Key Features

- **Conversational Intelligence:** Query Gemma 4 for live exchange rates, market analysis, and global financial trends.
- **Automated Data Capture:** Every conversion request automatically triggers background logging of:
    - **Forex History:** Transaction details and executed rates.
    - **Intelligence Signals:** Top 5 latest unique news headlines per currency.
    - **Usage Analytics:** Tracking of frequently requested source and target currencies.
- **Executive Prefab Dashboard:** A high-fidelity, Python-powered dashboard (`prefab_ui`) featuring:
    - **Strategic Analytics:** Dynamic PieCharts and BarCharts for market distribution.
    - **Intelligence Feed:** Real-time news signals captured by the AI.
    - **Audit Archive:** A searchable, paginated master log of all operations.
- **Clean Response Protocol:** Gemma 4 is strictly filtered to provide professional, JSON-only answers, removing all "Chain-of-Thought" monologue from the user interface.

## 🛠️ Architecture

- **Backend:** FastAPI (Python 3.10+) serving the AI chat and data showcase endpoints.
- **Intelligence:** Gemma 4 (26B) via the Google Gemini API.
- **MCP Server:** Custom Model Context Protocol server providing tools for:
    - `get_currency_rate`: Real-time exchange rate fetching.
    - `log_forex_data`: Persistent storage of transactions.
    - `capture_currency_news`: Intelligent rotation of top 5 news signals.
- **Frontend Suite:**
    - **Main Dashboard:** Glassmorphic HTML/JS/CSS interface.
    - **Executive Suite:** Generative UI built with `prefab-ui` for data-heavy visualization.

## 📋 Prerequisites

- Python 3.10+
- Google Gemini API Key (set in `.env`)
- Dependencies: `pip install -r requirements.txt`

## 🏃 Running the Suite

### 1. Start the Backend
The backend serves the AI and the data endpoints:
```powershell
python .\backend.py
```

### 2. Launch the Executive Dashboard
Use the Prefab CLI to serve the premium dashboard with hot-reloading:
```powershell
prefab serve prefab_dashboard.py --reload
```

### 3. Open the Main UI
Simply open `frontend/index.html` in your browser to start chatting with Gemma 4.

## 📂 Project Structure

- `backend.py`: FastAPI server logic and AI response filtering.
- `mcp_server.py`: Custom MCP tools for market data and logging.
- `prefab_dashboard.py`: Executive intelligence suite built with Prefab UI.
- `currency_news.json`: AI-captured intelligence feed.
- `forex_history.json`: Master transaction logs.
- `from_currencies.txt` / `to_currencies.txt`: Usage analytics raw data.
