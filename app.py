import streamlit as st
import json
import time

from agent import run_agent, refine_plan

# --- Page config ---
st.set_page_config(
    page_title="Saturday Planner",
    page_icon="🗓️",
    layout="centered",
)

# --- Header ---
st.title("🗓️ Perfect Saturday Planner")
st.caption("Tell me what you're in the mood for, and I'll plan your ideal Saturday.")

# --- Input form ---
with st.form("planner_form"):
    col1, col2 = st.columns(2)

    with col1:
        city = st.text_input(
            "City",
            placeholder="e.g. Bangalore, Delhi, Mumbai",
            help="We have detailed data for Bangalore, Delhi, Mumbai, Gurgaon, Hyderabad, and Pune"
        )
        budget = st.number_input(
            "Budget (₹)", min_value=0, max_value=50000,
            value=2000, step=100
        )
        available_time = st.text_input(
            "Available Time",
            placeholder="e.g. 4 hours, half day, whole day"
        )

    with col2:
        mood = st.text_input(
            "How are you feeling?",
            placeholder="e.g. tired but wants to do something fun"
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

    submitted = st.form_submit_button(
        "🚀 Plan My Saturday",
        use_container_width=True,
    )

# --- Run agent when form is submitted ---
if submitted:
    if not city:
        st.error("Please enter a city to get started!")
        st.stop()

    # parse constraints from comma-separated text
    constraints = [c.strip() for c in constraints_text.split(",") if c.strip()]

    user_input = {
        "city": city,
        "budget": budget,
        "available_time": available_time or "4 hours",
        "mood": mood or "relaxed",
        "interests": interests,
        "constraints": constraints,
    }

    # Show what we received
    st.divider()

    # Agent trace — live updates as each tool runs
    trace_container = st.status(
        "🤖 Planning your perfect Saturday...",
        expanded=True,
    )

    step_messages = []
    def on_step(tool_name, status, detail):
        icon = {"done": "✅", "warning": "⚠️", "error": "❌", "running": "⏳"}.get(
            status, "🔧"
        )
        with trace_container:
            st.markdown(f"{icon} **{tool_name}** — {detail}")
        time.sleep(0.3)

    # Run the agent
    result = run_agent(user_input, on_step=on_step)

    # Store in session state so follow-ups can refine it
    st.session_state["plan_result"] = result
    st.session_state["followup_used"] = False
    st.session_state["refinement_note"] = None

    # Update status
    trace_container.update(
        label="✅ Planning complete!",
        state="complete",
        expanded=False,
    )

# --- Display result from session state if available ---
if "plan_result" in st.session_state:
    result = st.session_state["plan_result"]

    # --- Display the plan ---
    st.markdown("---")
    st.markdown(result["plan"])

    # --- 1-Shot Followup / Refinement Box ---
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

    # --- Cost summary in sidebar-style expander ---
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

    # --- Validation notes ---
    if result.get("validation"):
        v = result["validation"]
        if v.get("warnings"):
            with st.expander("📝 Notes & Trade-offs", expanded=False):
                for w in v["warnings"]:
                    st.warning(w)
        if v.get("issues"):
            for issue in v["issues"]:
                st.error(issue)

    # --- Full agent trace (for the evaluator) ---
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
    "[GitHub](https://github.com)"
)
