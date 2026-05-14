# 🏏 IPL Elite Analytics & Match Predictor

A high-performance, structured IPL match prediction system using a Gemini-powered multi-step reasoning protocol and a premium web dashboard.

## ✨ New in v2.1: Gemini AI Integration
The engine has been upgraded to use **Gemini 1.5 Flash** for dynamic, real-time reasoning. The AI now interacts directly with the MCP tools to gather live data and synthesize expert-level predictions.

---

## 🚀 Quick Start (Using `uv`)

This project uses `uv` for seamless dependency management.

### 1. Configure API Keys
Create a `.env` file in the root directory and add your Google Gemini API Key:
```env
GOOGLE_API_KEY=your_gemini_api_key_here
```

### 2. Start the Backend Server
The backend handles the communication between the Dashboard, Gemini AI, and the MCP tools:
```bash
uv run backend.py
```
*The backend will automatically launch and manage the MCP server internally.*

### 3. Launch the Web Dashboard
In a separate terminal, launch the interactive frontend:
```bash
uv run python -m http.server 8000 --directory web
```
Then open [http://localhost:8000](http://localhost:8000) in your browser.

---

## 🧠 Reasoning Protocol

The system follows a strict, evidence-based reasoning process managed by Gemini:

1.  **Data Gathering `[LOOKUP]`**: League standings, recent form, and injury reports via MCP.
2.  **Environmental Analysis `[LOGIC]`**: Venue history, pitch behavior, and live weather updates.
3.  **Head-to-Head & Matchups `[LOOKUP/LOGIC]`**: Historical records and critical player-vs-player battles.
4.  **Quantitative Projection `[ARITHMETIC]`**: Score projections calculated from team form and venue stats.
5.  **Final Synthesis `[SYNTHESIS]`**: Probabilistic weighting of all factors to determine a winner.

### 📝 The AI Reasoning Prompt

The following system prompt is used within `backend.py` to guide Gemini through the elite analysis protocol:

```text
You are an elite IPL Cricket Analyst. Predict the outcome of this match:
Team A: {team_a}
Team B: {team_b}
Venue: {venue}

Follow this strict Reasoning Protocol:
1. [LOOKUP] Get league standings and recent form.
2. [LOOKUP] Get injury reports for both teams.
3. [LOGIC] Analyze venue environment and weather.
4. [LOOKUP] Check Head-to-Head stats and key player matchups.
5. [ARITHMETIC] Calculate score projections based on data.
6. [SYNTHESIS] Provide a final win probability and verdict.

Final output MUST be a JSON object with this structure:
{
    "reasoning_steps": [
        {"tag": "[LOOKUP]", "title": "Step 1: Data Gathering", "content": "..."},
        ...
    ],
    "final_prediction": {
        "team_a": "{team_a}",
        "team_b": "{team_b}",
        "prob_a": 58,
        "prob_b": 42,
        "venue_verdict": "...",
        "matchups": [
            {"batter": "Virat Kohli", "bowler": "Jasprit Bumrah", "edge": "Team A"},
            ...
        ]
    }
}
```

---

## 🛠️ Architecture

| Component | Technology | Description |
| :--- | :--- | :--- |
| **Frontend** | HTML5 / Vanilla CSS | Premium Glassmorphic Dashboard with dynamic animations. |
| **Backend** | FastAPI | Orchestrates Gemini LLM and MCP tool calling. |
| **AI Engine** | Gemini 1.5 Flash | Handles reasoning logic and multi-step tool interactions. |
| **Tools** | FastMCP | Python-based tools for web scraping and data processing. |

---

## 🛠️ MCP Tools Overview

| Tool | Step | Description |
| :--- | :--- | :--- |
| `get_league_standings` | 1 | Fetches live table rankings and NRR from ESPN Cricinfo. |
| `get_injury_report` | 1 | Scrapes official IPL announcements for player availability. |
| `analyze_venue_environment` | 2 | Real-time weather and official venue characteristics. |
| `get_h2h_matchups` | 3 | Retrieves historical trends and key player matchups. |
| `calculate_projections` | 4 | Computes expected scores based on humidity and form. |

---

## 🎨 Premium Dashboard Features

- **"Powered by Gemini" Engine**: Live AI reasoning feed showing tool interaction.
- **Glassmorphism UI**: Sleek, futuristic interface with background glows.
- **Dynamic Reasoning Feed**: Watch the protocol unfold step-by-step in real-time.
- **Win Probability Dashboard**: Visual bar charts and confidence meters.
- **Real-time Data**: Live weather and standings integration.

---

## 📅 Context & Data
*   **Current Season**: IPL 2026
*   **Version**: v2.1
*   **Engine**: Gemini 1.5 Flash + FastMCP
