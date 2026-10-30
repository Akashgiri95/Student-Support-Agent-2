"""The agent loop: build minimal context -> LLM decides -> run tools -> LLM answers. Every run is traced."""
import json, os, time
import re
import config, db, memory, security
import academic
from llm import get_llm
from tools import ToolBox, TOOL_SCHEMAS

SYSTEM_PROMPT = """You are the Student Support Agent of a PGDM college.
Rules:
- For fees or scholarship of the student, call the matching tool. Never guess numbers.
- For rules/policies, call search_policy and answer ONLY from the returned chunks.
  If no chunk is returned, say the available policy information is insufficient and offer a support ticket.
- Refer to retrieved rules as policy; phrase citations as "According to policy (section title)".
- For elective-change requests, never submit a request until the student specifies a target elective, sees the eligibility result, and confirms submission.
- Never claim an elective change is eligible without the eligibility tool checks. The Programme Office makes the final decision.
- Use save_memory only for useful long-term facts (preferences, goals). Never store OTPs, passwords
  or temporary information.
- Never ask for an OTP or password. Keep answers short.

Signed-in student: {sid} | {name} | {programme}

Remembered facts:
{memories}
"""


class StudentSupportAgent:
    def __init__(self, student_id, mode=config.AGENT_MODE, llm=None):
        self.student = db.get_student(student_id)
        self.sid = student_id
        self.mode = mode
        self.llm = llm or get_llm()
        self.tools = ToolBox(student_id, mode)
        self.history = []   # short-term (session) memory only
        self.pending_elective_request = False
        self.pending_elective_confirmation = None

    def _context(self, user_msg):
        """Context selection: system prompt + identity + memories + last few turns + the question.
        Policy text is NOT included here - it is fetched only when needed via search_policy."""
        mems = memory.load_memories(self.sid)
        mem_text = "\n".join(f"- {m['key']}: {m['value']}" for m in mems) or "- none"
        system = SYSTEM_PROMPT.format(sid=self.sid, name=self.student["name"],
                                      programme=self.student["programme"], memories=mem_text)
        recent = self.history[-config.MAX_HISTORY_TURNS * 2:]
        return [{"role": "system", "content": system}, *recent, {"role": "user", "content": user_msg}], mems

    def chat(self, user_msg):
        start = time.time()
        self.student = db.get_student(self.sid)
        messages, mems = self._context(user_msg)
        trace = {"student": self.sid, "mode": self.mode, "model": self.llm.name,
                 "question": security.redact(user_msg), "memories_in_context": mems,
                 "steps": [], "retrieved_chunks": [], "tokens_in": 0, "tokens_out": 0}
        if self.pending_elective_request:
            text = user_msg.strip()
            if re.fullmatch(r"cancel|never mind|nevermind", text, re.I):
                self.pending_elective_request = False
                return self._finish(user_msg, "Okay, I cancelled the elective-change request. No request was submitted.",
                                    trace, start)
            if self._is_elective_eligibility_question(text):
                result = self.tools.search_policy(user_msg)
                chunks = result.get("chunks", [])
                trace["steps"].append({"tool": "search_policy", "args": {"query": security.redact(user_msg)},
                                       "outcome": f"{len(chunks)} chunk(s)"})
                trace["retrieved_chunks"] += [f"{c['chunk_id']} {c['title']} ({c['score']})" for c in chunks]
                if chunks:
                    answer = (f"According to policy ({chunks[0]['title']}): {chunks[0]['text']} "
                              "Choose an offered elective when you're ready, or say ‘cancel’.")
                else:
                    answer = "I couldn't confirm the rules from the available policy information. Please contact the Programme Office. You can still say ‘cancel’ to stop this request."
                return self._finish(user_msg, answer, trace, start)
            target = academic.match_elective(self.student["programme"], text)
            if target:
                self.pending_elective_request = False
                summary = academic.eligibility_summary(self.sid, target)
                _trace_eligibility(trace, target, summary)
                if not summary["eligible"]:
                    return self._finish(user_msg, summary["message"], trace, start)
                self.pending_elective_confirmation = target
                checks = "; ".join(summary["checks"])
                return self._finish(user_msg, f"Eligibility check passed: {checks}. {summary['message']} Reply ‘submit’ to send this request to Programme Office, or ‘cancel’. No request has been submitted yet.", trace, start)
            if re.search(r"\b(fee|fees|scholarship|policy|attendance|ticket|hello|hi|thanks|thank you)\b", text, re.I):
                self.pending_elective_request = False
            else:
                options = academic.available_electives(self.student["programme"], self.student["elective"])
                if options:
                    answer = "I couldn't match that to an elective offered for your programme. Choose one of: " + ", ".join(options) + "."
                else:
                    answer = "The Programme Office hasn't configured elective offerings for your programme yet, so I can't submit a change request."
                return self._finish(user_msg, answer, trace, start)

        if self.pending_elective_confirmation:
            text = user_msg.strip()
            target = self.pending_elective_confirmation
            if self._is_elective_eligibility_question(text):
                summary = academic.eligibility_summary(self.sid, target)
                _trace_eligibility(trace, target, summary)
                return self._finish(user_msg, f"For {target}: {summary['message']} Reply ‘submit’ to continue or ‘cancel’.", trace, start)
            if re.fullmatch(r"submit|confirm|yes", text, re.I):
                self.pending_elective_confirmation = None
                return self._submit_elective_change(user_msg, target, trace, start)
            self.pending_elective_confirmation = None
            if re.fullmatch(r"cancel|no|never mind|nevermind", text, re.I):
                return self._finish(user_msg, "Okay, no request was submitted.", trace, start)

        if self._is_elective_eligibility_question(user_msg):
            result = self.tools.search_policy(user_msg)
            chunks = result.get("chunks", [])
            trace["steps"].append({"tool": "search_policy", "args": {"query": security.redact(user_msg)},
                                   "outcome": f"{len(chunks)} chunk(s)"})
            trace["retrieved_chunks"] += [f"{c['chunk_id']} {c['title']} ({c['score']})" for c in chunks]
            if chunks:
                answer = (f"According to policy ({chunks[0]['title']}): {chunks[0]['text']} "
                          "The Programme Office makes the final decision. Tell me which offered elective you want, and I can check the request conditions.")
            else:
                answer = "I couldn't find enough policy information to confirm the elective-change rules. Please contact the Programme Office before requesting a change."
            return self._finish(user_msg, answer, trace, start)

        if self._is_elective_change_request(user_msg):
            target = academic.match_elective(self.student["programme"], user_msg)
            if target:
                summary = academic.eligibility_summary(self.sid, target)
                _trace_eligibility(trace, target, summary)
                if not summary["eligible"]:
                    return self._finish(user_msg, summary["message"], trace, start)
                self.pending_elective_confirmation = target
                checks = "; ".join(summary["checks"])
                answer = (f"Eligibility check passed: {checks}. {summary['message']} "
                          "Reply ‘submit’ to send this request to Programme Office, or ‘cancel’. No request has been submitted yet.")
                return self._finish(user_msg, answer, trace, start)
            self.pending_elective_request = True
            options = academic.available_electives(self.student["programme"], self.student["elective"])
            if not options:
                self.pending_elective_request = False
                return self._finish(user_msg, "The Programme Office hasn't configured elective offerings for your programme yet, so I can't submit a change request.", trace, start)
            answer = (f"Which elective would you like to switch to? For your programme, the listed options are: "
                      f"{', '.join(options)}. I’ll check the request window and prior changes before submitting. "
                      "Reply with an option or say ‘cancel’.")
            return self._finish(user_msg, answer, trace, start)

        answer = "Sorry, I could not complete this request."
        for _ in range(config.MAX_AGENT_STEPS):
            reply = self.llm.chat(messages, TOOL_SCHEMAS)
            trace["tokens_in"] += reply["usage"]["in"]
            trace["tokens_out"] += reply["usage"]["out"]
            if not reply["tool_calls"]:                      # model answered directly -> done
                answer = reply["content"]
                break
            messages.append({"role": "assistant", "content": reply["content"],
                             "tool_calls": [{"id": c["id"], "type": "function",
                                             "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                            for c in reply["tool_calls"]]})
            for call in reply["tool_calls"]:
                result = self.tools.run(call["name"], call["args"])
                safe_args = json.loads(security.redact(json.dumps(call["args"])))
                trace["steps"].append({"tool": call["name"], "args": safe_args,
                                       "outcome": _outcome(result)})
                if call["name"] == "search_policy":
                    trace["retrieved_chunks"] += [f"{c['chunk_id']} {c['title']} ({c['score']})"
                                                  for c in result.get("chunks", [])]
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})

        return self._finish(user_msg, answer, trace, start)

    def _submit_elective_change(self, user_msg, target, trace, start):
        result = self.tools.run("request_elective_change", {"new_elective": target})
        trace["steps"].append({"tool": "request_elective_change",
                               "args": {"new_elective": target}, "outcome": _outcome(result)})
        if result.get("status") == "PENDING_APPROVAL":
            answer = (f"I submitted request #{result['approval_id']} to change your elective to {target}. "
                      "Your current elective has not changed. The Programme Office will check the request and confirm availability and prerequisites.")
        elif result.get("status") == "EXECUTED":
            answer = result["result"]
        else:
            answer = result.get("error", "I couldn't submit that elective-change request.")
        return self._finish(user_msg, answer, trace, start)

    def _finish(self, user_msg, answer, trace, start):
        self.history += [{"role": "user", "content": security.redact(user_msg)},
                         {"role": "assistant", "content": answer}]
        trace["answer"] = answer
        trace["latency_s"] = round(time.time() - start, 3)
        os.makedirs(os.path.dirname(config.TRACE_PATH), exist_ok=True)
        with open(config.TRACE_PATH, "a") as f:
            f.write(json.dumps(trace) + "\n")
        return answer, trace

    @staticmethod
    def _is_elective_eligibility_question(text):
        return bool(re.search(r"\b(can i|am i eligible|am i allowed|what are the rules|what is the policy|how do i|what requirements|eligible)\b", text, re.I)
                    and re.search(r"\b(elective|course|change|switch)\b", text, re.I))

    @classmethod
    def _is_elective_change_request(cls, text):
        return bool(re.search(r"\b(change|switch|replace)\b", text, re.I)
                    and re.search(r"\b(elective|course)\b", text, re.I)
                    and not cls._is_elective_eligibility_question(text))


def _outcome(result):
    if "error" in result:
        return "DENIED/ERROR: " + result["error"]
    if "status" in result:
        return result["status"]
    if "saved" in result:
        return "SAVED" if result["saved"] else "REFUSED: " + result["reason"]
    if "chunks" in result:
        return f"{len(result['chunks'])} chunk(s)"
    return "OK"


def _trace_eligibility(trace, target, summary):
    trace["steps"].append({"tool": "check_elective_eligibility", "args": {"target": target},
                           "outcome": "ELIGIBLE" if summary["eligible"] else "INELIGIBLE",
                           "checks": summary["checks"], "message": summary["message"]})
