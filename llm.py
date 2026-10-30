"""LLM wrapper. Real mode: any OpenAI-compatible API. Mock mode: simple rule-based model
(used when no API key is set, so the project and evaluation can run offline)."""
import json, re, time
import config, security

# Every LLM reply is normalised to: {"content": str|None, "tool_calls": [...], "usage": {"in": int, "out": int}}


class OpenAILLM:
    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        self.name = config.LLM_MODEL

    def chat(self, messages, tools, max_retries=3):
        delay = 1.0
        last_exc = None
        for attempt in range(max_retries):
            try:
                r = self.client.chat.completions.create(
                    model=config.LLM_MODEL,
                    messages=messages,
                    tools=tools,
                    temperature=0,
                    timeout=30.0,
                )
                msg = r.choices[0].message
                calls = [{"id": c.id, "name": c.function.name, "args": json.loads(c.function.arguments or "{}")}
                         for c in (msg.tool_calls or [])]
                usage = {"in": r.usage.prompt_tokens, "out": r.usage.completion_tokens} if r.usage else {"in": 0, "out": 0}
                return {"content": msg.content, "tool_calls": calls, "usage": usage}
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2.0
        raise RuntimeError(f"LLM call failed after {max_retries} attempts: {last_exc}")


class MockLLM:
    """Keyword-based stand-in for an LLM. Token counts are estimated (chars / 4)."""
    name = "mock-rule-based"

    def chat(self, messages, tools):
        last = messages[-1]
        if last["role"] == "tool":
            reply = self._answer_from_tools(messages)
        else:
            reply = self._decide(last["content"], messages[0]["content"])
        n_in = sum(len(str(m.get("content", ""))) for m in messages) // 4 + len(json.dumps(tools)) // 4
        n_out = len(json.dumps(reply)) // 4
        reply["usage"] = {"in": n_in, "out": n_out}
        return reply

    @staticmethod
    def _call(name, **args):
        return {"content": None, "tool_calls": [{"id": f"call_{name}", "name": name, "args": args}]}

    def _decide(self, q, system_prompt):
        ql = q.lower()
        other = re.search(r"\bS\d{4}\b", q, re.I)
        sid = {"student_id": other.group(0).upper()} if other else {}
        if security.contains_sensitive(q):
            return {"content": "Please never share an OTP or password with anyone, including support. "
                               "I have not stored it. If it is a login problem, I can raise an IT ticket.",
                    "tool_calls": []}
        if re.search(r"(do you remember|what .*(i told|did i say)|what is my (preferred|career|programme))", ql):
            facts = [f for f in re.findall(r"- (.+)", system_prompt.split("Remembered facts:")[-1]) if f != "none"]
            ans = "From earlier sessions I remember: " + "; ".join(facts) if facts else \
                  "I don't have any remembered information about that yet."
            return {"content": ans, "tool_calls": []}
        if re.search(r"(remember|i prefer|my preferred|my career goal)", ql):
            m = re.search(r"(?:prefer|preferred \w+ is|goal is|remember that)\s+(.+)", q, re.I)
            value = (m.group(1) if m else q).strip(" .")
            key = "preference" if "prefer" in ql else "career_goal" if "goal" in ql else "note"
            return self._call("save_memory", key=key, value=value)
        if re.search(r"(change|switch).*(elective|course)", ql):
            m = re.search(r"\bto\s+(.+)", q, re.I)
            new = (m.group(1) if m else "Unknown").strip(" .?")
            return self._call("request_elective_change", new_elective=new, **sid)
        if "scholarship" in ql and re.search(r"(my|status|\bs\d{4})", ql) and "eligib" not in ql:
            return self._call("get_scholarship_status", **sid)
        if re.search(r"(fee status|fees? (due|balance|pending)|my fees?|balance|how much .*fee)", ql) or (sid and "fee" in ql):
            return self._call("get_fee_status", **sid)
        if re.search(r"(raise|create|open|log).*(ticket|request|complaint)", ql):
            cat = "IT" if re.search(r"(wifi|login|portal|laptop|it )", ql) else "General"
            return self._call("create_support_ticket", category=cat, description=q)
        if re.fullmatch(r"\W*(hi|hello|hey|thanks|thank you)\W*", ql):
            return {"content": "Hello! I can help with fees, scholarships, course changes, policies and "
                               "support requests.", "tool_calls": []}
        return self._call("search_policy", query=q)

    def _answer_from_tools(self, messages):
        parts = []
        for m in messages[::-1]:
            if m["role"] != "tool":
                break
            r = json.loads(m["content"])
            if "error" in r:
                parts.append(f"Sorry, I can't do that. {r['error']}")
            elif "chunks" in r:
                if not r["chunks"]:
                    parts.append("The available policy information does not contain enough detail to answer this. "
                                 "I can raise a support request so the office can help you.")
                else:
                    c = r["chunks"][0]
                    parts.append(f"According to policy ({c['title']}): {c['text']}")
            elif "balance" in r:
                parts.append(f"Fee status for {r['student_id']}: paid INR {r['fee_paid']:,} of "
                             f"INR {r['fee_total']:,}. Balance INR {r['balance']:,}, due {r['due_date']}.")
            elif "scholarship" in r:
                parts.append(f"Scholarship status for {r['student_id']}: {r['scholarship']}.")
            elif "ticket_id" in r:
                parts.append(f"Support request #{r['ticket_id']} created ({r['category']}). "
                             f"You will get a reply within 2 working days.")
            elif r.get("status") == "PENDING_APPROVAL":
                parts.append(f"Your elective change request #{r['approval_id']} is waiting for "
                             f"Programme Office approval. Nothing has been changed yet.")
            elif r.get("status") == "EXECUTED":
                parts.append(r["result"])
            elif "saved" in r:
                parts.append(f"Noted - I'll remember your {r['key']}: {r['value']}." if r["saved"]
                             else f"I did not store that. {r['reason']}")
        return {"content": " ".join(parts[::-1]), "tool_calls": []}


def get_llm():
    use_mock = config.USE_MOCK == "1" or (config.USE_MOCK == "auto" and not config.LLM_API_KEY)
    return MockLLM() if use_mock else OpenAILLM()
