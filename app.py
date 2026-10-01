import streamlit as st
import datetime
import json
import time

from agent import run_agent, refine_plan
from tools import extract_preferences_from_text, get_anchor_options, _format_start_time

# --- Page config ---
st.set_page_config(
    page_title="Saturday Planner",
    page_icon="🗓️",
    layout="centered",
)

# --- Header ---
st.title("🗓️ Perfect Saturday Planner")
st.caption("Tell me what you're in the mood for, and I'll plan your ideal, realistic Saturday.")

# --- Helper: Reset State ---
def reset_planner():
    st.session_state.pop("pending_prefs", None)
    st.session_state.pop("anchor_options", None)
    st.session_state.pop("plan_result", None)
    st.session_state.pop("followup_used", None)
    st.session_state.pop("refinement_note", None)
    st.rerun()

# ==============================================================================
# VIEW 1: Input Preferences (Shown if no plan result and no pending anchor selection)
# ==============================================================================
if "plan_result" not in st.session_state and "pending_prefs" not in st.session_state:
    input_tab1, input_tab2 = st.tabs([
        "✨ Express Mode (Describe Your Day)",
        "🎛️ Custom Filters (Detailed)",
    ])

    # --- TAB 1: EXPRESS MODE ---
    with input_tab1:
        st.markdown("##### Describe your day in your own words")
        st.caption("Our agent will automatically extract your city, preferred start time, budget, and vibe.")

        def set_express_prompt(prompt_text: str):
            st.session_state["express_input_text"] = prompt_text

        st.caption("💡 *Quick Inspirations (click to fill):*")
        col_ex1, col_ex2, col_ex3 = st.columns(3)
        col_ex1.button(
            "☕ Lazy Afternoon (BLR)",
            on_click=set_express_prompt,
            args=("A lazy Saturday afternoon in Bangalore with good filter coffee, book browsing, and dinner under ₹1500",),
            use_container_width=True,
        )
        col_ex2.button(
            "🎸 Evening Vibes (DEL)",
            on_click=set_express_prompt,
            args=("High energy Saturday evening in Delhi with live music and street food under ₹2000",),
            use_container_width=True,
        )
        col_ex3.button(
            "🌅 Morning Walk (BOM)",
            on_click=set_express_prompt,
            args=("Early peaceful morning in Mumbai with seaside walk, quiet cafe, and photography under ₹1000",),
            use_container_width=True,
        )

        express_prompt = st.text_area(
            "How would you like your Saturday to look?",
            placeholder="e.g. A lazy Saturday afternoon in Bangalore with good filter coffee, book browsing, and a quiet dinner under ₹1500",
            height=110,
            key="express_input_text",
        )

        express_submit = st.button("🚀 Explore Activities & Timings", type="primary", use_container_width=True)

        # Allow submission via button or if prompt is non-empty
        prompt_val = (express_prompt or st.session_state.get("express_input_text", "")).strip()

        if express_submit:
            if not prompt_val:
                st.error("Please describe your ideal day or pick one of the inspirations above!")
            else:
                with st.spinner("🤖 Understanding your preferences & finding best spots..."):
                    parsed = extract_preferences_from_text(prompt_val)
                    st.session_state["pending_prefs"] = parsed
                    # Generate anchor options with explicit trade-offs and recommended timing
                    st.session_state["anchor_options"] = get_anchor_options(
                        city_key=parsed["city_key"] if "city_key" in parsed else parsed["city"].lower(),
                        interests=parsed["interests"],
                        mood=parsed["mood"],
                        start_time=parsed["start_time"],
                        budget=parsed["budget"],
                        available_hours=parsed["available_hours"],
                    )
                    st.rerun()

    # --- TAB 2: DETAILED FORM MODE ---
    with input_tab2:
        with st.form("detailed_planner_form"):
            col1, col2 = st.columns(2)

            with col1:
                city = st.text_input(
                    "City",
                    value="Bangalore",
                    placeholder="e.g. Bangalore, Delhi, Mumbai",
                    help="Detailed data for Bangalore, Delhi, Mumbai, Gurgaon, Hyderabad, and Pune"
                )
                budget = st.number_input(
                    "Budget (₹)", min_value=0, max_value=50000,
                    value=2000, step=100
                )
                time_window = st.selectbox(
                    "When does your Saturday start?",
                    options=[
                        "Morning (around 8:00 AM)",
                        "Late Morning (around 10:00 AM)",
                        "Afternoon (around 1:00 PM)",
                        "Evening (around 5:00 PM)",
                        "Custom start time",
                    ],
                    index=1,
                )
                if time_window == "Custom start time":
                    custom_start = st.time_input("Pick start time", value=datetime.time(10, 0))
                    start_time_str = custom_start.strftime("%I:%M %p")
                else:
                    start_time_str = time_window.split("(")[-1].replace("around ", "").replace(")", "").strip()

                available_hours = st.slider(
                    "Available Time (Hours)",
                    min_value=1.0,
                    max_value=10.0,
                    value=4.0,
                    step=0.5,
                )

            with col2:
                mood = st.text_input(
                    "How are you feeling?",
                    value="relaxed",
                    placeholder="e.g. tired, relaxed, energetic, romantic"
                )
                interests = st.multiselect(
                    "Interests",
                    options=[
                        "food", "music", "walks", "nature", "art",
                        "shopping", "books", "games", "photography",
                        "fitness", "culture", "nightlife", "coffee",
                    ],
                    default=["food", "walks"],
                )
                constraints_text = st.text_input(
                    "Any constraints?",
                    placeholder="e.g. vegetarian, avoid crowded places"
                )

            detailed_submit = st.form_submit_button(
                "🚀 Explore Activities & Timings",
                type="primary",
                use_container_width=True,
            )

        if detailed_submit:
            if not city.strip():
                st.error("Please enter a city to get started!")
            else:
                constraints = [c.strip() for c in constraints_text.split(",") if c.strip()]
                prefs = {
                    "city": city.strip(),
                    "budget": budget,
                    "available_time": f"{available_hours} hours",
                    "available_hours": available_hours,
                    "start_time": start_time_str,
                    "mood": mood.strip() or "relaxed",
                    "interests": interests,
                    "constraints": constraints,
                }
                with st.spinner("🤖 Curating anchor activities & timings..."):
                    st.session_state["pending_prefs"] = prefs
                    st.session_state["anchor_options"] = get_anchor_options(
                        city_key=city.strip().lower(),
                        interests=interests,
                        mood=mood.strip() or "relaxed",
                        start_time=start_time_str,
                        budget=budget,
                        available_hours=available_hours,
                    )
                    st.rerun()

# ==============================================================================
# VIEW 2: Interactive Anchor Selection & Trade-Offs (Step 1 -> Step 2 Flow)
# ==============================================================================
elif "pending_prefs" in st.session_state and "plan_result" not in st.session_state:
    prefs = st.session_state["pending_prefs"]
    options = st.session_state.get("anchor_options", [])

    st.markdown("### 🎯 Step 1: Choose Your Main Anchor Experience")
    st.write(
        f"Planning for **{prefs['city']}** starting at **{prefs['start_time']}** "
        f"(duration: **~{prefs['available_hours']}h**, budget: **₹{prefs['budget']}**)."
    )
    st.caption("Pick an anchor activity below to center your Saturday around its vibe & timing, or let the agent plan everything:")

    chosen_anchor = None
    surprise_me = False

    # Render anchor activity cards
    if options:
        cols = st.columns(len(options))
        for idx, opt in enumerate(options):
            with cols[idx]:
                with st.container(border=True):
                    st.markdown(f"#### {opt['vibe']}")
                    st.markdown(f"**{opt['name']}**")
                    st.caption(f"🕒 **Timing:** {opt['recommended_timing']}")
                    st.caption(f"💰 **Cost:** ₹{opt['cost']} | ⏳ **Duration:** ~{opt['duration_hours']}h")
                    st.info(f"💡 **Trade-off:** {opt['trade_off']}")
                    if st.button(f"👉 Select This & Plan", key=f"btn_opt_{idx}", use_container_width=True):
                        chosen_anchor = opt

    st.divider()
    col_act1, col_act2 = st.columns([2, 1])
    with col_act1:
        if st.button("🎲 Surprise Me (Plan Entire Day Automatically)", use_container_width=True):
            surprise_me = True
    with col_act2:
        if st.button("← Back / Edit", use_container_width=True):
            st.session_state.pop("pending_prefs", None)
            st.session_state.pop("anchor_options", None)
            st.rerun()

    # If anchor chosen or surprise me selected, run the agent
    if chosen_anchor or surprise_me:
        st.divider()
        trace_container = st.status(
            "🤖 Planning your tailored Saturday...",
            expanded=True,
        )

        def on_step(tool_name, status, detail):
            icon = {"done": "✅", "warning": "⚠️", "error": "❌", "running": "⏳"}.get(
                status, "🔧"
            )
            with trace_container:
                st.markdown(f"{icon} **{tool_name}** — {detail}")
            time.sleep(0.3)

        selected_anchor_act = chosen_anchor["activity"] if chosen_anchor else None
        result = run_agent(prefs, selected_anchor=selected_anchor_act, on_step=on_step)

        st.session_state["plan_result"] = result
        st.session_state["followup_used"] = False
        st.session_state["refinement_note"] = None
        st.session_state.pop("pending_prefs", None)

        trace_container.update(
            label="✅ Planning complete!",
            state="complete",
            expanded=False,
        )
        st.rerun()

# ==============================================================================
# VIEW 3: Final Plan Display & Refinement
# ==============================================================================
if "plan_result" in st.session_state:
    result = st.session_state["plan_result"]

    # Header action bar
    top_col1, top_col2 = st.columns([3, 1])
    with top_col1:
        st.markdown("### 🗓️ Your Personalized Saturday Itinerary")
    with top_col2:
        if st.button("🔄 Plan Another Day", use_container_width=True):
            reset_planner()

    # Display the plan markdown
    st.markdown(result["plan"])

    # 1-Shot Followup / Refinement Box
    st.markdown("### 💬 Want to tweak this plan?")
    if not st.session_state.get("followup_used", False):
        refine_col1, refine_col2 = st.columns([4, 1])
        with refine_col1:
            tweak_input = st.text_input(
                "Feedback / Adjustments (1 refinement allowed)",
                placeholder="e.g. 'Make it more relaxing', 'Swap breakfast for South Indian', 'Keep strictly under ₹500'",
                label_visibility="collapsed",
                key="tweak_input_field",
            )
        with refine_col2:
            refine_btn = st.button("🔄 Refine", use_container_width=True)

        if refine_btn:
            if tweak_input.strip():
                with st.spinner("🤖 Refining your plan based on your feedback..."):
                    updated_plan = refine_plan(
                        previous_plan=result["plan"],
                        feedback=tweak_input.strip(),
                        prefs=result.get("preferences", {}),
                    )
                    st.session_state["plan_result"]["plan"] = updated_plan
                    st.session_state["followup_used"] = True
                    st.session_state["refinement_note"] = tweak_input.strip()
                    st.rerun()
            else:
                st.warning("Please enter your adjustment request first.")
    else:
        st.success(
            f"✨ **Plan refined** with request: *'{st.session_state.get('refinement_note', '')}'* (Max 1 refinement used)"
        )

    # Cost summary expander
    if result.get("cost"):
        with st.expander("💰 Cost Breakdown", expanded=False):
            cost = result["cost"]
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Activities", f"₹{cost.get('activity_cost', 0)}")
            col_b.metric("Food", f"₹{cost.get('food_cost', 0)}")
            col_c.metric("Total", f"₹{cost.get('total_estimated', 0)}")

            if cost.get("breakdown"):
                st.caption("Activity costs:")
                for item in cost["breakdown"].get("activities", []):
                    st.text(f"  {item['name']}: ₹{item['cost']}")
                st.caption("Food costs:")
                for item in cost["breakdown"].get("food", []):
                    st.text(f"  {item['name']}: ₹{item['cost']}")

    # Validation notes expander
    if result.get("validation"):
        v = result["validation"]
        if v.get("warnings"):
            with st.expander("📝 Notes & Trade-offs", expanded=False):
                for w in v["warnings"]:
                    st.warning(w)
        if v.get("issues"):
            for issue in v["issues"]:
                st.error(issue)

    # Full agent trace expander
    with st.expander("🔍 Agent Trace (what happened under the hood)", expanded=False):
        for step in result.get("trace", []):
            icon = {"done": "✅", "warning": "⚠️", "error": "❌", "running": "⏳"}.get(
                step["status"], "🔧"
            )
            st.markdown(f"{icon} **{step['tool']}**: {step['detail']}")
            if step.get("data"):
                st.json(step["data"])

# --- Footer ---
st.divider()
st.caption(
    "Built with Streamlit + Gemini | "
    "Live places from OpenStreetMap with curated fallback | "
    "[GitHub](https://github.com/yash96499/Saturday-Planner)"
)
