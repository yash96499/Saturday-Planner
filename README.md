# 🗓️ Perfect Saturday Planner

An AI-powered agent that helps you plan a fun, personalised Saturday based on your city, budget, mood, interests, and constraints.

**Live URL:** _[to be added after deployment]_

## How it works

You enter your preferences, and an AI agent runs a pipeline of 6 tools to build your plan:

1. **parse_preferences** — normalises your input (handles aliases like "Bombay" → Mumbai, parses time formats)
2. **get_activities** — discovers live OpenStreetMap places, then scores them by interest, mood, and time
3. **get_food_spots** — discovers live restaurants and filters them by dietary constraints and budget
4. **estimate_cost** — calculates total spend with a 10% miscellaneous buffer
5. **validate_plan** — checks plan against budget, time, and constraints; flags trade-offs
6. **generate_final_plan** — uses Gemini to write a friendly, time-blocked plan with explanations
7. **1-Shot Followup & Refinement** — allows user to request a tweak (e.g., "swap lunch for Italian", "make it cheaper") and regenerates the itinerary seamlessly

The agent trace is visible in the UI so you can see exactly what happened at each step.

## Run locally

```bash
# clone the repo
git clone <repo-url>
cd Saturday_Planner

# install dependencies (Python 3.9+)
pip install -r requirements.txt

# set your Gemini API key (free at https://aistudio.google.com/apikey)
export GEMINI_API_KEY="your-key-here"

# run the app
streamlit run app.py
```

The app will open at `http://localhost:8501`.

## Tech stack

- **Python + Streamlit** for the UI (fast to build, easy to deploy)
- **Google Gemini 2.0 Flash** for natural language plan generation (free tier)
- **OpenStreetMap Nominatim + Overpass** for keyless, live place discovery
- **Custom agent pipeline** — no LangChain, just clean Python functions orchestrated in sequence
- Curated fallback data for Bangalore, Delhi, Mumbai, Gurgaon, Hyderabad, and Pune when a public endpoint is unavailable

## AI tools used during development

I used AI tools to accelerate the initial prototype, validate the agent flow, and quickly debug edge cases during implementation. The final product still reflects my own decisions on structure, heuristics, UX, and validation — I used the AI as a productivity and learning aid rather than as a substitute for the actual engineering work.

## Data behavior

The planner attempts live discovery for any city through OpenStreetMap. Public map data can have missing prices, ratings, dietary tags, or opening hours, so the app labels costs as estimates and asks users to verify details. If a public endpoint fails or returns no useful candidates, supported cities use curated fallback data and other cities use generic fallback suggestions.
