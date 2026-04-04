# Pulse News | Chrome Extension 🚀

A lightweight, premium Chrome plugin adapted from the Pulse Dashboard system. Delivers instant, categorized Top-10 hourly news directly to your browser's extension tray. 

### 🌟 Native Advantages
- **Serverless Fetching**: Entirely bypasses the need for an active `Node.js` proxy. Operates completely securely within the browser by leveraging Chrome's internal Manifest V3 `host_permissions` payload to overcome CORS dynamically.
- **Speed-Optimized Architecture**: Caches raw API data asynchronously in memory during tab-switching to eliminate redundant parsing load times.
- **Compact UI Overhaul**: Retains the gorgeous aesthetic and dynamic glowing effects of the core dashboard but intelligently repositions layout density into an interactive 600x550 tabbed widget format.

### 📰 Included Columns
1. **Finance** (Yahoo Finance)
2. **Sports** (ESPN)
3. **Global Conflict** (BBC World News)
4. **Artificial Intelligence** (TechCrunch)
5. **Crypto** (CoinTelegraph)

### 🛠️ Installation & Setup

1. Open Google Chrome.
2. Navigate to your extensions management page using the address:
   ```text
   chrome://extensions/
   ```
3. In the top right corner, toggle the **Developer Mode** slider to `ON`.
4. Click the newly exposed **Load Unpacked** button in the top left context menu.
5. Select this exact project directory (`news-chrome-extension`).
6. **(Optional but recommended)**: Find the puzzle-piece icon at the top right of your Chrome window, find "Pulse News", and click the pushpin 📌 to lock it permanently to your quick-access bar!

### ⚙️ How it Works under the Hood
- `manifest.json`: The core skeleton explicitly authorizing cross-origin access payload bounds.
- `popup.js`: Resolves live feeds asynchronously exactly as the user switches active tabs, converting pure XML metadata into readable UI cards on the fly.
