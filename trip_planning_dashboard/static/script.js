document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('trip-form');
    const submitBtn = document.getElementById('generate-btn');
    const resultsSection = document.getElementById('results-section');
    const itineraryOutput = document.getElementById('itinerary-output');
    const budgetOutput = document.getElementById('budget-output');
    const guideOutput = document.getElementById('guide-output');
    const errorBanner = document.getElementById('error-message');



    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const destination = document.getElementById('destination').value;
        const duration = document.getElementById('duration').value;
        const travel_type = document.getElementById('travel_type').value;
        const season = document.getElementById('season').value;
        const interests = document.getElementById('interests').value;

        // Reset UI state
        errorBanner.classList.add('hidden');
        resultsSection.classList.add('hidden');
        submitBtn.classList.add('loading');
        submitBtn.disabled = true;

        try {
            const response = await fetch('/api/plan_trip', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ destination, duration, travel_type, season, interests })
            });

            const data = await response.json();

            if (data.success) {
                // Render all sections
                itineraryOutput.innerHTML = marked.parse(data.itinerary);
                budgetOutput.innerHTML = marked.parse(data.budget);
                guideOutput.innerHTML = marked.parse(data.guide);
                resultsSection.classList.remove('hidden');
                


                // Smooth scroll to results
                resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } else {
                showError(data.error || 'Failed to generate itinerary. Please try again.');
            }
        } catch (error) {
            showError('Network error occurred. Please check your connection.');
        } finally {
            submitBtn.classList.remove('loading');
            submitBtn.disabled = false;
        }
    });

    function showError(message) {
        errorBanner.textContent = message;
        errorBanner.classList.remove('hidden');
    }
});
