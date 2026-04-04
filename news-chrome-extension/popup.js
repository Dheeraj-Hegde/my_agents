const topicSources = {
    finance: 'https://news.yahoo.com/rss/business',
    sports: 'https://www.espn.com/espn/rss/news',
    war: 'https://feeds.bbci.co.uk/news/world/rss.xml',
    ai: 'https://techcrunch.com/feed/',
    crypto: 'https://cointelegraph.com/rss'
};

const cache = {}; // Cache to retain state during fast tab switches

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

async function fetchNews(topic) {
    if (cache[topic]) return cache[topic];
    
    try {
        const response = await fetch(topicSources[topic]);
        if (!response.ok) throw new Error('Network error');
        
        const rawXml = await response.text();
        const parser = new DOMParser();
        const xmlDoc = parser.parseFromString(rawXml, "text/xml");
        
        const items = Array.from(xmlDoc.querySelectorAll("item"));
        
        let articles = items.map(item => {
            const title = item.querySelector("title")?.textContent || "No Title";
            const link = item.querySelector("link")?.textContent || "#";
            const pubDateStr = item.querySelector("pubDate")?.textContent;
            const date = pubDateStr ? new Date(pubDateStr) : new Date();
            
            let source = "NEWS FEED";
            try {
                const urlObj = new URL(link);
                source = urlObj.hostname.replace('www.', '').toUpperCase();
            } catch(e) {}
            
            return {
                title, source, time: timeAgo(date), url: link, timestamp: date.getTime()
            };
        })
        .sort((a, b) => b.timestamp - a.timestamp)
        .slice(0, 10);
        
        cache[topic] = articles;
        return articles;
    } catch (error) {
        console.error(error);
        return [{ title: "Unable to load feed from the source.", source: "SYSTEM", time: "Just now", url: "#" }];
    }
}

async function renderTopic(topic) {
    const list = document.getElementById(`list-${topic}`);
    if(!list) return;
    
    if(!cache[topic]) {
        list.innerHTML = `<div style="text-align:center; padding: 2rem; color: #777;">Fetching secure feed...</div>`;
    }
    
    const articles = await fetchNews(topic);
    list.innerHTML = '';
    
    articles.forEach((article, index) => {
        const link = document.createElement('a');
        link.className = 'article-card';
        link.href = article.url;
        link.target = "_blank"; // Opens in new chrome tab
        link.innerHTML = `
            <div class="article-rank">${index + 1}</div>
            <div class="article-time">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"></circle>
                    <polyline points="12 6 12 12 16 14"></polyline>
                </svg>
                ${article.time}
            </div>
            <h3 class="article-title">${article.title}</h3>
            <div class="article-source">${article.source}</div>
        `;
        list.appendChild(link);
    });
}

function updateClock() {
    const timeOptions = { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false };
    document.getElementById('clock').textContent = new Date().toLocaleTimeString('en-US', timeOptions);
}

document.addEventListener('DOMContentLoaded', () => {
    updateClock();
    setInterval(updateClock, 1000);
    
    // Tab Switching Logic
    const tabs = document.querySelectorAll('.tab-btn');
    const sections = document.querySelectorAll('.topic-section');
    
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.getAttribute('data-target');
            
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            
            sections.forEach(s => s.classList.remove('active-section'));
            document.getElementById(`section-${target}`).classList.add('active-section');
            
            renderTopic(target); // Fetch if not cached
        });
    });

    // Initial render for Finance (default active)
    renderTopic('finance');
    
    // Preload others in background for snappy UI
    setTimeout(() => {
        ['sports', 'war', 'ai', 'crypto'].forEach(t => fetchNews(t));
    }, 1000);
});
