# 🗓️ Perfect Saturday Planner

An AI-powered agent that helps you plan a fun, personalised Saturday based on your city, budget, mood, interests, and constraints.

**Live URL:** _[to be added after deployment]_

## How it works

You enter your preferences, and an AI agent runs a pipeline of 6 tools to build your plan:

1. **parse_preferences** — normalises your input (handles aliases like "Bombay" → Mumbai, parses time formats)
2. **get_activities** — scores and ranks activities by interest match, mood fit, and time feasibility
3. **get_food_spots** — filters restaurants by dietary constraints, budget, and crowd preference
4. **estimate_cost** — calculates total spend with a 10% miscellaneous buffer
5. **validate_plan** — checks plan against budget, time, and constraints; flags trade-offs
6. **generate_final_plan** — uses GPT-4o-mini to write a friendly, time-blocked plan with explanations

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
- **Custom agent pipeline** — no LangChain, just clean Python functions orchestrated in sequence
- Mock data with real place names for Bangalore, Delhi, Mumbai, Gurgaon, Hyderabad, and Pune

## AI tools used during development

I used AI tools to accelerate the initial prototype, validate the agent flow, and quickly debug edge cases during implementation. The final product still reflects my own decisions on structure, heuristics, UX, and validation — I used the AI as a productivity and learning aid rather than as a substitute for the actual engineering work.

## Supported cities

Bangalore, Delhi, Mumbai, Gurgaon, Hyderabad, Pune — with realistic mock data. Other cities get generic but still useful suggestions.
