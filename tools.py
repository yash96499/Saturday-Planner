"""
Tool functions for the Saturday Planner agent.
Each tool does one specific thing — the agent orchestrates them.
"""

import re
import json
from mock_data import (
    ACTIVITIES, FOOD_SPOTS,
    DEFAULT_ACTIVITIES, DEFAULT_FOOD,
    CITY_ALIASES,
)


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
                   available_hours: float) -> list:
    """
    Find and rank activities based on user preferences.
    Scores each activity by interest match, mood fit, and time feasibility.
    """
    activities = ACTIVITIES.get(city_key, DEFAULT_ACTIVITIES)

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

        if score > 0:
            scored.append({**activity, "_relevance_score": score})

    scored.sort(key=lambda x: x["_relevance_score"], reverse=True)
    return scored[:6]  # return top 6 for the LLM to pick from


def get_food_spots(city_key: str, budget: int, constraints: list,
                   interests: list = None) -> list:
    """
    Find food spots matching dietary constraints and budget.
    Budget allocation: ~40% of total budget for food.
    """
    spots = FOOD_SPOTS.get(city_key, DEFAULT_FOOD)
    food_budget = budget * 0.4  # rule of thumb

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

    # sort by rating
    matching.sort(key=lambda x: x.get("rating", 3.0), reverse=True)
    return matching[:5]


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
