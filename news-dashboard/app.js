// Live API configuration mapping to public RSS feeds for live updates
const topicSources = {
    finance: 'https://news.yahoo.com/rss/business',
    sports: 'https://www.espn.com/espn/rss/news',
    war: 'https://feeds.bbci.co.uk/news/world/rss.xml',
    ai: 'https://techcrunch.com/feed/',
    crypto: 'https://cointelegraph.com/rss'
};

function timeAgo(date) {
    const seconds = Math.floor((new Date() - date) / 1000);
    let interval = seconds / 31536000;
    if (interval >= 1) return Math.floor(interval) + " years ago";
    interval = seconds / 2592000;
    if (interval >= 1) return Math.floor(interval) + " months ago";
    interval = seconds / 86400;
    if (interval >= 1) return Math.floor(interval) + " days ago";
    interval = seconds / 3600;
    if (interval >= 1) return Math.floor(interval) + " hours ago";
    interval = seconds / 60;
    if (interval >= 1) return Math.floor(interval) + " mins ago";
    return "Just now";
}

// Fetch live news dynamically via local backend proxy
async function fetchLiveNews(topic) {
    try {
        // Fetch through our own local Node server proxy to completely immune ourselves from Antivirus/CORS blocks
        const response = await fetch(`/api/proxy?topic=${topic}`);
        if (!response.ok) throw new Error('Network response was not ok');
        
        // Proxy returns the raw XML string directly, no JSON wrapping
        const rawXml = await response.text(); 
        
        // Parse the raw XML string from the RSS feed
        const parser = new DOMParser();
        const xmlDoc = parser.parseFromString(rawXml, "text/xml");
        const items = Array.from(xmlDoc.querySelectorAll("item"));
        
        let articles = items.map(item => {
            const title = item.querySelector("title")?.textContent || "No Title";
            const link = item.querySelector("link")?.textContent || "#";
            const pubDateStr = item.querySelector("pubDate")?.textContent;
            const date = pubDateStr ? new Date(pubDateStr) : new Date();
            
            // Extract domain from link for a clean professional source name
            let source = "NEWS FEED";
            try {
                const urlObj = new URL(link);
                source = urlObj.hostname.replace('www.', '').toUpperCase();
            } catch(e) {}
            
            return {
                title: title,
                source: source,
                time: timeAgo(date),
                url: link,
                timestamp: date.getTime()
            };
        })
        .sort((a, b) => b.timestamp - a.timestamp) // Enforce order: latest items first
        .slice(0, 10); // Take strictly top 10
            
        return articles;
    } catch (error) {
        console.error(`Error fetching ${topic}:`, error);
        return [{ title: "Unable to connect to live feed. Please refresh.", source: "SYSTEM", time: "Just now", url: "#" }];
    }
}

// Clock functionality
function updateClock() {
    const now = new Date();
    
    // Time
    const timeOptions = { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false };
    document.getElementById('clock').textContent = now.toLocaleTimeString('en-US', timeOptions);
    
    // Date
    const dateOptions = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
    document.getElementById('date').textContent = now.toLocaleDateString('en-US', dateOptions);
}

// Populate UI with news
async function renderNews() {
    const topics = Object.keys(topicSources);
    
    for (const topic of topics) {
        const container = document.getElementById(`list-${topic}`);
        if (!container) continue;
        
        container.innerHTML = '<div class="article-source" style="padding: 1rem;">Loading live feed...</div>';
        
        const articles = await fetchLiveNews(topic);
        container.innerHTML = ''; // Clear existing
        
        articles.forEach((article, index) => {
            // Animate entry with delay
            setTimeout(() => {
                // Wrapper to make it clickable
                const linkWrapper = document.createElement('a');
                linkWrapper.href = article.url;
                linkWrapper.target = "_blank";
                linkWrapper.style.textDecoration = 'none';
                linkWrapper.style.color = 'inherit';
                linkWrapper.style.display = 'block';

                const card = document.createElement('div');
                card.className = 'article-card';
                card.style.opacity = '0';
                card.style.transform = 'translateY(20px)';
                card.style.transition = 'all 0.5s ease';
                
                card.innerHTML = `
                    <div class="article-rank">${index + 1}</div>
                    <div class="article-time">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <circle cx="12" cy="12" r="10"></circle>
                            <polyline points="12 6 12 12 16 14"></polyline>
                        </svg>
                        ${article.time}
                    </div>
                    <h3 class="article-title">${article.title}</h3>
                    <div class="article-source">${article.source}</div>
                `;
                
                linkWrapper.appendChild(card);
                container.appendChild(linkWrapper);
                
                // Trigger reflow for animation
                void card.offsetWidth;
                card.style.opacity = '1';
                card.style.transform = 'translateY(0)';
            }, index * 100); // Staggered animation
        });
    }
}

// Initialization
document.addEventListener('DOMContentLoaded', () => {
    // Start clock
    updateClock();
    setInterval(updateClock, 1000);
    
    // Initial fetch/render
    renderNews();
    
    // Auto refresh every hour
    setInterval(() => {
        renderNews();
    }, 60 * 60 * 1000); // 1 hour
});
