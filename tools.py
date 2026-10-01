"""
Tool functions for the Saturday Planner agent.
Each tool does one specific thing — the agent orchestrates them.
"""

import os
import re
import json
import time
from mock_data import (
    ACTIVITIES, FOOD_SPOTS,
    DEFAULT_ACTIVITIES, DEFAULT_FOOD,
    CITY_ALIASES,
)
from live_data import fetch_activities, fetch_food_spots

FALLBACK_SOURCE = "curated fallback"


def _parse_start_time(start_time_str: str) -> float:
    """Parse start time string into numeric hour (0.0-24.0), e.g. '8:00 AM' -> 8.0, '1:30 PM' -> 13.5."""
    if not start_time_str:
        return 10.0
    s = start_time_str.lower().strip()
    if "morning" in s and not re.search(r'\d', s):
        return 8.0
    if "afternoon" in s and not re.search(r'\d', s):
        return 13.0
    if ("evening" in s or "night" in s) and not re.search(r'\d', s):
        return 17.0

    match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', s)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        ampm = match.group(3)
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        return hour + minute / 60.0
    return 10.0


def _format_start_time(hour_float: float) -> str:
    """Format numeric hour (e.g. 13.5) into user-friendly time string (1:30 PM)."""
    h = int(hour_float)
    m = int(round((hour_float - h) * 60))
    suffix = "AM" if h < 12 else "PM"
    display_h = h % 12
    if display_h == 0:
        display_h = 12
    return f"{display_h}:{m:02d} {suffix}"


def get_gemini_api_keys() -> list:
    """
    Retrieve all configured Gemini API keys with fallback support.
    Checks environment variables and Streamlit secrets for:
    - GEMINI_API_KEY (single key or comma-separated keys)
    - GEMINI_API_KEY_1, GEMINI_API_KEY_2, GEMINI_API_KEY_3, GEMINI_API_KEY_4, GEMINI_API_KEY_5
    - GEMINI_API_KEYS (list or comma-separated string)
    """
    raw_candidates = []

    # 1. Check Streamlit secrets
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            for name in [
                "GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2",
                "GEMINI_API_KEY_3", "GEMINI_API_KEY_4", "GEMINI_API_KEY_5",
                "GEMINI_API_KEYS"
            ]:
                val = st.secrets.get(name)
                if val:
                    if isinstance(val, (list, tuple)):
                        raw_candidates.extend(val)
                    else:
                        raw_candidates.append(str(val))
    except Exception:
        pass

    # 2. Check environment variables
    for name in [
        "GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3", "GEMINI_API_KEY_4", "GEMINI_API_KEY_5",
        "GEMINI_API_KEYS"
    ]:
        val = os.environ.get(name)
        if val:
            raw_candidates.append(val)

    # 3. Clean, parse comma-separated strings, and deduplicate
    keys = []
    seen = set()
    for item in raw_candidates:
        if not item:
            continue
        parts = re.split(r'[,;\n]+', str(item))
        for part in parts:
            clean = part.strip().strip('"\'')
            if clean and clean not in seen and clean != "your-key-here":
                seen.add(clean)
                keys.append(clean)

    return keys


def extract_preferences_from_text(text: str) -> dict:
    """
    Extract structured preferences from free-text user description.
    Tries Gemini LLM with multi-key fallback, falls back to regex/heuristic extraction.
    """
    if not text or not text.strip():
        return {
            "city": "Bangalore",
            "budget": 2000,
            "start_time": "10:00 AM",
            "available_hours": 4.0,
            "mood": "relaxed",
            "interests": ["food", "walks"],
            "constraints": [],
        }

    keys = get_gemini_api_keys()
    if keys:
        try:
            import google.generativeai as genai
            extraction_prompt = f"""You are a helpful assistant that parses user preferences for weekend planning.
Extract the following fields from the user's description into valid JSON ONLY:
{{
  "city": "city name (default Bangalore)",
  "budget": integer_amount_in_inr (default 2000),
  "start_time": "approx start time like '8:00 AM', '1:00 PM', or '5:00 PM' (default '10:00 AM')",
  "available_hours": float_number_of_hours (default 4.0),
  "mood": "mood description e.g. tired, relaxed, energetic",
  "interests": ["list", "of", "interests" from: food, music, walks, nature, art, shopping, books, games, photography, fitness, culture, nightlife, coffee],
  "constraints": ["list", "of", "constraints" e.g. vegetarian, avoid crowded places]
}}

User description:
"{text}"

Return ONLY the raw JSON object, without backticks or markdown fences.
"""
            for key in keys:
                try:
                    genai.configure(api_key=key)
                    model = genai.GenerativeModel("gemini-flash-latest")
                    res = model.generate_content(extraction_prompt)
                    if res and res.text:
                        clean_text = res.text.strip().replace("```json", "").replace("```", "").strip()
                        parsed = json.loads(clean_text)
                        if isinstance(parsed, dict) and "city" in parsed:
                            parsed["budget"] = int(parsed.get("budget", 2000))
                            parsed["available_hours"] = float(parsed.get("available_hours", 4.0))
                            parsed["start_time"] = str(parsed.get("start_time", "10:00 AM"))
                            parsed["mood"] = str(parsed.get("mood", "relaxed"))
                            parsed["interests"] = list(parsed.get("interests", ["food", "walks"]))
                            parsed["constraints"] = list(parsed.get("constraints", []))
                            return parsed
                except Exception:
                    # Try next fallback key if quota/rate limited or invalid
                    continue
        except Exception:
            pass

    # Heuristic Fallback
    text_lower = text.lower()

    # 1. City extraction
    city = "Bangalore"
    all_city_candidates = list(CITY_ALIASES.keys()) + list(ACTIVITIES.keys()) + [
        "delhi", "mumbai", "bangalore", "bengaluru", "gurgaon", "gurugram", "hyderabad", "pune", "chennai"
    ]
    for c in all_city_candidates:
        if c in text_lower:
            city = CITY_ALIASES.get(c, c).title()
            break

    # 2. Budget extraction
    budget = 2000
    b_match = re.search(r'(?:₹|rs\.?|inr|under|budget\s*of)\s*(\d+)', text_lower)
    if not b_match:
        b_match = re.search(r'(\d+)\s*(?:rs|inr|bucks)', text_lower)
    if b_match:
        budget = int(b_match.group(1))

    # 3. Start time extraction
    start_time = "10:00 AM"
    if "morning" in text_lower or "sunrise" in text_lower or "early" in text_lower:
        start_time = "8:00 AM"
    elif "afternoon" in text_lower or "lunch" in text_lower or "brunch" in text_lower:
        start_time = "1:00 PM"
    elif "evening" in text_lower or "sunset" in text_lower:
        start_time = "5:00 PM"
    elif "night" in text_lower or "dinner" in text_lower:
        start_time = "7:00 PM"

    time_match = re.search(r'(\d{1,2}(?::\d{2})?\s*(?:am|pm))', text_lower)
    if time_match:
        start_time = time_match.group(1).upper()

    # 4. Available hours
    hours = 4.0
    h_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:hours|hrs|hr)', text_lower)
    if h_match:
        hours = float(h_match.group(1))
    elif "whole day" in text_lower or "full day" in text_lower:
        hours = 8.0
    elif "half day" in text_lower:
        hours = 5.0
    elif "quick" in text_lower or "short" in text_lower:
        hours = 2.5

    # 5. Mood
    mood = "relaxed"
    for m in ["tired", "lazy", "chill", "relaxed", "energetic", "peaceful", "curious", "romantic", "fun"]:
        if m in text_lower:
            mood = m
            break

    # 6. Interests
    interest_keywords = {
        "coffee": ["coffee", "cafe", "espresso", "latte", "roastery"],
        "books": ["book", "reading", "library", "bookstore"],
        "walks": ["walk", "stroll", "park", "garden"],
        "nature": ["nature", "lake", "greenery", "trees", "garden"],
        "art": ["art", "gallery", "exhibition", "museum"],
        "music": ["music", "concert", "gig", "indie", "band"],
        "nightlife": ["pub", "bar", "brewery", "drink", "cocktail", "nightlife"],
        "food": ["food", "eat", "lunch", "dinner", "breakfast", "street food", "snack"],
        "shopping": ["shop", "market", "mall", "bazaar"],
        "fitness": ["run", "cycle", "cycling", "gym", "workout"],
        "games": ["game", "arcade", "board game", "bowling"],
        "photography": ["photo", "viewpoint", "sightseeing"],
    }
    detected_interests = []
    for tag, keywords in interest_keywords.items():
        if any(kw in text_lower for kw in keywords):
            detected_interests.append(tag)
    if not detected_interests:
        detected_interests = ["food", "walks"]

    # 7. Constraints
    constraints = []
    if ("vegetarian" in text_lower or "veg" in text_lower) and "non-veg" not in text_lower and "nonveg" not in text_lower:
        constraints.append("vegetarian")
    if "vegan" in text_lower:
        constraints.append("vegan")
    if "crowd" in text_lower or "quiet" in text_lower or "peace" in text_lower:
        constraints.append("avoid crowded places")

    return {
        "city": city,
        "budget": budget,
        "start_time": start_time,
        "available_hours": hours,
        "mood": mood,
        "interests": detected_interests,
        "constraints": constraints,
    }


def parse_preferences(raw_input: dict) -> dict:
    """
    Parse and normalize user input into a structured format.
    Handles messy input — partial fields, weird formats, etc.
    """
    city = raw_input.get("city", "").strip()
    # resolve aliases (bombay -> mumbai, etc.)
    city_lower = city.lower()
    city_key = CITY_ALIASES.get(city_lower, city_lower)

    budget = raw_input.get("budget", 2000)
    if isinstance(budget, str):
        # try to extract number from string like "2000 rs" or "₹2000"
        nums = re.findall(r'\d+', budget)
        budget = int(nums[0]) if nums else 2000

    available_time = raw_input.get("available_time", "4 hours")
    available_hours = _parse_time(available_time)

    start_time = raw_input.get("start_time", "10:00 AM")
    start_hour = _parse_start_time(start_time)

    mood = raw_input.get("mood", "relaxed")
    interests = raw_input.get("interests", [])
    if isinstance(interests, str):
        interests = [i.strip() for i in interests.split(",")]

    constraints = raw_input.get("constraints", [])
    if isinstance(constraints, str):
        constraints = [c.strip() for c in constraints.split(",")]

    # check if city is supported
    is_known_city = city_key in ACTIVITIES

    return {
        "city": city,
        "city_key": city_key,
        "budget": int(budget),
        "available_hours": available_hours,
        "start_time": start_time,
        "start_hour": start_hour,
        "mood": mood,
        "interests": [i.lower() for i in interests],
        "constraints": [c.lower() for c in constraints],
        "is_known_city": is_known_city,
    }


def _parse_time(time_str: str) -> float:
    """Convert time string to hours. Handles various formats."""
    if not time_str:
        return 4.0
    time_str = time_str.lower().strip()

    if "whole day" in time_str or "full day" in time_str:
        return 10.0
    if "half day" in time_str:
        return 5.0
    if "evening" in time_str or "afternoon" in time_str:
        return 4.0
    if "morning" in time_str:
        return 3.0

    match = re.search(r'(\d+\.?\d*)', time_str)
    if match:
        return float(match.group(1))
    return 4.0  # sensible default


def get_activities(city_key: str, interests: list, mood: str,
                   available_hours: float, start_hour: float = 10.0) -> list:
    """
    Find and rank activities based on user preferences.
    Scores each activity by interest match, mood fit, time feasibility, and start time compatibility.
    """
    try:
        activities = fetch_activities(city_key, interests)
    except (ValueError, RuntimeError, OSError):
        activities = []

    if not activities:
        fallback = ACTIVITIES.get(city_key, DEFAULT_ACTIVITIES)
        activities = [{**activity, "_data_source": FALLBACK_SOURCE}
                      for activity in fallback]

    scored = []
    for activity in activities:
        score = 0

        # Interest match (strongest signal)
        for interest in interests:
            matching = [t for t in activity["interest_tags"]
                        if interest in t.lower() or t.lower() in interest]
            if matching:
                score += 3

        # Mood match
        mood_lower = mood.lower()
        for mood_tag in activity["mood_tags"]:
            if mood_tag in mood_lower or mood_lower in mood_tag:
                score += 2
        # "tired" is a common mood — boost low-crowd, low-effort activities
        if "tired" in mood_lower:
            if activity["crowd_level"] == "low":
                score += 1
            if activity["type"] in ("cafe", "indoor", "walk"):
                score += 1

        # Duration check
        if activity["duration_hours"] <= available_hours:
            score += 1
        else:
            score -= 3  # harsh penalty — don't suggest things that don't fit

        # Time-of-day compatibility based on start_hour
        act_time = activity.get("time_of_day", "afternoon")
        if start_hour < 11.5:  # Morning start
            if act_time == "morning":
                score += 2
        elif 11.5 <= start_hour < 16.5:  # Afternoon start
            if act_time in ("afternoon", "indoor"):
                score += 2
            elif act_time == "morning" and activity.get("type") in ("walk", "fitness"):
                score -= 1  # morning walk not ideal in afternoon heat
        else:  # Evening/night start
            if act_time in ("evening", "nightlife") or activity.get("type") in ("music", "nightlife"):
                score += 3
            elif act_time == "morning":
                score -= 2  # morning park walk doesn't fit evening start

        if score > 0:
            scored.append({**activity, "_relevance_score": score})

    scored.sort(key=lambda x: x["_relevance_score"], reverse=True)
    return scored[:6]  # return top 6 for the LLM to pick from


def get_anchor_options(city_key: str, interests: list, mood: str,
                       start_time: str, budget: int, available_hours: float) -> list:
    """
    Generate 2-3 curated starting anchor activity options with distinct vibes,
    recommended timings, and explicit trade-off explanations.
    """
    start_hour = _parse_start_time(start_time)
    activities = get_activities(city_key, interests, mood, available_hours, start_hour=start_hour)
    if not activities:
        fallback = ACTIVITIES.get(city_key, DEFAULT_ACTIVITIES)
        activities = [{**a, "_relevance_score": 3, "_data_source": FALLBACK_SOURCE} for a in fallback]

    # Select up to 3 distinct activities by type/vibe
    selected = []
    seen_types = set()
    for act in activities:
        act_type = act.get("type", "outdoor")
        if act_type not in seen_types:
            seen_types.add(act_type)
            selected.append(act)
        if len(selected) >= 3:
            break

    # If we couldn't get 3 distinct types, take top activities
    if len(selected) < 3:
        for act in activities:
            if act not in selected:
                selected.append(act)
            if len(selected) >= 3:
                break

    options = []
    vibe_map = {
        "walk": ("🌿 Serene & Unhurried", "Early hours avoid crowd & heat, but requires waking up early and light breakfast beforehand."),
        "outdoor": ("🚴 Active & Outdoor Adventure", "Great fresh air and active experience, but weather-dependent and requires physical energy."),
        "art": ("🎨 Culture & Aesthetic Inspiration", "Inspiring exhibits and quiet ambiance, but limited dining on-site."),
        "culture": ("🏛️ Heritage & Curiosity", "Deep historical richness, but can involve standing and walking through exhibits."),
        "music": ("🎸 Vibrant Social & Nightlife", "Energetic live performance and great weekend vibe, but ticket cost and higher noise/crowds."),
        "nightlife": ("🍸 Energetic Evening Hangout", "Lively social ambiance, but higher drinks budget and late-night rush."),
        "indoor": ("☕ Chill & Cozy Indoor", "Completely weather-proof and relaxing, but less active movement."),
        "cafe": ("☕ Specialty Coffee & Conversations", "Low effort and great artisanal flavours, but can get busy during peak hours."),
        "shopping": ("🛍️ Local Boutiques & Street Markets", "Exciting local finds and bustling vibe, but requires navigating weekend crowds."),
        "fitness": ("🏃 High Energy Fitness", "Invigorating workout, but will tire you out for the remainder of the day."),
        "games": ("🎲 Playful & Interactive Chill", "Fun social camaraderie and zero physical strain, but reservations may be needed."),
    }

    for idx, act in enumerate(selected):
        act_type = act.get("type", "indoor")
        vibe_title, default_tradeoff = vibe_map.get(
            act_type,
            ("✨ Curated Local Highlight", "Authentic local spot, but verify weekend schedule beforehand.")
        )

        cost_val = act.get("cost", 0)
        crowd_val = act.get("crowd_level", "medium")
        notes = []
        if cost_val == 0:
            notes.append("Free entry")
        elif cost_val > budget * 0.35 and budget > 0:
            notes.append(f"Takes ~{round(cost_val/budget*100)}% of your ₹{budget} budget")
        else:
            notes.append(f"Cost: ₹{cost_val}")

        if crowd_val == "low":
            notes.append("low crowd / peaceful")
        elif crowd_val == "high":
            notes.append("popular / busy weekend crowd")

        trade_off = f"{default_tradeoff} ({', '.join(notes)})."

        timing_str = _format_start_time(start_hour)

        options.append({
            "id": f"anchor_{idx}",
            "name": act["name"],
            "type": act_type,
            "vibe": vibe_title,
            "recommended_timing": f"Start around {timing_str}",
            "trade_off": trade_off,
            "cost": cost_val,
            "duration_hours": act.get("duration_hours", 1.5),
            "description": act.get("description", ""),
            "activity": act,
        })

    return options


def get_food_spots(city_key: str, budget: int, constraints: list,
                   _interests: list = None) -> list:
    """
    Find food spots matching dietary constraints and budget.
    Budget allocation: ~40% of total budget for food.
    """
    try:
        time.sleep(2)  # avoid Overpass API rate-limiting after activities query
        spots = fetch_food_spots(city_key)
    except (ValueError, RuntimeError, OSError):
        spots = []

    if not spots:
        fallback = FOOD_SPOTS.get(city_key, DEFAULT_FOOD)
        spots = [{**spot, "_data_source": FALLBACK_SOURCE}
                 for spot in fallback]
    food_budget = budget * 0.4  # rule of thumb

    matching = _filter_food_spots(spots, food_budget, constraints)

    # OSM dietary tags are often incomplete. Prefer a constraint-safe curated
    # fallback over presenting an unverified venue as vegetarian or vegan.
    if not matching and any(
            spot.get("_data_source") == "OpenStreetMap" for spot in spots):
        fallback = FOOD_SPOTS.get(city_key, DEFAULT_FOOD)
        fallback = [{**spot, "_data_source": FALLBACK_SOURCE}
                    for spot in fallback]
        matching = _filter_food_spots(fallback, food_budget, constraints)

    # sort by rating
    matching.sort(key=lambda x: x.get("rating", 3.0), reverse=True)
    return matching[:5]


def _filter_food_spots(spots: list, food_budget: float,
                       constraints: list) -> list:
    """Apply hard dietary, crowd, and budget constraints to food candidates."""
    matching = []
    for spot in spots:
        # dietary constraint check
        skip = False
        for constraint in constraints:
            c = constraint.lower()
            if "veg" in c and "non" not in c:
                # user wants vegetarian
                if "vegetarian" not in spot["dietary"]:
                    skip = True
                    break
            if "non-veg" in c or "nonveg" in c:
                if "non-vegetarian" not in spot["dietary"]:
                    skip = True
                    break
            # crowd constraint
            if "crowd" in c and ("avoid" in c or "no" in c):
                if spot["crowd_level"] == "high":
                    skip = True
                    break
        if skip:
            continue

        # budget check (per person)
        if spot["cost_per_person"] <= food_budget:
            matching.append(spot)
    return matching


def estimate_cost(activities: list, food_spots: list) -> dict:
    """Calculate total estimated cost with breakdown."""
    activity_cost = sum(a.get("cost", 0) for a in activities)
    food_cost = sum(f.get("cost_per_person", 0) for f in food_spots)
    misc = round((activity_cost + food_cost) * 0.1)  # 10% for transport/misc
    total = activity_cost + food_cost + misc

    return {
        "activity_cost": activity_cost,
        "food_cost": food_cost,
        "misc_buffer": misc,
        "total_estimated": total,
        "breakdown": {
            "activities": [
                {"name": a["name"], "cost": a.get("cost", 0)}
                for a in activities
            ],
            "food": [
                {"name": f["name"], "cost": f.get("cost_per_person", 0)}
                for f in food_spots
            ],
        }
    }


def validate_plan(activities: list, food_spots: list, cost_data: dict,
                   budget: int, constraints: list, available_hours: float) -> dict:
    """
    Validate plan against budget, time, and constraints.
    Returns issues (blockers) and warnings (nice-to-know).
    """
    issues = []
    warnings = []
    suggestions = []

    total = cost_data.get("total_estimated", 0)

    # Budget check
    if total > budget:
        over_by = total - budget
        issues.append(
            f"Plan is ₹{over_by} over budget (₹{total} vs ₹{budget} limit)"
        )
        suggestions.append("Consider removing the most expensive activity or picking a cheaper food option")
    elif total > budget * 0.9:
        warnings.append(
            f"Tight on budget — using {round(total/budget*100)}% (₹{total} of ₹{budget})"
        )

    # Time check
    total_hours = sum(a.get("duration_hours", 0) for a in activities)
    food_time = len(food_spots) * 1.0  # ~1 hour per food stop
    total_time = total_hours + food_time
    if total_time > available_hours:
        issues.append(
            f"Activities + food total ~{total_time:.1f}h but only {available_hours}h available"
        )
        suggestions.append("Drop one activity or pick quicker food options")
    elif total_time > available_hours * 0.9:
        warnings.append(f"Schedule is packed — ~{total_time:.1f}h of {available_hours}h. Leave buffer for travel.")

    # Crowd constraint
    for constraint in constraints:
        if "crowd" in constraint.lower():
            crowded = [a["name"] for a in activities if a.get("crowd_level") == "high"]
            crowded += [f["name"] for f in food_spots if f.get("crowd_level") == "high"]
            if crowded:
                warnings.append(f"These spots can be crowded: {', '.join(crowded)}. Go early to avoid peak hours.")

    # no activities found
    if not activities:
        issues.append("No matching activities found for your preferences")
        suggestions.append("Try broadening your interests or increasing budget")

    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "suggestions": suggestions,
    }
