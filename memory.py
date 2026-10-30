"""Long-term memory: useful facts persist across sessions; sensitive/temporary facts are refused."""
import db, security

TEMPORARY_WORDS = ["today", "right now", "currently", "this morning", "tonight", "at the moment"]


def save_memory(student_id, key, value):
    text = f"{key} {value}"
    if security.contains_sensitive(text):
        return {"saved": False, "reason": "Sensitive information (OTP/password/card) is never stored."}
    if any(w in text.lower() for w in TEMPORARY_WORDS):
        return {"saved": False, "reason": "Temporary information is not stored as long-term memory."}
    db.execute("INSERT INTO memories(student_id, key, value) VALUES (?,?,?) "
               "ON CONFLICT(student_id, key) DO UPDATE SET value=excluded.value", (student_id, key, value))
    return {"saved": True, "key": key, "value": value}


def load_memories(student_id):
    return db.query("SELECT key, value FROM memories WHERE student_id=?", (student_id,))
