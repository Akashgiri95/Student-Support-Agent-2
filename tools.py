"""Tools the agent can call. Authorization is enforced HERE (in code), not by the prompt."""
import db, memory, security
import academic
from retriever import PolicyRetriever

RETRIEVER = PolicyRetriever()

# JSON schemas sent to the LLM (OpenAI function-calling format)
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_fee_status", "description": "Get fee paid, balance and due date for a student.",
        "parameters": {"type": "object", "properties": {
            "student_id": {"type": ["string", "null"], "description": "Student ID. Omit or null for the signed-in student."}}}}},
    {"type": "function", "function": {
        "name": "get_scholarship_status", "description": "Get the scholarship status of a student.",
        "parameters": {"type": "object", "properties": {
            "student_id": {"type": ["string", "null"], "description": "Student ID. Omit or null for the signed-in student."}}}}},
    {"type": "function", "function": {
        "name": "search_policy", "description": "Search student policies for rules and guidance.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "create_support_ticket", "description": "Create a support request (IT, fees, academics, hostel).",
        "parameters": {"type": "object", "properties": {
            "category": {"type": "string"}, "description": {"type": "string"}},
            "required": ["category", "description"]}}},
    {"type": "function", "function": {
        "name": "request_elective_change", "description": "Change the elective course of the signed-in student.",
        "parameters": {"type": "object", "properties": {"new_elective": {"type": "string"}},
                       "required": ["new_elective"]}}},
    {"type": "function", "function": {
        "name": "save_memory", "description": "Remember a useful long-term fact or preference about the student "
                                              "(e.g. preferred language, career goal). Never for OTPs or passwords.",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                       "required": ["key", "value"]}}},
]


    # Whitelist of valid tool names — prevents getattr from dispatching to
    # Python internals (__init__, __class__, etc.) if the LLM hallucinates.
ALLOWED_TOOLS = {s["function"]["name"] for s in TOOL_SCHEMAS}


class ToolBox:
    def __init__(self, session_student_id, mode="a2"):
        self.sid = session_student_id   # identity comes from login, NOT from the chat text
        self.mode = mode

    # ---- helper: permission check + audit for private data ----
    def _check(self, action, target):
        target = target or self.sid
        if self.mode == "a1":           # Assignment-1 baseline: no authorization layer
            return True, target, "no-check (a1)"
        allowed, reason = security.authorize(self.sid, target, action)
        return allowed, target, reason

    def run(self, name, args):
        if name not in ALLOWED_TOOLS:
            return {"error": f"Unknown tool '{name}'"}
        fn = getattr(self, name, None)
        if fn is None:
            return {"error": f"Tool '{name}' is not implemented."}
        try:
            return fn(**args)
        except Exception as e:          # tool errors are returned to the agent, not crashed
            return {"error": f"Tool failed: {e}"}

    # ---------------- private-data tools ----------------
    def get_fee_status(self, student_id=None):
        allowed, target, reason = self._check("get_fee_status", student_id)
        if not allowed:
            security.audit(self.sid, "get_fee_status", target, {}, "DENIED", reason)
            return {"error": reason}
        s = db.get_student(target)
        if not s:
            return {"error": f"No record found for student {target}."}
        result = {"student_id": target, "fee_total": s["fee_total"], "fee_paid": s["fee_paid"],
                  "balance": s["fee_total"] - s["fee_paid"], "due_date": s["fee_due_date"]}
        if self.mode == "a2":
            security.audit(self.sid, "get_fee_status", target, {}, "ALLOWED", "returned fee status")
        return result

    def get_scholarship_status(self, student_id=None):
        allowed, target, reason = self._check("get_scholarship_status", student_id)
        if not allowed:
            security.audit(self.sid, "get_scholarship_status", target, {}, "DENIED", reason)
            return {"error": reason}
        s = db.get_student(target)
        if not s:
            return {"error": f"No record found for student {target}."}
        if self.mode == "a2":
            security.audit(self.sid, "get_scholarship_status", target, {}, "ALLOWED", "returned scholarship")
        return {"student_id": target, "scholarship": s["scholarship"], "cgpa": s["cgpa"]}

    # ---------------- public / write tools ----------------
    def search_policy(self, query):
        chunks = RETRIEVER.search(query)
        if not chunks:
            return {"chunks": [], "note": "No relevant policy information found."}
        return {"chunks": chunks}

    def create_support_ticket(self, category, description):
        description = security.redact(description)
        tid = db.execute("INSERT INTO tickets(student_id, category, description) VALUES (?,?,?)",
                         (self.sid, category, description))
        if self.mode == "a2":
            security.audit(self.sid, "create_support_ticket", self.sid,
                           {"category": category}, "ALLOWED", f"ticket {tid} created")
        return {"ticket_id": tid, "status": "OPEN", "category": category}

    def request_elective_change(self, new_elective, student_id=None):
        allowed, target, reason = self._check("request_elective_change", student_id)
        if not allowed:
            security.audit(self.sid, "request_elective_change", target,
                           {"new_elective": new_elective}, "DENIED", reason)
            return {"error": reason}
        try:
            new_elective = academic.validate_elective_change(target, new_elective)
        except ValueError as exc:
            return {"error": str(exc)}
        params = {"current_elective": db.get_student(target)["elective"],
                  "new_elective": new_elective,
                  "eligibility": "Meets listed course, term-window, and one-change checks; final seat and prerequisite check required."}
        if self.mode == "a1":           # baseline executes immediately (unsafe)
            return {"status": "EXECUTED",
                    "result": security.execute_sensitive_action(target, "change_elective", params,
                                                                  actor=self.sid, source="a1_baseline")}
        # Assignment 2: sensitive action -> wait for human approval, do NOT execute now
        aid = security.create_approval(target, "change_elective", params)
        security.audit(self.sid, "request_elective_change", target, params,
                       "PENDING_APPROVAL", f"approval #{aid} created, not executed")
        return {"status": "PENDING_APPROVAL", "approval_id": aid,
                "message": "Sent to Programme Office for approval. Nothing has changed yet."}

    def save_memory(self, key, value):
        result = memory.save_memory(self.sid, key, value)
        if self.mode == "a2":
            security.audit(self.sid, "save_memory", self.sid, {"key": key},
                           "SAVED" if result["saved"] else "REFUSED", result.get("reason", "ok"))
        return result
