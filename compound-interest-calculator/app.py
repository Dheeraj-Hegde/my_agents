import os
import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='chrome-extension', static_url_path='')
CORS(app) # Allows the Chrome extension to talk to this server

# 2. The Compound Interest Function
def calculate_compound_interest(
    principal: float, 
    annual_rate: float, 
    compounding_frequency: int, 
    years: int, 
    addition: float = 0.0, 
    addition_frequency: str = 'none'
):
    """
    Calculates compound interest with optional periodic contributions.
    Returns detailed year-by-year breakdown.
    Args:
        principal: Initial amount of money.
        annual_rate: Annual interest rate (as a decimal, e.g., 0.05 for 5%).
        compounding_frequency: Times interest is compounded per year (e.g., 12).
        years: Number of years the money is invested.
        addition: Amount added periodically (set to 0 if none).
        addition_frequency: 'monthly', 'quarterly', 'yearly', or 'none'.
    """
    freq_map = {'monthly': 12, 'quarterly': 4, 'yearly': 1, 'none': 0}
    additions_per_yr = freq_map.get(addition_frequency.lower(), 0)
    
    balance = float(principal)
    cumulative_invested = float(principal)
    
    yearly_breakdown = []
    
    for year in range(1, int(years) + 1):
        year_invested = 0.0
        year_start_balance = balance
        
        for month in range(1, 13):
            # Compound interest for one month
            balance *= (1 + annual_rate / compounding_frequency) ** (compounding_frequency / 12)
            
            # Process additions at end of period
            if additions_per_yr == 12:
                balance += addition
                year_invested += addition
            elif additions_per_yr == 4 and month % 3 == 0:
                balance += addition
                year_invested += addition
            elif additions_per_yr == 1 and month == 12:
                balance += addition
                year_invested += addition
                
        cumulative_invested += year_invested
        year_end_balance = balance
        year_interest = year_end_balance - year_start_balance - year_invested
        cumulative_interest = year_end_balance - cumulative_invested
        
        yearly_breakdown.append({
            "year": year,
            "invested_this_year": round(year_invested, 2),
            "interest_this_year": round(year_interest, 2),
            "cumulative_invested": round(cumulative_invested, 2),
            "cumulative_interest": round(cumulative_interest, 2),
            "balance": round(balance, 2)
        })
        
    return {
        "final_amount": round(balance, 2), 
        "total_invested": round(cumulative_invested, 2),
        "total_interest": round(balance - cumulative_invested, 2),
        "yearly_breakdown": yearly_breakdown
    }

# 3. Configure Gemini
# SECURITY WARNING: Never hardcode your API key in a real app. Use environment variables.

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Define the tool for Gemini
tools = [calculate_compound_interest]

model = genai.GenerativeModel(
    model_name='gemini-3.1-flash-lite-preview',
    tools=tools
)

# --- LOGGING SYSTEM ---
def log_process(step, details):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {step.upper()}: {details}\n"
    with open("llm_activity.log", "a") as f:
        f.write(log_entry)
    print(log_entry)

@app.route('/chat', methods=['POST'])
def handle_request():
    data = request.json
    user_prompt = data.get('prompt')

    log_process("User Request", user_prompt)

    try:
        # Start a chat session
        chat = model.start_chat(enable_automatic_function_calling=True)

        # Send the message to Gemini
        response = chat.send_message(user_prompt)

        # Build readable history log
        history_log = []
        formatted_log_lines = [f"\n--- NEW REQUEST: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---"]
        formatted_log_lines.append(f"USER: {user_prompt}\n")

        for content in chat.history:
            role = content.role.upper()
            role_log = f"{role}:\n"
            
            for part in content.parts:
                if part.function_call:
                    # Pretty-print arguments if they exist
                    args_dict = {k: v for k, v in part.function_call.args.items()} if hasattr(part.function_call, 'args') else {}
                    role_log += f"  [Function Call] -> {part.function_call.name}\n"
                    role_log += f"  [Arguments]     -> {args_dict}\n"
                    history_log.append(f"Called function: {part.function_call.name}")
                elif part.function_response:
                    role_log += f"  [Function Reply]-> {part.function_response.name} successfully returned exact math.\n"
                    history_log.append(f"Function response received for {part.function_response.name}")
                else:
                    text_content = part.text.strip().replace('\n', '\n    ')
                    role_log += f"  [Text]          -> {text_content}\n"
                    history_log.append(part.text)
                    
            formatted_log_lines.append(role_log)
            
        formatted_log_lines.append("--------------------------------------------------\n")
        
        # Write to log file in readable format
        with open("llm_activity.log", "a", encoding='utf-8') as f:
            f.write("\n".join(formatted_log_lines))
        print("\n".join(formatted_log_lines))
        
        return jsonify({
            "answer": response.text,
            "logs": history_log
        })

    except Exception as e:
        log_process("ERROR", str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(port=5000, debug=True)