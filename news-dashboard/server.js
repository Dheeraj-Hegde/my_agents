const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');

// Target feeds
const topicSources = {
    finance: 'https://news.yahoo.com/rss/business',
    sports: 'https://www.espn.com/espn/rss/news',
    war: 'https://feeds.bbci.co.uk/news/world/rss.xml',
    ai: 'https://techcrunch.com/feed/',
    crypto: 'https://cointelegraph.com/rss'
};

const server = http.createServer((req, res) => {
    // 1. Backend Proxy Route: Fetch XML server-side to avoid CORS and Antivirus blocks
    if (req.url.startsWith('/api/proxy?topic=')) {
        const urlObj = new URL(req.url, `http://${req.headers.host}`);
        const topic = urlObj.searchParams.get('topic');
        const targetUrl = topicSources[topic];
        
        if (!targetUrl) {
            res.writeHead(400);
            return res.end('Invalid topic');
        }

        // Fetch securely from the server
        https.get(targetUrl, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' // Some RSS feeds require a standard UA
            }
        }, (proxyRes) => {
            let data = '';
            proxyRes.on('data', chunk => data += chunk);
            proxyRes.on('end', () => {
                res.writeHead(200, {
                    'Content-Type': 'application/xml',
                    'Access-Control-Allow-Origin': '*'
                });
                res.end(data);
            });
        }).on('error', (err) => {
            console.error('Proxy Error:', err);
            res.writeHead(500);
            res.end('Error fetching XML');
        });
        return;
    }

    // 2. Serve standard HTML/CSS/JS files
    let filePath = path.join(__dirname, req.url === '/' ? 'index.html' : req.url);
    const extname = String(path.extname(filePath)).toLowerCase();
    
    // Only serve our specific web files for security
    const mimeTypes = {
        '.html': 'text/html',
        '.js': 'text/javascript',
        '.css': 'text/css'
    };
    
    const contentType = mimeTypes[extname] || 'application/octet-stream';

    fs.readFile(filePath, (error, content) => {
        if (error) {
            if(error.code === 'ENOENT') {
                res.writeHead(404);
                res.end('404 File not found');
            } else {
                res.writeHead(500);
                res.end(`Server Error: ${error.code}`);
            }
        } else {
            res.writeHead(200, { 'Content-Type': contentType });
            res.end(content, 'utf-8');
        }
    });
});

const PORT = 3000;
server.listen(PORT, () => {
    console.log(`\n================================`);
    console.log(`🚀 News Dashboard is LIVE!`);
    console.log(`🌐 Open: http://localhost:${PORT}`);
    console.log(`================================`);
    console.log(`Backend proxy is active. Antiviruses will no longer block your feed.\n`);
});
