# Pulse News Dashboard 🌐

<img src="./news_dashboard_demo.webp" alt="Dashboard Demonstration Video" width="100%">

### 🎬 Detailed Walkthrough Demo
<img src="./pulse_news_demo.webp" alt="Pulse News Dashboard — Detailed Captioned Walkthrough" width="100%">

A premium, full-width web dashboard that displays live hourly top 10 news updates across 5 curated categories simultaneously. Designed with a gorgeous, fluid grid layout and responsive background glow effects.

### 🌟 Key Features
- **Real-Time Data**: Automatically sources, parses, and styles RSS updates perfectly.
- **Local Proxy Backend**: Employs a custom `Node.js` proxy relay to completely spoof and bypass CORS policy blocks and Antivirus interferences natively, requiring no third-party keys.
- **Top 10 Filtering**: Evaluates raw XML streams asynchronously and enforces strict temporal sorting (Latest First) capped at EXACTLY 10 breaking items per column.
- **Live Clock Synchronization**: Runs a live, synchronous ticking time interface.

### 📰 Curated Sources
1. **Finance**: Yahoo Business
2. **Sports**: ESPN News
3. **Global Conflict**: BBC World News
4. **Artificial Intelligence**: TechCrunch Feed
5. **Stocks & Crypto**: CoinTelegraph

### 🛠️ Prerequisites
This project uses a backend proxy to securely fetch feeds without CORS errors. You must have **Node.js** installed on your computer to run the local proxy.

- **Windows/Mac**: Download and install the latest LTS version directly from the [official Node.js website](https://nodejs.org/).
- **Linux**: Install via your package manager (e.g., `sudo apt install nodejs`).

### 🚀 How to Run

1. Open a terminal and navigate to the project directory:
   ```bash
   cd news-dashboard
   ```
2. Start the local proxy node server:
   ```bash
   node server.js
   ```
3. Open your browser and navigate to the dashboard interface:
   ```text
   http://localhost:3000/
   ```

### ⚙️ Architecture details
- `server.js`: Handles backend RSS routing to overcome browser-level security barriers dynamically.
- `app.js`: Translates XML to JSON, normalizes domains, assigns semantic UI timestamps ("10 mins ago"), and manages DOM generation.
- `index.html / style.css`: Constructs the 5-column dashboard using dynamic grids and `auto-fit` styling rules to handle varying viewport dimensions perfectly.
