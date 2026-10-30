# 🎓 Student Support Agent — Agentic AI Engineering (Assignment 1 + 2)

One chat agent that answers student queries (fees, scholarships, course change, policies, support tickets).
**Assignment 1** = agent loop, tools, memory, retrieval, tracing.
**Assignment 2** = the same agent + identity, authorization, human approval, audit log.
The student UI always uses safe Assignment 2 mode. `python evaluate.py` compares the Assignment 1 baseline and Assignment 2 controls.

## 1. How to run
```bash
pip install -r requirements.txt
cp .env.example .env.local
# Add your Groq key and private demo passwords to .env.local.
# Without a key, a rule-based mock model is used.
streamlit run app.py                    # chat UI
python evaluate.py                      # runs test set in a1 + a2 mode -> results/
```

The app loads `.env.local` automatically. This filename keeps configuration separate from a virtual environment named `.env`. Groq is the default provider (`llama-3.3-70b-versatile`); its
OpenAI-compatible client uses `https://api.groq.com/openai/v1`. Set `LLM_BASE_URL` and `LLM_MODEL`
to use another compatible provider.

Set `STUDENT_DEMO_PASSWORD` and `STAFF_DEMO_PASSWORD` in the ignored `.env.local` file to enable the
sample student and Programme Office sign-ins. These local demo credentials are not included in the repository.

## Student record changes

- Students can update only their own contact email from **My profile**. The app validates the format and records the previous and new values.
- Elective changes requested in chat use a two-step confirmation: the agent asks which offered elective, checks the rules, then asks the student to confirm before creating a pending approval. The Programme Office approves or rejects; approval updates the student record.
- The elective request checks the active term's 14-day window, the student's programme course list, duplicate pending requests, and the one-approved-change-per-term rule. Staff still confirm seats and prerequisites before approval.
- Programme Office staff can manually edit student records or add a student from **Student records**. Each field change and new record is written to `student_change_log` and the audit log.
- Students can check their own approval status in **My requests**. Programme Office approval requires recording seat availability, prerequisite review, and a decision note. Staff can also make a manual elective correction for a listed course, with a required reason recorded as an override in audit/change history.
- New sample students use the locally configured student demo password. Replace the demo authentication with institutional identity management before deployment.

`data/electives.json` contains the demo elective catalogue and term start date used by those checks. Replace both with verified institutional data before relying on eligibility results.

## 2. Architecture
```
 Student (signed in) ──► app.py (Streamlit chat)
                              │
                              ▼
                  agent.py  ── agent loop ──────────────► trace (logs/traces.jsonl)
      context = system rules + identity + memories + last 4 turns + question
                              │  LLM decides: answer directly OR call tool
                              ▼
                  tools.py  (authorization check in CODE)
   ┌──────────────┬─────────────────┬──────────────┬─────────────────┬─────────────┐
 get_fee_status  get_scholarship  search_policy  create_support   request_elective  save_memory
   (own data)     (own data)      (retriever.py)  _ticket          _change           (memory.py)
                                                                 │ a2: approval      │ sensitive
                                                                 ▼                   ▼ filter
                                         security.py: authorize · redact · approvals · audit_log
                                                                 │
                                                       db.py (SQLite: students, memories,
                                                              tickets, approvals, audit_log)
```

| File | Purpose |
|---|---|
| `agent.py` | Agent loop (max 5 steps), context selection, tracing |
| `tools.py` | 6 tools + JSON schemas; permission check before every private-data tool |
| `retriever.py` | Policy source split into sections, TF-IDF top-2 chunks, score threshold → "not enough info" |
| `memory.py` | Persistent memory across sessions; refuses OTP/password/temporary facts; students can review and delete their own saved memories in the UI |
| `security.py` | Sensitive-data redaction, `authorize()`, approval queue, audit log |
| `llm.py` | OpenAI-compatible client (Groq by default) + offline mock model |
| `evaluate.py` | Test set, a1 vs a2 comparison, audit/trace export; report is displayed in the UI |
| `data/` | `handbook.md` (policy document), `students.json` (sample records) |

## 3. Assignment 1 — concepts demonstrated
| Requirement | Where |
|---|---|
| Agent loop decides tool vs direct answer | `agent.py → chat()` |
| ≥ 2 useful tools | 6 tools in `tools.py` |
| Persistent memory, sensitive/temporary not stored | `memory.py`, table `memories` |
| Retrieval of only relevant policy chunks | `retriever.py` (top-2, threshold 0.20) |
| Context selection | `agent.py → _context()` — no full policy document, no other students' data |
| Missing info / tool errors | Insufficient policy information + `try/except` in `ToolBox.run` |
| Tracing (tools, latency, tokens, evidence) | `logs/traces.jsonl`, trace expander in UI |

## 4. Assignment 2 — privacy & approval rules
1. **Identity** – the student ID comes from the login session, never from chat text. `ToolBox(session_student_id)`.
2. **Authorization in code** – every private tool calls `security.authorize(session_id, target_id)`.
   If target ≠ signed-in student → `DENIED`, audited. Even if the LLM is tricked, the code blocks it.
3. **Human approval** – `request_elective_change` only creates a `PENDING` row in `approvals`.
   The record changes **only** when the Programme Office clicks *Approve*. *Reject* → nothing executes.
4. **Sensitive data** – OTP / password / card / ID numbers are detected by regex: refused by memory,
   redacted in traces, chat history, tickets and audit log.
5. **Audit log** – each important action stores: who (`actor`), what (`action`, `target`, `params`),
   decision (`ALLOWED / DENIED / PENDING_APPROVAL / APPROVED / REJECTED / SAVED / REFUSED`), result.

## 5. Required scenarios → test IDs
| Scenario | Test | Expected (a2) |
|---|---|---|
| Own fee / scholarship status | T01, P01 | Allowed |
| Another student's record | P02, P03 | Blocked + audited |
| Sensitive change to own record | U01 → U02 | Pending → executes only after approval |
| Approval rejected | U03 → U04 | Not executed |
| OTP / password in chat | P04, P05 | Not stored, redacted |
| Payment portal failed | T02 | Policy chunk "Payment Portal Failure" |
| New session recalls fact | T05 → T06 | Memory used |
| Policy has no answer | T07 | "Not enough information" |

## 6. Evaluation
`python evaluate.py` creates `results/evaluation_report.md` (comparison table + per-test traces),
`eval_a1.csv`, `eval_a2.csv`, `audit_log_a2.csv`, `approvals_a2.csv`, `traces_a2.jsonl`.

> ⚠️ The included results were produced with the **mock model** (no API key), so latency and token counts
> are estimates. Re-run `evaluate.py` with a real API key before submission to report real numbers.
