# Triply Dashboard

A stunning, AI-powered travel planning application built with Flask and the **Google Gemini 3.1 Flash** model. This dashboard generates highly customized, day-by-day travel itineraries based on destination, duration, travel style, and specific interests.

![Triply Walkthrough](triply.mp4)

## 🌟 Features

- **Personalized Itineraries**: Generate detailed, day-by-day itineraries tailored to your exact interests.
- **Multiple Travel Styles**: Choose between Backpacking, Luxury, Family, Romantic, Adventure, Cultural, or Relaxing styles to shape the AI's recommendations.
- **Beautiful UI**: Features a modern, bright "travel" aesthetic with glassmorphism components, background photography, and responsive design.
- **Markdown Rendering**: Robustly parses the AI's output into beautiful formatted text, lists, and headers utilizing `marked.js`.

## 📁 Project Structure

```text
trip_planning_dashboard/
├── app.py             # Flask backend & Gemini API integration logic
└── static/
    ├── index.html     # Main UI structure and markup
    ├── style.css      # Custom styling, animations, and typography
    ├── script.js      # Frontend logic, form handling, and API calls
    └── bg.png         # Generated travel theme background image
```

## 🛠️ Prerequisites

Before running the application, ensure you have the following set up:

1. A verified Python environment.
2. The necessary python dependencies installed:
   ```bash
   pip install flask flask-cors google-genai python-dotenv
   ```
3. A `.env` file located in the parent directory (`../.env`) containing your Google Gemini API key:
   ```env
   GEMINI_API_KEY="YourAPIKeyHere..."
   ```

## 🚀 How to Start the App

Follow these exact steps to run the dashboard locally:

1. **Open your terminal (PowerShell or Command Prompt).**

2. **Activate your Python Virtual Environment.**
   Ensure that you are utilizing the correct virtual environment for this project by running:

3. **Navigate to the Project Directory:**

4. **Run the Flask Application:**
   Execute the `app.py` script to start the local web server:
   ```powershell
   python app.py
   ```

5. **Access the Dashboard:**
   Open your preferred web browser and navigate to:  
   **[http://127.0.0.1:3000](http://127.0.0.1:3000)**

You're all set! Enjoy creating your perfect journeys instantly.
