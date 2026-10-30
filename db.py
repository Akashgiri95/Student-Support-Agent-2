"""SQLite storage: student records, long-term memory, approvals, audit log."""
import json, sqlite3, os
import config


def get_conn():
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(reset=False):
    if reset and os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS students (
        student_id TEXT PRIMARY KEY, name TEXT, programme TEXT, email TEXT, elective TEXT,
        fee_total INTEGER, fee_paid INTEGER, fee_due_date TEXT, scholarship TEXT, cgpa REAL);
    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT, key TEXT, value TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(student_id, key));
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT, category TEXT,
        description TEXT, status TEXT DEFAULT 'OPEN', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS approvals (
        approval_id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT, action TEXT,
        params TEXT, status TEXT DEFAULT 'PENDING', decided_by TEXT, result TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, decided_at TEXT);
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT CURRENT_TIMESTAMP,
        actor TEXT, action TEXT, target TEXT, params TEXT, decision TEXT, result TEXT);
    CREATE TABLE IF NOT EXISTS student_change_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT CURRENT_TIMESTAMP,
        actor TEXT, student_id TEXT, action TEXT, field TEXT, old_value TEXT,
        new_value TEXT, source TEXT, approval_id INTEGER);
    """)
    if conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0:
        with open(config.STUDENTS_PATH) as f:
            for s in json.load(f):
                conn.execute("INSERT INTO students VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (s["student_id"], s["name"], s["programme"], s["email"], s["elective"],
                              s["fee_total"], s["fee_paid"], s["fee_due_date"], s["scholarship"], s["cgpa"]))
    conn.commit()
    conn.close()


def get_student(student_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_students():
    conn = get_conn()
    rows = conn.execute("SELECT student_id, name FROM students").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query(sql, params=()):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def execute(sql, params=()):
    conn = get_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def delete_memory(student_id, key):
    conn = get_conn()
    conn.execute("DELETE FROM memories WHERE student_id=? AND key=?", (student_id, key))
    conn.commit()
    conn.close()


def update_student(student_id, updates, actor, source, approval_id=None):
    allowed_fields = {"name", "programme", "email", "elective", "fee_total", "fee_paid",
                      "fee_due_date", "scholarship", "cgpa"}
    if not updates or not updates.keys() <= allowed_fields:
        raise ValueError("Student update contains no fields or an unsupported field.")
    conn = get_conn()
    row = conn.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError("Student record not found.")
    before = dict(row)
    for field, value in updates.items():
        old_value = before[field]
        if old_value == value:
            continue
        conn.execute(f"UPDATE students SET {field}=? WHERE student_id=?", (value, student_id))
        conn.execute(
            "INSERT INTO student_change_log(actor, student_id, action, field, old_value, new_value, source, approval_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (actor, student_id, "UPDATE", field, str(old_value), str(value), source, approval_id),
        )
    conn.commit()
    conn.close()
    return before


def add_student(student, actor):
    fields = ("student_id", "name", "programme", "email", "elective", "fee_total", "fee_paid",
              "fee_due_date", "scholarship", "cgpa")
    values = tuple(student[field] for field in fields)
    conn = get_conn()
    conn.execute(f"INSERT INTO students ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", values)
    conn.execute(
        "INSERT INTO student_change_log(actor, student_id, action, field, old_value, new_value, source) "
        "VALUES (?,?,?,?,?,?,?)",
        (actor, student["student_id"], "CREATE", "record", None, json.dumps(student), "programme_office"),
    )
    conn.commit()
    conn.close()
