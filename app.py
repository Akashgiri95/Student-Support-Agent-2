"""Chat UI.  Run:  streamlit run app.py"""
import pandas as pd
import streamlit as st
import subprocess
import sys
import re
import json
from datetime import date
import config, db, security
import academic
from agent import StudentSupportAgent

st.set_page_config(page_title="Student Support Agent", page_icon="🎓", layout="wide")
db.init_db()
DEMO_PASSWORD = config.STUDENT_DEMO_PASSWORD
STAFF_DEMO_PASSWORD = config.STAFF_DEMO_PASSWORD
AGENT_RUNTIME_VERSION = 3


def _valid_date(value):
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _policy_wording(value):
    if isinstance(value, dict):
        return {key: _policy_wording(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_policy_wording(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"According to the handbook", "According to policy", value, flags=re.I)
        value = re.sub(r"the student handbook does not contain enough information",
                       "the available policy information does not contain enough detail", value, flags=re.I)
        value = re.sub(r"not enough handbook information", "not enough policy information", value, flags=re.I)
    return value


if st.session_state.get("user") and not st.session_state.get("role"):
    st.session_state["role"] = "student"
if st.session_state.get("agent_runtime_version") != AGENT_RUNTIME_VERSION:
    st.session_state["agent"] = None
    st.session_state["agent_runtime_version"] = AGENT_RUNTIME_VERSION

# ---------------- Sidebar: sign-in, mode, approver panel ----------------
with st.sidebar:
    st.header("🔐 Sign in")
    student_choices = {s["student_id"]: s["name"] for s in db.list_students()}
    sid = st.selectbox("Student ID", list(student_choices),
                       format_func=lambda x: f"{x} - {student_choices[x]}")
    pwd = st.text_input("Password", type="password", help="Configure STUDENT_DEMO_PASSWORD in .env.local.")
    if st.button("Sign in"):
        if not DEMO_PASSWORD:
            st.error("Set STUDENT_DEMO_PASSWORD in .env.local before signing in.")
        elif pwd == DEMO_PASSWORD:
            st.session_state.update(user=sid, role="student", chat=[], agent=None)
        else:
            st.error("Wrong password")
    with st.expander("Programme Office demo sign-in"):
        staff_pwd = st.text_input("Staff demo password", type="password", key="staff_password",
                                  help="Configure STAFF_DEMO_PASSWORD in .env.local.")
        if st.button("Sign in as Programme Office"):
            if not STAFF_DEMO_PASSWORD:
                st.error("Set STAFF_DEMO_PASSWORD in .env.local before signing in.")
            elif staff_pwd == STAFF_DEMO_PASSWORD:
                st.session_state.update(user="programme_office", role="staff", chat=[], agent=None)
            else:
                st.error("Wrong staff password")
    if st.session_state.get("user"):
        st.caption(f"Signed in: {st.session_state.get('role', 'student')} · {st.session_state['user']}")

    st.divider()
    mode_selection = st.radio(
        "Assignment Mode",
        ["Assignment 2 (Safe Agent)", "Assignment 1 (Baseline)"],
        index=0,
        help="Switch between Assignment 1 baseline (no auth/approval) and Assignment 2 (production controls)."
    )
    current_mode = "a2" if "Assignment 2" in mode_selection else "a1"

    if st.button("Sign out"):
        st.session_state.clear()
        st.rerun()

role = st.session_state.get("role")
if role == "staff":
    pending_count = len(security.pending_approvals())
    tab_admin, tab_records, tab_audit, tab_evaluation = st.tabs([
        f"✅ Approvals ({pending_count})", "🎓 Student records", "📜 Change & audit log", "🧪 Evaluation"])
elif role == "student":
    tab_chat, tab_profile, tab_requests, tab_memory, tab_trace = st.tabs([
        "💬 Student support", "👤 My profile", "📨 My requests", "🧠 Memory", "🔎 Trace & evaluation"])
else:
    st.info("Sign in with a demo student account to continue.")

# ---------------- Student chat ----------------
if role == "student":
    user = st.session_state.get("user")
    agent = st.session_state.get("agent")
    mode = current_mode
    if agent is None or agent.mode != mode:
        agent = st.session_state["agent"] = StudentSupportAgent(user, mode=mode)
    st.title("Student Support")
    mode_label = "🛡️ Assignment 2 (Safe Controls)" if mode == "a2" else "⚠️ Assignment 1 (Baseline)"
    st.caption(f"Signed in as **{user}** · Mode: **{mode_label}** · Model: **{agent.llm.name}**")
    for chat_role, text, _trace in st.session_state["chat"]:
        with st.chat_message(chat_role):
            st.write(_policy_wording(text) if chat_role == "assistant" else text)
    if q := st.chat_input("Ask about fees, scholarships, course changes, or college policies..."):
        st.session_state["chat"].append(("user", security.redact(q), None))
        answer, trace = agent.chat(q)
        st.session_state["chat"].append(("assistant", answer, trace))
        st.rerun()
    if agent.pending_elective_confirmation:
        target = agent.pending_elective_confirmation
        submit_col, cancel_col = st.columns(2)
        if submit_col.button(f"Submit request for {target}", type="primary", key="submit_elective_request"):
            answer, trace = agent.chat("submit")
            st.session_state["chat"].append(("assistant", answer, trace))
            st.rerun()
        if cancel_col.button("Cancel request", key="cancel_elective_request"):
            answer, trace = agent.chat("cancel")
            st.session_state["chat"].append(("assistant", answer, trace))
            st.rerun()

    with tab_profile:
        st.subheader("My profile")
        student = db.get_student(user)
        st.write(f"**Student ID:** {student['student_id']}  \n**Name:** {student['name']}  \n**Programme:** {student['programme']}  \n**Current elective:** {student['elective']}")
        options = academic.available_electives(student["programme"], student["elective"])
        st.caption("You can update your contact email here. To change electives, tell Student Support which listed course you want. The system checks the policy window and prior changes; the Programme Office makes the final decision.")
        st.write("**Other listed electives:** " + (", ".join(options) if options else "No alternatives are listed."))
        with st.form("student_contact_form"):
            email = st.text_input("Contact email", value=student["email"] or "")
            confirmed = st.checkbox("I confirm this is the contact email I want on my student record.")
            if st.form_submit_button("Save contact email"):
                if not confirmed:
                    st.error("Confirm the contact email before saving.")
                elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
                    st.error("Enter a valid email address.")
                elif email.strip() == student["email"]:
                    st.info("Your contact email is unchanged.")
                else:
                    db.update_student(user, {"email": email.strip()}, actor=user, source="self_service")
                    security.audit(user, "update_contact_email", user, {"field": "email"},
                                   "ALLOWED", "contact email updated")
                    st.success("Contact email updated. The Programme Office can see the change in the record history.")
                    st.rerun()

    with tab_requests:
        st.subheader("My change requests")
        requests = db.query(
            "SELECT approval_id, action, params, status, result, created_at, decided_at "
            "FROM approvals WHERE student_id=? ORDER BY approval_id DESC", (user,))
        if not requests:
            st.info("No requests yet. In Student Support, choose an offered elective, review the eligibility result, then use the Submit request button.")
        st.caption("To review a pending request, sign out and use Programme Office demo sign-in. The request appears in Approvals and stays pending until staff decide.")
        for request in requests:
            params = json.loads(request["params"])
            with st.container(border=True):
                st.markdown(f"**Request #{request['approval_id']} · {request['status']}**")
                st.write(f"{params.get('current_elective', 'Current elective')} → {params.get('new_elective', request['action'])}")
                st.caption(f"Submitted {request['created_at']}")
                if request["result"]:
                    st.write(request["result"])

    with tab_trace:
        st.subheader("Agent traces")
        traces = [(text, trace) for chat_role, text, trace in st.session_state["chat"]
                  if chat_role == "assistant" and trace]
        if not traces:
            st.info("Traces for your questions will appear here.")
        for index, (answer, trace) in enumerate(reversed(traces), start=1):
            with st.expander(f"Trace {index}: {answer[:80]}"):
                st.json(_policy_wording(trace))
        st.caption("Traces show tool calls, retrieved policy evidence, latency, and token usage.")

    with tab_memory:
        st.subheader("Your saved memories")
        memories = db.query("SELECT key, value, created_at FROM memories WHERE student_id=? ORDER BY created_at DESC", (user,))
        if not memories:
            st.info("No saved memories yet. Tell the agent a useful preference or long-term goal to remember.")
        for item in memories:
            col1, col2 = st.columns([5, 1])
            col1.write(f"**{item['key']}** · {item['value']}  \nSaved {item['created_at']}")
            if col2.button("Delete", key=f"delete_memory_{item['key']}"):
                db.delete_memory(user, item["key"])
                st.rerun()

# ---------------- Human approver ----------------
if role == "staff":
    with tab_admin:
        st.subheader("Pending elective-change requests")
        st.caption("This queue shows student requests submitted from Student Support. Refresh this page if it was already open when a student submitted.")
        if st.button("Refresh approval queue", key="refresh_approval_queue"):
            st.rerun()
        pending = security.pending_approvals()
        if not pending:
            st.write("No pending requests.")
        for p in pending:
            params = json.loads(p["params"])
            student = db.get_student(p["student_id"])
            with st.container(border=True):
                st.markdown(f"**Request #{p['approval_id']} · {student['name']} ({p['student_id']})**")
                st.write(f"Current elective: **{params.get('current_elective', student['elective'])}**  ")
                st.write(f"Requested elective: **{params['new_elective']}**")
                st.caption(f"Submitted {p['created_at']} · {params.get('eligibility', 'Review policy before deciding.')}")
                staff_check = academic.eligibility_summary(p["student_id"], params["new_elective"],
                                                          p["created_at"], p["approval_id"])
                if not staff_check["eligible"]:
                    st.error(f"Eligibility recheck failed: {staff_check['message']}")
                with st.expander("Decision checklist", expanded=True):
                    seat_available = st.checkbox("Seat availability confirmed", key=f"seat_{p['approval_id']}")
                    prerequisites_met = st.checkbox("Prerequisites and academic requirements checked", key=f"prereq_{p['approval_id']}")
                    decision_note = st.text_input("Decision note (required)", key=f"decision_note_{p['approval_id']}")
                approve_col, reject_col = st.columns(2)
                if approve_col.button("Approve request", key=f"a{p['approval_id']}", type="primary"):
                    if not staff_check["eligible"]:
                        st.error("This request no longer meets the configured eligibility rules.")
                    elif not seat_available or not prerequisites_met or not decision_note.strip():
                        st.error("Confirm seat availability and prerequisites, and add a decision note before approval.")
                    else:
                        result = security.decide_approval(p["approval_id"], True,
                                                         approver=st.session_state["user"], reason=decision_note,
                                                         seat_available=seat_available,
                                                         prerequisites_met=prerequisites_met)
                        st.success(result)
                        st.rerun()
                if reject_col.button("Reject request", key=f"r{p['approval_id']}"):
                    if not decision_note.strip():
                        st.error("Add a short reason so the student understands the decision.")
                    else:
                        result = security.decide_approval(p["approval_id"], False,
                                                         approver=st.session_state["user"], reason=decision_note)
                        st.warning(result)
                        st.rerun()
        with st.expander("Request history"):
            st.dataframe(pd.DataFrame(db.query("SELECT * FROM approvals ORDER BY approval_id DESC")),
                         use_container_width=True, hide_index=True)

    with tab_audit:
        st.subheader("Audit log")
        st.dataframe(pd.DataFrame(db.query("SELECT * FROM audit_log ORDER BY id DESC")), use_container_width=True)

    with tab_records:
        st.subheader("Student records")
        records = db.query(
            "SELECT student_id, name, programme, email, elective, fee_total, fee_paid, "
            "fee_due_date, scholarship, cgpa FROM students ORDER BY student_id")
        st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
        record_students = db.list_students()
        selected_id = st.selectbox("Select a student to edit", [s["student_id"] for s in record_students],
                                   format_func=lambda x: f"{x} · {next(s['name'] for s in record_students if s['student_id'] == x)}")
        selected = db.get_student(selected_id)
        st.subheader("Edit student record")
        with st.form("edit_student_record"):
            name = st.text_input("Name", value=selected["name"])
            programme = st.text_input("Programme", value=selected["programme"])
            email = st.text_input("Contact email", value=selected["email"])
            elective = st.text_input("Elective", value=selected["elective"])
            elective_override_reason = st.text_input(
                "Reason for manual elective change (required only if changed)", key="manual_elective_reason")
            fee_total = st.number_input("Total fees (INR)", min_value=0, value=int(selected["fee_total"]))
            fee_paid = st.number_input("Fees paid (INR)", min_value=0, value=int(selected["fee_paid"]))
            fee_due_date = st.text_input("Fee due date (YYYY-MM-DD)", value=selected["fee_due_date"])
            scholarship = st.text_input("Scholarship status", value=selected["scholarship"] or "None")
            cgpa = st.number_input("CGPA", min_value=0.0, max_value=4.0, value=float(selected["cgpa"]), step=0.1)
            if st.form_submit_button("Save student record"):
                updates = {"name": name.strip(), "programme": programme.strip(), "email": email.strip(),
                           "elective": elective.strip(), "fee_total": int(fee_total), "fee_paid": int(fee_paid),
                           "fee_due_date": fee_due_date.strip(), "scholarship": scholarship.strip(), "cgpa": float(cgpa)}
                if not all((updates["name"], updates["programme"], updates["elective"], updates["fee_due_date"])):
                    st.error("Name, programme, elective, and due date are required.")
                elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", updates["email"]):
                    st.error("Enter a valid email address.")
                elif not _valid_date(updates["fee_due_date"]):
                    st.error("Enter the due date in YYYY-MM-DD format.")
                elif updates["fee_paid"] > updates["fee_total"]:
                    st.error("Fees paid cannot exceed total fees.")
                else:
                    changed = {key: value for key, value in updates.items() if selected[key] != value}
                    if "elective" in changed and not academic.match_elective(updates["programme"], updates["elective"]):
                        st.error("Choose an elective listed for the updated programme.")
                        changed = None
                    elif "elective" in changed:
                        if not elective_override_reason.strip():
                            st.error("Add the Programme Office reason for this manual elective override.")
                            changed = None
                    if changed:
                        db.update_student(selected_id, changed, actor="programme_office", source="manual_staff_edit")
                        security.audit("programme_office", "manual_student_update", selected_id,
                                       {"fields": sorted(changed),
                                        "elective_override_reason": elective_override_reason.strip()
                                        if "elective" in changed else None},
                                       "MANUAL_OVERRIDE" if "elective" in changed else "ALLOWED",
                                       "student record updated")
                        st.success("Record updated and change history saved.")
                        st.rerun()
                    elif changed == {}:
                        st.info("No changes to save.")

        st.subheader("Add student")
        st.caption("New demo student accounts use the locally configured student demo password.")
        with st.form("add_student_record"):
            new_id = st.text_input("Student ID", placeholder="S1004").strip().upper()
            new_name = st.text_input("Name", key="new_student_name")
            new_programme = st.text_input("Programme", key="new_student_programme")
            new_email = st.text_input("Contact email", key="new_student_email")
            new_elective = st.text_input("Elective", key="new_student_elective")
            new_total = st.number_input("Total fees (INR)", min_value=0, value=0, key="new_student_fee_total")
            new_paid = st.number_input("Fees paid (INR)", min_value=0, value=0, key="new_student_fee_paid")
            new_due_date = st.text_input("Fee due date (YYYY-MM-DD)", key="new_student_due_date")
            new_scholarship = st.text_input("Scholarship status", value="None", key="new_student_scholarship")
            new_cgpa = st.number_input("CGPA", min_value=0.0, max_value=4.0, value=0.0, step=0.1, key="new_student_cgpa")
            if st.form_submit_button("Add student"):
                new_record = {"student_id": new_id, "name": new_name.strip(), "programme": new_programme.strip(),
                              "email": new_email.strip(), "elective": new_elective.strip(), "fee_total": int(new_total),
                              "fee_paid": int(new_paid), "fee_due_date": new_due_date.strip(),
                              "scholarship": new_scholarship.strip(), "cgpa": float(new_cgpa)}
                if not re.fullmatch(r"S\d{4,}", new_id):
                    st.error("Use a student ID such as S1004.")
                elif db.get_student(new_id):
                    st.error("That student ID already exists.")
                elif not all((new_record["name"], new_record["programme"], new_record["elective"], new_record["fee_due_date"])):
                    st.error("Name, programme, elective, and due date are required.")
                elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", new_record["email"]):
                    st.error("Enter a valid email address.")
                elif not _valid_date(new_record["fee_due_date"]):
                    st.error("Enter the due date in YYYY-MM-DD format.")
                elif new_record["fee_paid"] > new_record["fee_total"]:
                    st.error("Fees paid cannot exceed total fees.")
                else:
                    db.add_student(new_record, actor="programme_office")
                    security.audit("programme_office", "add_student", new_id,
                                   {"fields": sorted(new_record.keys())}, "ALLOWED", "student record created")
                    st.success("Student added and creation recorded in change history.")
                    st.rerun()

        st.subheader("Student change history")
        changes = db.query("SELECT * FROM student_change_log ORDER BY id DESC")
        st.dataframe(pd.DataFrame(changes), use_container_width=True, hide_index=True)
    with tab_evaluation:
        st.subheader("Assignment evaluation")
        st.write("Run the scripted scenarios to compare the Assignment 1 baseline with Assignment 2 safety controls.")
        if st.button("Run evaluation", type="primary"):
            with st.spinner("Running evaluation scenarios..."):
                try:
                    subprocess.run(
                        [sys.executable, f"{config.BASE_DIR}/evaluate.py"],
                        cwd=config.BASE_DIR,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    st.session_state["evaluation_completed"] = True
                except subprocess.CalledProcessError as error:
                    st.error(f"Evaluation failed: {error.stderr or error.stdout}")
        if st.session_state.get("evaluation_completed"):
            with open(f"{config.BASE_DIR}/results/evaluation_report.md", encoding="utf-8") as report_file:
                st.markdown(report_file.read())
            st.caption("This report was generated by executing the scenarios in `evaluate.py`. Token counts and latency depend on the selected model.")
        else:
            st.info("Choose Run evaluation to execute the scenarios and generate the comparison report.")
