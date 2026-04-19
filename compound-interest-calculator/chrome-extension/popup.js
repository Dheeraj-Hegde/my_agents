document.getElementById('calcBtn').addEventListener('click', async () => {
    const amount = document.getElementById('amount').value;
    const rate = document.getElementById('rate').value / 100; // convert to decimal
    const frequency = 12; // Default to monthly compounding
    const years = document.getElementById('years').value;
    const addition = document.getElementById('addition').value;
    const additionFreq = document.getElementById('addition-freq').value;

    const resultContainer = document.getElementById('result-container');
    const resultDiv = document.getElementById('result');
    const btn = document.getElementById('calcBtn');

    // Construct the prompt for the LLM
    const prompt = `Please calculate the compound interest for a principal of $${amount}, an annual interest rate of ${rate * 100}%, compounded ${frequency} times per year, for ${years} years. Also process an additional periodic contribution of $${addition} made ${additionFreq}. Use your tool to get the exact math. Summarize the result concisely, explicitly stating the overall Total Deposits (principal + all additions). You MUST present the year-by-year split in a semantic HTML <table> element. Ensure the table columns include: Year, Deposits Made This Year, Total Deposits (Cumulative Invested), Interest this Year, and Balance. DO NOT use code blocks for the HTML table, just output the raw HTML safely.`;

    // Make UI loading state
    resultContainer.classList.add('hidden');
    btn.classList.add('loading');
    btn.disabled = true;

    try {
        const response = await fetch('http://localhost:5000/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: prompt })
        });

        const data = await response.json();

        if (data.error) {
            resultDiv.innerHTML = `<strong style="color: #ef4444">Error</strong>${data.error}`;
        } else {
            let formattedText = data.answer.replace(/\*\*(.*?)\*\*/g, '<span style="color:var(--text-main); font-weight:600">$1</span>');
            // Remove codeblocks or other markdown formatting just in case
            formattedText = formattedText.replace(/`/g, '');

            // Check if the LLM called our function by looking at the logs
            const usedTool = data.logs && data.logs.some(log => log.includes("Called function: calculate_compound_interest"));
            let toolBadge = usedTool
                ? `<div style="font-size: 11px; color: var(--accent); margin-top: 12px; padding-top: 8px; border-top: 1px solid var(--surface-border); display: flex; align-items: center; gap: 4px;">
                     <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
                     Custom Python Function Executed
                   </div>`
                : '';

            resultDiv.innerHTML = `<strong>AI Analysis:</strong><div style="margin-top: 8px;">${formattedText}</div>${toolBadge}`;
        }

        // Slight delay to ensure content is rendered before animation
        setTimeout(() => {
            resultContainer.classList.remove('hidden');
        }, 10);

    } catch (err) {
        resultDiv.innerHTML = `<strong style="color: #ef4444">Connection Failed</strong>Is the Python server running on localhost:5000?`;
        setTimeout(() => {
            resultContainer.classList.remove('hidden');
        }, 10);
    } finally {
        btn.classList.remove('loading');
        btn.disabled = false;
    }
});