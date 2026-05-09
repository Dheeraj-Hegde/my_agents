const chatHistory = document.getElementById('chatHistory');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');

// Function to add a message to the chat
function addMessage(text, isUser = false) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${isUser ? 'user' : 'assistant'}`;
    const icon = isUser ? 'user' : 'sparkles';

    messageDiv.innerHTML = `
        <div class="avatar"><i data-lucide="${icon}"></i></div>
        <div class="text">${text}</div>
    `;

    chatHistory.appendChild(messageDiv);
    lucide.createIcons();
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

// Function to handle sending message
async function handleSend() {
    const message = userInput.value.trim();
    if (!message) return;

    userInput.value = '';
    addMessage(message, true);

    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'message assistant';
    loadingDiv.innerHTML = `<div class="avatar"><i data-lucide="sparkles"></i></div><div class="text">Processing...</div>`;
    chatHistory.appendChild(loadingDiv);
    lucide.createIcons();
    chatHistory.scrollTop = chatHistory.scrollHeight;

    try {
        const response = await fetch('http://localhost:8000/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message }),
        });
        const data = await response.json();
        chatHistory.removeChild(loadingDiv);
        addMessage(data.response || 'Error.');
    } catch (error) {
        chatHistory.removeChild(loadingDiv);
        addMessage('Server error.');
    }
}

sendBtn.addEventListener('click', handleSend);
userInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') handleSend(); });

// Fetch trending rates
async function fetchTrending() {
    try {
        const res = await fetch(`https://open.er-api.com/v6/latest/USD`);
        const data = await res.json();
        document.getElementById('usd-eur').innerText = data.rates.EUR.toFixed(4);
        document.getElementById('usd-gbp').innerText = data.rates.GBP.toFixed(4);
        document.getElementById('usd-inr').innerText = data.rates.INR.toFixed(4);
    } catch (e) { }
}

fetchTrending();
setInterval(fetchTrending, 60000);
