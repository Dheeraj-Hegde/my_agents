document.addEventListener('DOMContentLoaded', () => {
    const runBtn = document.getElementById('run-prediction');
    const resultsSection = document.getElementById('results-section');
    const loader = document.getElementById('loader');
    const stepsContainer = document.getElementById('reasoning-steps');

    const teams = {
        'RCB': { name: 'Royal Challengers Bengaluru', color: '#ff4b2b' },
        'MI': { name: 'Mumbai Indians', color: '#00d2ff' },
        'CSK': { name: 'Chennai Super Kings', color: '#ffcc00' },
        'KKR': { name: 'Kolkata Knight Riders', color: '#a020f0' },
        'RR': { name: 'Rajasthan Royals', color: '#ff69b4' },
        'SRH': { name: 'Sunrisers Hyderabad', color: '#ffa500' },
        'LSG': { name: 'Lucknow Super Giants', color: '#0057b8' },
        'DC': { name: 'Delhi Capitals', color: '#00008b' },
        'PBKS': { name: 'Punjab Kings', color: '#ed1c24' },
        'GT': { name: 'Gujarat Titans', color: '#1b2133' }
    };

    // Fetch venues on load
    async function initVenues() {
        try {
            const response = await fetch('http://localhost:8001/venues');
            if (response.ok) {
                const venues = await response.json();
                const venueSelect = document.getElementById('venue');
                venueSelect.innerHTML = venues.map(v => `<option value="${v}">${v}</option>`).join('');
            }
        } catch (error) {
            console.error('Failed to load venues:', error);
        }
    }
    initVenues();

    async function updateMatchVenue() {
        const teamA = document.getElementById('team-a').value;
        const teamB = document.getElementById('team-b').value;
        try {
            const response = await fetch(`http://localhost:8001/match-venue?team_a=${teamA}&team_b=${teamB}`);
            if (response.ok) {
                const venue = await response.text();
                const venueSelect = document.getElementById('venue');
                
                // Check if venue exists in list, if not add it
                let exists = Array.from(venueSelect.options).some(opt => opt.value === venue);
                if (!exists) {
                    const newOpt = document.createElement('option');
                    newOpt.value = venue;
                    newOpt.text = venue;
                    venueSelect.add(newOpt);
                }
                venueSelect.value = venue;
            }
        } catch (error) {
            console.error('Failed to update match venue:', error);
        }
    }

    document.getElementById('team-a').addEventListener('change', updateMatchVenue);
    document.getElementById('team-b').addEventListener('change', updateMatchVenue);
    
    // Trigger once on load
    setTimeout(updateMatchVenue, 1000);

    runBtn.addEventListener('click', async () => {
        const teamA = document.getElementById('team-a').value;
        const teamB = document.getElementById('team-b').value;
        const venue = document.getElementById('venue').value;

        // Reset and show loader
        stepsContainer.innerHTML = '';
        resultsSection.classList.add('hidden');
        loader.classList.remove('hidden');

        try {
            const response = await fetch('http://localhost:8001/predict', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ team_a: teamA, team_b: teamB, venue: venue })
            });

            if (!response.ok) throw new Error('Backend failed');
            
            const data = await response.json();

            // Display reasoning steps with slight delay for effect
            for (const step of data.reasoning_steps) {
                await new Promise(resolve => setTimeout(resolve, 600));
                addStep(step);
            }

            loader.classList.add('hidden');
            resultsSection.classList.remove('hidden');
            updateDashboard(data.final_prediction);

        } catch (error) {
            console.error(error);
            loader.classList.add('hidden');
            alert('Error generating prediction. Make sure backend.py is running and GOOGLE_API_KEY is set.');
        }
    });

    function addStep(step) {
        const div = document.createElement('div');
        div.className = 'step-card';
        div.innerHTML = `
            <div class="step-header">
                <span class="step-tag">${step.tag}</span>
                <span class="step-title">${step.title}</span>
            </div>
            <div class="step-content">${step.content}</div>
        `;
        stepsContainer.appendChild(div);
        div.scrollIntoView({ behavior: 'smooth' });
    }

    function updateDashboard(prediction) {
        document.getElementById('prob-team-a').innerText = prediction.team_a;
        document.getElementById('prob-team-b').innerText = prediction.team_b;
        document.getElementById('perc-a').innerText = prediction.prob_a + '%';
        document.getElementById('perc-b').innerText = prediction.prob_b + '%';
        
        const barA = document.getElementById('prob-bar-a');
        const barB = document.getElementById('prob-bar-b');
        
        barA.style.width = prediction.prob_a + '%';
        barB.style.width = prediction.prob_b + '%';
        
        // Dynamic Colors from branding
        const getColor = (teamName) => {
            if (teams[teamName]) return teams[teamName].color;
            // Fallback: check if it's a full name in our mapping
            const found = Object.values(teams).find(t => t.name.toLowerCase() === teamName.toLowerCase());
            return found ? found.color : '#00d2ff';
        };

        const colorA = getColor(prediction.team_a);
        const colorB = getColor(prediction.team_b);
        
        barA.style.background = colorA;
        barA.style.boxShadow = `0 0 15px ${colorA}55`;
        barB.style.background = colorB;
        barB.style.boxShadow = `0 0 15px ${colorB}55`;
        
        document.getElementById('ai-verdict-text').innerText = prediction.venue_verdict || "Match is evenly balanced.";
        document.getElementById('venue-verdict-text').innerText = prediction.venue_verdict;

        const tbody = document.querySelector('#matchup-table tbody');
        tbody.innerHTML = prediction.matchups.map(m => `
            <tr>
                <td><strong>${m.batter}</strong></td>
                <td><strong>${m.bowler}</strong></td>
                <td style="color: ${m.edge === 'Even' ? 'inherit' : (m.edge === prediction.team_a ? 'var(--accent-blue)' : 'var(--accent-red)')}">${m.edge}</td>
            </tr>
        `).join('');

        // Update confidence
        const confidenceMeter = document.getElementById('confidence-meter');
        const probDiff = Math.abs(prediction.prob_a - prediction.prob_b);
        if (probDiff > 20) {
            confidenceMeter.className = 'confidence high';
            confidenceMeter.querySelector('span').innerText = 'High';
        } else if (probDiff > 10) {
            confidenceMeter.className = 'confidence medium';
            confidenceMeter.querySelector('span').innerText = 'Medium';
        } else {
            confidenceMeter.className = 'confidence low';
            confidenceMeter.querySelector('span').innerText = 'Low';
        }
    }
});
