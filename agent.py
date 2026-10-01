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
    get_gemini_api_keys,
)

FALLBACK_SOURCE = "curated fallback"


# System prompt for final plan generation
PLANNER_PROMPT = """You are a friendly, knowledgeable local guide helping someone plan their perfect Saturday.

Based on the activities, food spots, cost estimate, and validation results provided, create a personalized Saturday plan.

Guidelines:
- Create a TIME-BLOCKED schedule starting PRECISELY at the user's Preferred Start Time (e.g., if start time is 5:00 PM, your first time block must be 5:00 PM, NOT 10:00 AM!).
- If a Chosen Anchor Activity was selected by the user, feature it as the central highlight of the day and explain why it anchors their vibe.
- Pick the 2-4 BEST activities and 1-2 food spots from the options — don't include everything.
- For each pick, briefly explain WHY it fits their mood/interests/timing (1 line).
- Include practical tips (best time to visit, what to try, parking, etc.).
- Mention the total budget usage naturally.
- If there are warnings or trade-offs, weave them in ("this place can get busy, so arrive by...").
- Keep the tone warm and conversational — like a friend who knows the city well.
- Use ₹ for prices.
- If the city isn't well-known to you, be honest and use the data provided.
- End with a short "Why this plan works for you" summary.

IMPORTANT: The plan should feel realistic and actionable, not generic. Use specific names and details from the data.
"""


def _format_time_from_hour(hour_val: float) -> str:
    """Format numeric hour (e.g. 17.5) into user-friendly time string (5:30 PM)."""
    h = int(hour_val)
    m = int(round((hour_val - h) * 60))
    suffix = "AM" if (h % 24) < 12 else "PM"
    display_h = (h % 24) % 12
    if display_h == 0:
        display_h = 12
    return f"{display_h}:{m:02d} {suffix}"


def run_agent(user_input: dict, selected_anchor: dict = None, on_step=None):
    """
    Run the Saturday planner agent pipeline.

    Args:
        user_input: dict with city, budget, available_time, start_time, mood, interests, constraints
        selected_anchor: optional user-selected anchor activity from interactive step
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
            f"{city_note} | Start: {prefs['start_time']} | Budget: ₹{prefs['budget']} | Time: {prefs['available_hours']}h",
            data=prefs)
    except Exception as e:
        log("parse_preferences", "error", f"Failed to parse input: {str(e)}")
        return _error_result(trace, "Couldn't understand your input. Please check the fields and try again.")

    # ---- Step 2: Find activities ----
    log("get_activities", "running", f"Finding things to do in {prefs['city']} around {prefs['start_time']}...")
    activities = get_activities(
        prefs["city_key"], prefs["interests"],
        prefs["mood"], prefs["available_hours"],
        start_hour=prefs["start_hour"]
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

    selected_acts = []
    selected_food = []
    remaining_time = prefs["available_hours"]

    # If the user selected an anchor activity, prioritize it as the primary event
    if selected_anchor:
        selected_acts.append(selected_anchor)
        remaining_time -= selected_anchor.get("duration_hours", 1.5)

    # Priority: 1 food spot if time >= 2 hours
    if food and remaining_time >= 2.0:
        selected_food.append(food[0])
        remaining_time -= 1.0  # reserve ~1 hour for food

    anchor_name = selected_anchor["name"].lower() if selected_anchor else ""
    for act in activities:
        if act["name"].lower() == anchor_name:
            continue
        act_dur = act.get("duration_hours", 1.5)
        if act_dur <= remaining_time:
            selected_acts.append(act)
            remaining_time -= act_dur
            if len(selected_acts) >= 3:
                break

    # If no activity fit in remaining time, pick the shortest top-ranked activity
    if not selected_acts and activities:
        selected_acts.append(min(activities, key=lambda x: x.get("duration_hours", 2.0)))

    # If plenty of time remaining, add a second food spot (e.g. snack/dinner)
    if len(food) > 1 and remaining_time >= 1.5:
        selected_food.append(food[1])
        remaining_time -= 1.0

    cost = estimate_cost(selected_acts, selected_food)
    budget_pct = round(cost["total_estimated"] / prefs["budget"] * 100) if prefs["budget"] > 0 else 0
    anchor_msg = f" (Anchored around '{selected_anchor['name']}')" if selected_anchor else ""
    log("estimate_cost", "done",
        f"Estimated total: ₹{cost['total_estimated']} ({budget_pct}% of ₹{prefs['budget']} budget for {len(selected_acts)} activities & {len(selected_food)} food spots){anchor_msg}",
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

    activities = selected_acts
    food = selected_food

    # ---- Step 6: Generate final plan with LLM ----
    log("generate_final_plan", "running", f"Crafting your personalized plan starting at {prefs['start_time']}...")
    try:
        final_plan = _generate_plan_with_llm(prefs, activities, food, cost, validation, selected_anchor=selected_anchor)
        log("generate_final_plan", "done", "Your Saturday plan is ready! 🎉")
    except Exception as e:
        log("generate_final_plan", "warning", f"LLM unavailable ({str(e)[:50]}), using template")
        final_plan = _generate_fallback_plan(prefs, activities, food, cost, validation, selected_anchor=selected_anchor)
        log("generate_final_plan", "done", "Generated plan using template (LLM was unavailable)")

    return {
        "plan": final_plan,
        "trace": trace,
        "cost": cost,
        "validation": validation,
        "preferences": prefs,
        "selected_anchor": selected_anchor,
    }


def _generate_plan_with_llm(prefs, activities, food, cost, validation, selected_anchor=None):
    """Use Google Gemini to generate a nicely written plan with automatic multi-key fallback."""
    if genai is None:
        raise ValueError("Google Gemini SDK is not installed")

    keys = get_gemini_api_keys()
    if not keys:
        raise ValueError("No Gemini API key found")

    anchor_context = ""
    if selected_anchor:
        anchor_context = (
            f"\n- User's Chosen Anchor Activity: {selected_anchor['name']} "
            f"(Feature this as the primary highlight of their day, honoring its timing and trade-off: '{selected_anchor.get('trade_off', '')}')"
        )

    context = f"""
User Preferences:
- City: {prefs['city']}
- Preferred Start Time: {prefs['start_time']} (MANDATORY: schedule must start at this time!)
- Available Duration: {prefs['available_hours']} hours
- Budget: ₹{prefs['budget']}
- Mood: {prefs['mood']}
- Interests: {', '.join(prefs['interests']) if prefs['interests'] else 'not specified'}
- Constraints: {', '.join(prefs['constraints']) if prefs['constraints'] else 'none'}{anchor_context}

Available Activities (ranked by relevance):
{json.dumps([{k: v for k, v in a.items() if not k.startswith('_')} for a in activities[:5]], indent=2)}

Food Options:
{json.dumps(food[:4], indent=2)}

Cost Estimate (for top picks):
{json.dumps(cost, indent=2)}

Validation Notes:
{json.dumps(validation, indent=2)}

Now create the personalized Saturday plan starting at {prefs['start_time']}. Pick the BEST 2-3 activities and 1-2 food spots, not all of them.
"""

    last_error = None
    for idx, api_key in enumerate(keys):
        try:
            genai.configure(api_key=api_key)
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
            last_error = e
            # Try next key if quota exceeded (429), rate limited, or invalid
            continue

    if last_error:
        raise last_error


def _generate_fallback_plan(prefs, activities, food, cost, validation, selected_anchor=None):
    """Template-based plan when LLM is not available."""
    lines = []
    lines.append(f"## 🗓️ Your Saturday in {prefs['city']}\n")

    current_hour = prefs.get("start_hour", 10.0)

    top_activities = activities[:3]
    top_food = food[:2]

    # Interleave activities and food with realistic calculated timings
    for activity in top_activities[:2]:
        slot_time = _format_time_from_hour(current_hour)
        is_anchor = " ⭐ *[Your Selected Anchor Experience]*" if (selected_anchor and activity["name"] == selected_anchor["name"]) else ""
        lines.append(f"### {slot_time} — {activity['name']}{is_anchor}")
        lines.append(f"{activity['description']}")
        lines.append(f"*Cost: ₹{activity.get('cost', 0)} | Duration: ~{activity['duration_hours']}h*\n")
        current_hour += activity.get("duration_hours", 1.5)

    if top_food:
        slot_time = _format_time_from_hour(current_hour)
        meal_name = "Lunch" if current_hour < 16.0 else "Dinner / Bites"
        lines.append(f"### {slot_time} — {meal_name} at {top_food[0]['name']}")
        lines.append(f"{top_food[0]['description']}")
        lines.append(f"*Cost: ~₹{top_food[0]['cost_per_person']} per person | {top_food[0]['cuisine']}*\n")
        current_hour += 1.0  # 1 hour for meal

    if len(top_activities) > 2:
        activity = top_activities[2]
        slot_time = _format_time_from_hour(current_hour)
        lines.append(f"### {slot_time} — {activity['name']}")
        lines.append(f"{activity['description']}")
        lines.append(f"*Cost: ₹{activity.get('cost', 0)} | Duration: ~{activity['duration_hours']}h*\n")
        current_hour += activity.get("duration_hours", 1.5)

    if len(top_food) > 1:
        slot_time = _format_time_from_hour(current_hour)
        lines.append(f"### {slot_time} — Evening Bites at {top_food[1]['name']}")
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
    Refine the generated plan based on 1-shot user feedback with multi-key fallback.
    """
    keys = get_gemini_api_keys()

    if not keys or not genai:
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

    last_error = None
    for api_key in keys:
        try:
            genai.configure(api_key=api_key)
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
            last_error = e
            continue

    err_msg = f" ({str(last_error)[:60]})" if last_error else ""
    return (
        f"{previous_plan}\n\n"
        f"---\n"
        f"🔄 **Refinement Applied:** '{feedback}'{err_msg}."
    )
