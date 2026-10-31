"""Assignment 2 controls: sensitive-data filter, authorization, approval and audit.
All checks run in normal Python code - the LLM cannot skip them."""
import json, re
import db
import academic

# ---------- 1. Sensitive data detection / redaction ----------
SENSITIVE_PATTERNS = [
    (r"\b(otp|one[- ]time password)\b\D{0,15}\d{4,8}", "[REDACTED_OTP]"),
    (r"\b(password|passwd|pwd|pin|cvv)\b\s*(is|:|=)?\s*\S+", "[REDACTED_SECRET]"),
    (r"\b(?:\d[ -]?){13,16}\b", "[REDACTED_CARD]"),          # card numbers
    (r"\b\d{4}\s?\d{4}\s?\d{4}\b", "[REDACTED_ID]"),          # aadhaar-like numbers
]


def contains_sensitive(text: str) -> bool:
    return any(re.search(p, text or "", re.I) for p, _ in SENSITIVE_PATTERNS)


def redact(text: str) -> str:
    for p, repl in SENSITIVE_PATTERNS:
        text = re.sub(p, repl, text or "", flags=re.I)
    return text


# ---------- 2. Authorization ----------
def authorize(session_student_id: str, target_student_id: str, action: str):
    """Return (allowed, reason). A student can only touch their OWN record."""
    if not session_student_id:
        return False, "No student is signed in."
    if target_student_id and target_student_id != session_student_id:
        return False, f"Access denied: {session_student_id} cannot access {target_student_id}'s private data."
    return True, "Owner access"


# ---------- 3. Audit log ----------
def audit(actor, action, target, params, decision, result):
    db.execute("INSERT INTO audit_log(actor, action, target, params, decision, result) VALUES (?,?,?,?,?,?)",
               (actor, action, target, redact(json.dumps(params)), decision, redact(str(result))[:300]))


# ---------- 4. Human approval for sensitive actions ----------
SENSITIVE_ACTIONS = {"change_elective", "update_email"}


def create_approval(student_id, action, params):
    return db.execute("INSERT INTO approvals(student_id, action, params) VALUES (?,?,?)",
                      (student_id, action, json.dumps(params)))


def pending_approvals():
    return db.query("SELECT * FROM approvals WHERE status='PENDING' ORDER BY approval_id")


def decide_approval(approval_id: int, approve: bool, approver, reason="",
                    seat_available=False, prerequisites_met=False):
    """Called by the human approver (admin panel). Only APPROVED requests are executed."""
    if approver != "programme_office":
        return "Only Programme Office can decide change requests."
    if not reason.strip():
        return "A decision note is required."
    rows = db.query("SELECT * FROM approvals WHERE approval_id=?", (approval_id,))
    if not rows or rows[0]["status"] != "PENDING":
        return "Approval not found or already decided."
    req = rows[0]
    if approve and req["action"] == "change_elective" and (not seat_available or not prerequisites_met):
        return "Confirm seat availability and prerequisite review before approval."
    params = json.loads(req["params"])
    if approve:
        try:
            result = execute_sensitive_action(req["student_id"], req["action"], params,
                                              actor=approver, source="approval", approval_id=approval_id,
                                              request_created_at=req["created_at"])
            if reason.strip():
                result += f" Decision note: {reason.strip()}"
            status = "APPROVED"
        except ValueError as exc:
            result = f"Not executed: {exc}"
            status = "BLOCKED"
    else:
        result = "Not executed - request rejected by approver."
        if reason.strip():
            result += f" Reason: {reason.strip()}"
        status = "REJECTED"
    db.execute("UPDATE approvals SET status=?, decided_by=?, result=?, decided_at=CURRENT_TIMESTAMP "
               "WHERE approval_id=?", (status, approver, result, approval_id))
    audit(approver, f"decide:{req['action']}", req["student_id"],
          {"approval_id": approval_id, **params}, status, result)
    return result


def execute_sensitive_action(student_id, action, params, actor="system", source="approval", approval_id=None,
                             request_created_at=None):
    if action == "change_elective":
        new_elective = academic.validate_elective_change(
            student_id, params["new_elective"], request_created_at, approval_id)
        db.update_student(student_id, {"elective": new_elective}, actor, source, approval_id)
        return f"Elective changed to {new_elective}."
    elif action == "update_email":
        new_email = params["new_email"]
        db.update_student(student_id, {"email": new_email}, actor, source, approval_id)
        return f"Contact email changed to {new_email}."
    return "Unknown action."
