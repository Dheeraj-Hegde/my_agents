from flask import Flask, request, jsonify, send_from_directory
from google import genai
from google.genai import types
import os
from dotenv import load_dotenv

# Load .env from parent directory
load_dotenv(dotenv_path="../.env")

app = Flask(__name__, static_folder='static', static_url_path='')

# The client gets the API key from the environment variable `GEMINI_API_KEY`
client = genai.Client()

@app.route('/')
def serve_index():
    return app.send_static_file('index.html')

@app.route('/api/plan_trip', methods=['POST'])
def plan_trip():
    data = request.json
    destination = data.get('destination')
    duration = data.get('duration')
    travel_type = data.get('travel_type', 'general')
    season = data.get('season', 'best time')
    interests = data.get('interests')
    
    if not destination or not duration:
        return jsonify({"success": False, "error": "Destination and duration are required."}), 400
 
    interests_str = f" The traveler is interested in: {interests}." if interests else ""
    prompt = f"""
    Plan a detailed {travel_type} trip itinerary for {duration} days in {destination} during the {season} season.{interests_str}
    
    Return the response as a JSON object with three keys:
    1. 'itinerary': A day-by-day plan with specific activities. For every attraction or site mentioned, include the IDEAL TIME TO SPEND there (e.g., "2-3 hours"). Use emojis, clear headers, and bullet points. Tailor activities to the {season} season.
    2. 'budget': Estimated total cost for {destination}. Provide costs in BOTH the Local Currency AND in USD for every item and total. Include a comparison table for Peak vs Low Season. Use emojis for categories.
    3. 'guide': A "Travel Guide & Seasonal Comparison" for {destination}. Include a seasonal comparison table and a one-day "Sample Highlight" for each season.
    
    Format everything in beautiful, clean Markdown.
    """
    
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        
        try:
            import json
            plan_data = json.loads(response.text)
        except json.JSONDecodeError:
            return jsonify({
                "success": False, 
                "error": "The AI provided an invalid response format. Please try again."
            }), 500
                
        return jsonify({
            "success": True, 
            "itinerary": plan_data.get('itinerary', ''),
            "budget": plan_data.get('budget', ''),
            "guide": plan_data.get('guide', '')
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=3000)
