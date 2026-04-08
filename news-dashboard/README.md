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

---

## 📂 Detailed File Breakdown

Below is a comprehensive explanation of every file in this directory, covering its purpose, internal structure, and how it connects to the rest of the project.

---

### `server.js` — Backend Proxy & Static File Server

**Purpose:** The Node.js HTTP server that powers the entire application. It serves two critical roles: (1) acting as a **CORS-bypassing proxy** that fetches remote RSS feeds server-side on behalf of the browser, and (2) functioning as a **static file server** that delivers the HTML, CSS, and JS assets to the client.

**Key Internals:**

| Section | Lines | Description |
|---|---|---|
| Module imports | 1–4 | Loads Node.js built-in modules: `http` (server), `https` (outbound RSS requests), `fs` (file reading), and `path` (path resolution). |
| `topicSources` map | 7–13 | A dictionary mapping each of the 5 topic slugs (`finance`, `sports`, `war`, `ai`, `crypto`) to their corresponding RSS feed URL. This single source-of-truth is shared conceptually with `app.js`. |
| Proxy route handler | 17–48 | Listens for requests matching `/api/proxy?topic=<slug>`. Extracts the `topic` query parameter, resolves it against `topicSources`, and performs an outbound `https.get()` to the RSS provider. Streams the raw XML response back to the browser with an `Access-Control-Allow-Origin: *` header, completely eliminating CORS restrictions. A `User-Agent` header is spoofed to satisfy feeds that block non-browser requests. Returns `400` for invalid topics and `500` on network errors. |
| Static file server | 50–76 | For any request that does **not** match the `/api/proxy` route, the server resolves the URL to a local file path (defaulting `/` to `index.html`). It reads the file from disk and serves it with the correct MIME type (`text/html`, `text/javascript`, or `text/css`). Returns `404` for missing files and `500` for read errors. |
| Server bootstrap | 79–86 | Binds the server to **port 3000** and prints a startup banner confirming the dashboard is live. |

**Why it exists:** Browsers enforce Same-Origin Policy (CORS) and some antivirus software intercepts cross-origin XML requests. By routing all RSS fetches through a local Node server on `localhost:3000`, these restrictions are completely circumvented without needing any third-party API keys or proxy services.

---

### `app.js` — Client-Side Application Logic

**Purpose:** The core front-end JavaScript that drives all dynamic behavior: fetching live news data from the backend proxy, parsing RSS XML into structured objects, rendering interactive article cards into the DOM, and maintaining a real-time clock display.

**Key Functions:**

| Function | Lines | Description |
|---|---|---|
| `timeAgo(date)` | 10–23 | Converts a `Date` object into a human-readable relative timestamp (e.g., `"3 hours ago"`, `"Just now"`). Uses progressively smaller interval thresholds: years → months → days → hours → minutes → "Just now". |
| `fetchLiveNews(topic)` | 26–69 | The async data-fetching pipeline. Sends a `GET` request to `/api/proxy?topic=<slug>`, receives raw XML text, parses it using the browser's native `DOMParser`, extracts all `<item>` nodes, and maps each to a normalized article object containing `title`, `source` (extracted domain name), `time` (via `timeAgo`), `url`, and `timestamp`. Articles are **sorted newest-first** and **capped at exactly 10**. On failure, returns a single error-state card. |
| `updateClock()` | 72–82 | Reads the current time and date, formats them via `toLocaleTimeString` (24-hour format) and `toLocaleDateString` (full weekday, month, day, year), and injects them into the `#clock` and `#date` DOM elements. Called once on load and then every **1 second** via `setInterval`. |
| `renderNews()` | 85–137 | Iterates over all 5 topic keys, calls `fetchLiveNews()` for each, and builds the DOM for each article card. Each card includes a rank number, a clock-icon timestamp, the article title (clamped to 3 lines), and the source domain name. Cards are wrapped in `<a>` tags that open in a new tab. A **staggered animation** (`100ms` delay per card) creates a cascading entrance effect with opacity and translateY transitions. |
| `DOMContentLoaded` listener | 140–152 | Bootstraps the application: starts the clock, performs the initial news render, and sets up an **hourly auto-refresh** (`setInterval` at `3,600,000ms`). |

**Data Flow:** `DOMContentLoaded` → `renderNews()` → `fetchLiveNews(topic)` → `fetch(/api/proxy?topic=...)` → `server.js` proxy → RSS XML → DOMParser → sorted/sliced array → DOM cards with animations.

---

### `index.html` — Main Dashboard Page

**Purpose:** The primary HTML document that defines the structural layout of the Pulse News Dashboard. It is the page served when you visit `http://localhost:3000/`.

**Key Sections:**

| Section | Lines | Description |
|---|---|---|
| `<head>` | 3–9 | Sets the page charset (UTF-8), viewport meta tag for responsiveness, page title (`"Pulse News | Real-time Insights"`), links to `style.css`, and imports **Google Fonts** (Outfit for headings, Inter for body text). |
| Background effects | 11–17 | Contains 5 `<div class="glow">` elements — one per news category — that produce large, blurred, color-coded orbs. These float behind all content via CSS `position: fixed` and `z-index: -1`, creating a dynamic glassmorphism backdrop. |
| Header | 19–28 | A sticky top bar with two components: the **PULSE.** logo (with an accent-colored dot) and subtitle, and the **live clock** display with placeholders (`--:--:--` and `"Loading date..."`) that are populated by `app.js`. |
| Dashboard grid | 30–78 | The `<main class="dashboard">` container holds 5 `<section>` elements, one per topic. Each section has: a **section header** (`<h2>` + pulsing live indicator dot) and an empty **news list** container (`<div id="list-{topic}">`) that `app.js` populates at runtime. Section IDs follow the pattern `section-{topic}` and list IDs follow `list-{topic}`. |
| Script | 80 | Loads `app.js` at the bottom of `<body>` to ensure all DOM elements are available before JavaScript executes. |

**Design Pattern:** The HTML is intentionally minimal — it defines only the skeleton. All article content is dynamically injected by `app.js`, keeping the markup clean and the rendering logic centralized in JavaScript.

---

### `style.css` — Visual Design & Layout System

**Purpose:** The complete styling layer for the dashboard. Implements a dark-themed, glassmorphic design system with per-category color palettes, responsive grid layout, hover animations, and custom scrollbar styling.

**Key Sections:**

| Section | Lines | Description |
|---|---|---|
| CSS Custom Properties (`:root`) | 1–34 | Defines the entire design token system. Includes: base colors (`--bg-color: #050505`, `--text-primary`, `--text-secondary`), 5 per-category accent color palettes (each with accent, background, and card variants at different opacities), and font family variables (`Outfit` for headings, `Inter` for body). |
| Global reset | 36–40 | Zeroes out all default margin, padding, and sets `box-sizing: border-box` globally for predictable element sizing. |
| Body | 42–48 | Applies the dark background, primary text color, body font, full viewport height, and `overflow-x: hidden` to prevent horizontal scroll from glow effects. |
| Background glow effects | 50–79 | Positions 5 large (600×600 px) circular, heavily blurred (`filter: blur(140px)`) colored orbs at different corners and center of the viewport. Each animates with a `float` keyframe that translates vertically and scales, with staggered `animation-delay` values creating organic, out-of-phase motion. |
| Sticky header | 81–128 | Styles the top bar with a semi-transparent black background (`rgba(5,5,5,0.7)`), `backdrop-filter: blur(20px)` for glass effect, flexbox layout for logo/clock alignment, and `position: sticky` to keep it visible during scroll. The logo uses `Outfit` at `2.5rem` with `-1px` letter spacing. The clock uses a lightweight `300` font weight for elegance. |
| Dashboard grid | 130–135 | Uses `display: grid` with `grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))` for a fluid, responsive column layout that automatically adapts from 5 columns on wide screens down to fewer on narrower viewports. |
| Topic sections | 137–171 | Each section card gets a frosted-glass look via `backdrop-filter: blur(10px)`, rounded corners (`border-radius: 16px`), and per-category tinted backgrounds/borders using the CSS variables. Section headers have colored bottom borders. |
| Live indicator | 173–191 | The pulsing dot next to each section title. Uses `@keyframes pulse` to animate scale and opacity, with per-category colored `box-shadow` for a glow effect. |
| Article cards | 199–283 | Cards feature category-tinted backgrounds, a hidden 4px colored left accent bar (revealed on hover via `::before` pseudo-element), a large watermark rank number (opacity `0.03`, barely visible), hover effects (`translateY(-4px)`, shadow, background change), title clamping to 3 lines via `-webkit-line-clamp`, and uppercase source labels. |
| Custom scrollbar | 286–298 | Slim `6px` track with translucent thumb for a polished look on WebKit browsers. |
| Responsive breakpoints | 300–320 | **≤1400px**: Collapses to 2-column grid. **≤768px**: Single column, centered header, reduced padding. |

---

### `demo.html` — Automated Captioned Walkthrough Page

**Purpose:** A self-running demonstration version of the dashboard that overlays timed, animated captions explaining each feature. Used for recording video walkthroughs and demos. Shares the same `style.css` and `app.js` as the main dashboard, so it displays real live news data while the captions narrate the functionality.

**Key Internals:**

| Section | Lines | Description |
|---|---|---|
| Inline `<style>` | 9–49 | Defines the **caption overlay** (`#demo-caption`): a fixed-position pill at the bottom of the screen with a dark glass background (`rgba(0,0,0,0.9)` + `backdrop-filter: blur(16px)`), smooth opacity/transform transitions, and large `22px` Outfit font. Also defines a `.demo-highlight` keyframe that creates a white glowing box-shadow pulse around sections when they are spotlighted. |
| Dashboard HTML | 51–111 | Identical structure to `index.html` — same background glows, header, and 5 topic sections. Ensures the demo looks exactly like the real dashboard. |
| Caption element | 114 | A hidden `<div id="demo-caption">` that serves as the target for caption text injection. |
| `showCaption(text, icon)` | 121–127 | Fades the caption out, waits 500ms, injects new HTML (with optional emoji icon), then fades it back in by toggling the `.visible` class. |
| `hideCaption()` | 129–131 | Removes the `.visible` class to fade the caption out. |
| `highlightSection(id)` | 133–140 | Scrolls a section into view (`behavior: 'smooth'`), applies the `.demo-highlight` glow animation class, and removes it after 2 seconds. |
| `demoSteps` timeline | 143–161 | An array of 16 timed steps that execute over ~84 seconds. Each step triggers a caption and optionally highlights a section or scrolls the page. The sequence progresses: introduction → UI overview → header clock → each of the 5 categories (with scroll-to-highlight) → hover animations → live indicators → proxy architecture → glassmorphism → responsiveness → auto-refresh → closing tagline → caption fade-out. |
| Timeline execution | 164–168 | After a 1-second initial delay, all steps are scheduled via nested `setTimeout` calls using each step's `time` property as the offset, creating the complete scripted demo experience. |

**Usage:** Start the server with `node server.js`, then navigate to `http://localhost:3000/demo.html`. The walkthrough auto-plays with captions — ideal for screen recording.

---

### `news_dashboard_demo.webp` — Quick Demo Recording

**Purpose:** An animated WebP video file (≈8.0 MB) that provides a short visual demonstration of the dashboard in action. Embedded at the top of this README to give viewers an immediate preview of the project without needing to run it locally.

**Format:** WebP (animated), displayed inline as an `<img>` tag in the README.

---

### `pulse_news_demo.webp` — Detailed Captioned Walkthrough Recording

**Purpose:** A longer, more detailed animated WebP video file (≈14.0 MB) captured from the `demo.html` page. It shows the full captioned walkthrough experience, including the timed captions, section highlights, and smooth scroll animations narrating every feature of the dashboard.

**Format:** WebP (animated), displayed inline as an `<img>` tag in the README below the quick demo.

---

## ⚙️ Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Browser (Client)                      │
│                                                          │
│  index.html ──► style.css    (layout & visual design)    │
│       │                                                  │
│       └──► app.js                                        │
│              │  1. DOMContentLoaded fires                 │
│              │  2. updateClock() starts (1s interval)     │
│              │  3. renderNews() called                    │
│              │     └─► fetchLiveNews(topic) × 5           │
│              │           └─► fetch("/api/proxy?topic=")   │
│              │  4. Auto-refresh every 60 minutes          │
└──────────────┼───────────────────────────────────────────┘
               │  HTTP requests to localhost:3000
               ▼
┌─────────────────────────────────────────────────────────┐
│                  server.js (Node.js)                      │
│                                                          │
│  /api/proxy?topic=<slug>                                 │
│     └─► Resolves topic → RSS URL from topicSources       │
│     └─► https.get() → fetches raw XML from provider      │
│     └─► Returns XML with CORS headers to browser         │
│                                                          │
│  /* (all other routes)                                   │
│     └─► Serves static files (HTML, CSS, JS)              │
└──────────────┼───────────────────────────────────────────┘
               │  HTTPS requests (server-side)
               ▼
┌─────────────────────────────────────────────────────────┐
│              External RSS Providers                       │
│                                                          │
│  • news.yahoo.com/rss/business                           │
│  • www.espn.com/espn/rss/news                            │
│  • feeds.bbci.co.uk/news/world/rss.xml                   │
│  • techcrunch.com/feed/                                  │
│  • cointelegraph.com/rss                                 │
└─────────────────────────────────────────────────────────┘
```
