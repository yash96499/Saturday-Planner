"""
Agent orchestrator for the Saturday Planner.
Runs tools in a logical pipeline, uses Gemini for the final plan generation.
No LangChain — just a clean, understandable agent loop.
"""

import os
import json
import time

try:
    import google.generativeai as genai
except ModuleNotFoundError:  # pragma: no cover - handled gracefully at runtime
    genai = None

from tools import (
    parse_preferences,
    get_activities,
    get_food_spots,
    estimate_cost,
    validate_plan,
)

FALLBACK_SOURCE = "curated fallback"


# System prompt for final plan generation
PLANNER_PROMPT = """You are a friendly, knowledgeable local guide helping someone plan their perfect Saturday.

Based on the activities, food spots, cost estimate, and validation results provided, create a personalized Saturday plan.

Guidelines:
- Create a TIME-BLOCKED schedule (e.g., "10:00 AM — Morning walk at Cubbon Park")
- Pick the 2-4 BEST activities and 1-2 food spots from the options — don't include everything
- For each pick, briefly explain WHY it fits their mood/interests (1 line)
- Include practical tips (best time to visit, what to try, parking, etc.)
- Mention the total budget usage naturally
- If there are warnings or trade-offs, weave them in ("this place can get busy, so arrive by...")
- Keep the tone warm and conversational — like a friend who knows the city well
- Use ₹ for prices
- If the city isn't well-known to you, be honest and use the data provided
- End with a short "Why this plan works for you" summary

IMPORTANT: The plan should feel realistic and actionable, not generic. Use specific names and details from the data.
"""


def run_agent(user_input: dict, on_step=None):
    """
    Run the Saturday planner agent pipeline.

    Args:
        user_input: dict with city, budget, available_time, mood, interests, constraints
        on_step: callback function(step_name, status, detail) for live trace updates

    Returns:
        dict with plan, trace, cost, validation results
    """
    trace = []

    def log(tool_name, status, detail="", data=None):
        step = {
            "tool": tool_name,
            "status": status,
            "detail": detail,
            "timestamp": time.time(),
        }
        if data:
            step["data"] = data
        trace.append(step)
        if on_step:
            on_step(tool_name, status, detail)

    # ---- Step 1: Parse preferences ----
    log("parse_preferences", "running", "Parsing your preferences...")
    try:
        prefs = parse_preferences(user_input)
        city_note = f"City: {prefs['city']}"
        if not prefs["is_known_city"]:
            city_note += " (not in our database — using general suggestions)"
        log("parse_preferences", "done",
            f"{city_note} | Budget: ₹{prefs['budget']} | Time: {prefs['available_hours']}h",
            data=prefs)
    except Exception as e:
        log("parse_preferences", "error", f"Failed to parse input: {str(e)}")
        return _error_result(trace, "Couldn't understand your input. Please check the fields and try again.")

    # ---- Step 2: Find activities ----
    log("get_activities", "running", f"Finding things to do in {prefs['city']}...")
    activities = get_activities(
        prefs["city_key"], prefs["interests"],
        prefs["mood"], prefs["available_hours"]
    )
    if activities:
        activity_source = activities[0].get("_data_source", FALLBACK_SOURCE)
        log("get_activities", "done",
            f"Found {len(activities)} matching activities from {activity_source}",
            data=[{"name": a["name"], "score": a["_relevance_score"],
                   "source": a.get("_data_source", FALLBACK_SOURCE)}
                  for a in activities])
    else:
        log("get_activities", "warning", "No perfect matches — using broader suggestions")
        # fallback: try without mood filter
        from mock_data import DEFAULT_ACTIVITIES
        activities = [{**activity, "_data_source": FALLBACK_SOURCE}
                      for activity in DEFAULT_ACTIVITIES[:3]]

    # ---- Step 3: Find food spots ----
    log("get_food_spots", "running", "Finding food options...")
    food = get_food_spots(
        prefs["city_key"], prefs["budget"],
        prefs["constraints"], prefs["interests"]
    )
    if food:
        dietary_note = "vegetarian " if any("veg" in c for c in prefs["constraints"]) else ""
        food_source = food[0].get("_data_source", FALLBACK_SOURCE)
        log("get_food_spots", "done",
            f"Found {len(food)} {dietary_note}food spots from {food_source} within budget",
            data=[{"name": f["name"], "cost": f["cost_per_person"],
                   "source": f.get("_data_source", FALLBACK_SOURCE)}
                  for f in food])
    else:
        log("get_food_spots", "warning", "Limited food options for your constraints")
        from mock_data import DEFAULT_FOOD
        food = [{**spot, "_data_source": FALLBACK_SOURCE}
            for spot in DEFAULT_FOOD]

    # ---- Step 4: Select feasible itinerary & Estimate costs ----
    log("estimate_cost", "running", "Selecting best-fit items and calculating costs...")
    
    # Intelligently select activities & food spots that fit the user's available time and budget
    selected_acts = []
    selected_food = []
    remaining_time = prefs["available_hours"]
    
    # Priority: 1 food spot if time >= 2 hours
    if food and remaining_time >= 2.0:
        selected_food.append(food[0])
        remaining_time -= 1.0  # reserve ~1 hour for food
        
    for act in activities:
        act_dur = act.get("duration_hours", 1.5)
        if act_dur <= remaining_time:
            selected_acts.append(act)
            remaining_time -= act_dur
            if len(selected_acts) >= 3:
                break
                
    # If no activity fit in remaining time, at least pick the shortest top-ranked activity
    if not selected_acts and activities:
        selected_acts.append(min(activities, key=lambda x: x.get("duration_hours", 2.0)))
        
    # If plenty of time remaining, add a second food spot (e.g. snack/dinner)
    if len(food) > 1 and remaining_time >= 1.5:
        selected_food.append(food[1])
        remaining_time -= 1.0

    cost = estimate_cost(selected_acts, selected_food)
    budget_pct = round(cost["total_estimated"] / prefs["budget"] * 100) if prefs["budget"] > 0 else 0
    log("estimate_cost", "done",
        f"Estimated total: ₹{cost['total_estimated']} ({budget_pct}% of ₹{prefs['budget']} budget for {len(selected_acts)} activities & {len(selected_food)} food spots)",
        data=cost)

    # ---- Step 5: Validate plan ----
    log("validate_plan", "running", "Checking against your constraints...")
    validation = validate_plan(
        selected_acts, selected_food, cost,
        prefs["budget"], prefs["constraints"],
        prefs["available_hours"]
    )
    if validation["is_valid"]:
        warn_text = f" ({len(validation['warnings'])} minor notes)" if validation["warnings"] else ""
        log("validate_plan", "done", f"Plan looks good!{warn_text}", data=validation)
    else:
        log("validate_plan", "warning",
            f"Issues found: {'; '.join(validation['issues'])}",
            data=validation)
        # try to adjust — remove most expensive activity
        if selected_acts and cost["total_estimated"] > prefs["budget"]:
            log("validate_plan", "running", "Adjusting plan to fit budget...")
            selected_acts = sorted(selected_acts, key=lambda x: x.get("cost", 0))[:max(1, len(selected_acts)-1)]
            cost = estimate_cost(selected_acts, selected_food)
            validation = validate_plan(
                selected_acts, selected_food, cost,
                prefs["budget"], prefs["constraints"],
                prefs["available_hours"]
            )
            log("validate_plan", "done", "Adjusted plan to better fit constraints")

    # Pass the selected feasible candidate items to the final plan generator
    activities = selected_acts
    food = selected_food

    # ---- Step 6: Generate final plan with LLM ----
    log("generate_final_plan", "running", "Crafting your personalized plan...")
    try:
        final_plan = _generate_plan_with_llm(prefs, activities, food, cost, validation)
        log("generate_final_plan", "done", "Your Saturday plan is ready! 🎉")
    except Exception as e:
        log("generate_final_plan", "warning", f"LLM unavailable ({str(e)[:50]}), using template")
        final_plan = _generate_fallback_plan(prefs, activities, food, cost, validation)
        log("generate_final_plan", "done", "Generated plan using template (LLM was unavailable)")

    return {
        "plan": final_plan,
        "trace": trace,
        "cost": cost,
        "validation": validation,
        "preferences": prefs,
    }


def _generate_plan_with_llm(prefs, activities, food, cost, validation):
    """Use Google Gemini to generate a nicely written plan."""
    if genai is None:
        raise ValueError("Google Gemini SDK is not installed")

    # try streamlit secrets first, then env var
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        try:
            import streamlit as st
            api_key = st.secrets.get("GEMINI_API_KEY", "")
        except Exception:
            pass

    if not api_key:
        raise ValueError("No Gemini API key found")

    context = f"""
User Preferences:
- City: {prefs['city']}
- Budget: ₹{prefs['budget']}
- Available Time: {prefs['available_hours']} hours
- Mood: {prefs['mood']}
- Interests: {', '.join(prefs['interests']) if prefs['interests'] else 'not specified'}
- Constraints: {', '.join(prefs['constraints']) if prefs['constraints'] else 'none'}

Available Activities (ranked by relevance):
{json.dumps([{k: v for k, v in a.items() if not k.startswith('_')} for a in activities[:5]], indent=2)}

Food Options:
{json.dumps(food[:4], indent=2)}

Cost Estimate (for top picks):
{json.dumps(cost, indent=2)}

Validation Notes:
{json.dumps(validation, indent=2)}

Now create the Saturday plan. Pick the BEST 2-3 activities and 1-2 food spots, not all of them.
"""

    if not genai:
        raise ValueError("Google Gemini SDK is not installed")

    genai.configure(api_key=api_key)

    try:
        model = genai.GenerativeModel(
            model_name="gemini-flash-latest",
            system_instruction=PLANNER_PROMPT,
        )
        response = model.generate_content(
            context,
            generation_config=genai.types.GenerationConfig(
                temperature=0.7,
                max_output_tokens=3000,
            ),
        )
        if response and response.text:
            return response.text
    except Exception as e:
        raise e


def _generate_fallback_plan(prefs, activities, food, cost, validation):
    """Template-based plan when LLM is not available."""
    lines = []
    lines.append(f"## 🗓️ Your Saturday in {prefs['city']}\n")

    # pick top 3 activities and 1-2 food spots
    top_activities = activities[:3]
    top_food = food[:2]

    time_slots = ["10:00 AM", "12:30 PM", "2:00 PM", "4:00 PM", "6:30 PM"]
    slot_idx = 0

    # interleave activities and food
    for activity in top_activities[:2]:
        if slot_idx < len(time_slots):
            lines.append(f"### {time_slots[slot_idx]} — {activity['name']}")
            lines.append(f"{activity['description']}")
            lines.append(f"*Cost: ₹{activity.get('cost', 0)} | Duration: ~{activity['duration_hours']}h*\n")
            slot_idx += 1

    if top_food:
        lines.append(f"### {time_slots[slot_idx]} — Lunch at {top_food[0]['name']}")
        lines.append(f"{top_food[0]['description']}")
        lines.append(f"*Cost: ~₹{top_food[0]['cost_per_person']} per person | {top_food[0]['cuisine']}*\n")
        slot_idx += 1

    if len(top_activities) > 2:
        activity = top_activities[2]
        lines.append(f"### {time_slots[slot_idx]} — {activity['name']}")
        lines.append(f"{activity['description']}")
        lines.append(f"*Cost: ₹{activity.get('cost', 0)} | Duration: ~{activity['duration_hours']}h*\n")
        slot_idx += 1

    if len(top_food) > 1:
        lines.append(f"### {time_slots[min(slot_idx, len(time_slots)-1)]} — Dinner at {top_food[1]['name']}")
        lines.append(f"{top_food[1]['description']}")
        lines.append(f"*Cost: ~₹{top_food[1]['cost_per_person']} per person*\n")

    lines.append("---")
    lines.append("### 💰 Budget Summary")
    lines.append(f"- Estimated total: **₹{cost['total_estimated']}** of ₹{prefs['budget']} budget")
    if validation["warnings"]:
        lines.append(f"\n⚠️ *Note: {'; '.join(validation['warnings'])}*")

    return "\n".join(lines)


def _error_result(trace, message):
    """Return a structured error result."""
    return {
        "plan": f"❌ **Oops!** {message}",
        "trace": trace,
        "cost": {},
        "validation": {"is_valid": False, "issues": [message]},
        "preferences": {},
    }


def refine_plan(previous_plan: str, feedback: str, prefs: dict) -> str:
    """
    Refine the generated plan based on 1-shot user feedback.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        try:
            import streamlit as st
            api_key = st.secrets.get("GEMINI_API_KEY", "")
        except Exception:
            pass

    if not api_key or not genai:
        # Fallback if no LLM: append user note to plan
        return (
            f"{previous_plan}\n\n"
            f"---\n"
            f"🔄 **Refinement Note:** User requested '{feedback}'. "
            f"Please swap or adjust the relevant slot according to this preference!"
        )

    prompt = f"""You previously created this Saturday plan for a user in {prefs.get('city', 'their city')}:

{previous_plan}

The user has given this follow-up feedback to tweak the plan:
"{feedback}"

Update and return the full Saturday plan reflecting their feedback.
Keep the same friendly, time-blocked format and practical advice.
Explicitly mention the adjustment made in response to their feedback.
"""

    genai.configure(api_key=api_key)

    try:
        model = genai.GenerativeModel(
            model_name="gemini-flash-latest",
            system_instruction=PLANNER_PROMPT,
        )
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.7,
                max_output_tokens=3000,
            ),
        )
        if response and response.text:
            return response.text
    except Exception as e:
        err_msg = f" ({str(e)[:60]})"
        return (
            f"{previous_plan}\n\n"
            f"---\n"
            f"🔄 **Refinement Applied:** '{feedback}'{err_msg}."
        )
