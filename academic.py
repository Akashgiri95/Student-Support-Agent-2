import json
import re
from datetime import date, datetime

import config, db

with open(config.ELECTIVES_PATH, encoding="utf-8") as f:
    CATALOG = json.load(f)


def available_electives(programme, current=None):
    return [name for name in CATALOG["programmes"].get(programme, []) if name != current]


def match_elective(programme, text):
    value = (text or "").strip().casefold()
    aliases = {key.casefold(): name for key, name in CATALOG["aliases"].items()}
    value = aliases.get(value, value)
    for alias, name in aliases.items():
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", value):
            value = name.casefold()
            break
    for name in CATALOG["programmes"].get(programme, []):
        if value == name.casefold() or name.casefold() in value:
            return name
    return None


def validate_elective_change(student_id, new_elective, request_created_at=None, approval_id=None):
    student = db.get_student(student_id)
    if not student:
        raise ValueError("Student record not found.")

    target = match_elective(student["programme"], new_elective)
    if not target:
        options = available_electives(student["programme"], student["elective"])
        if not options:
            raise ValueError("The Programme Office has not configured elective offerings for your programme.")
        raise ValueError("That elective is not listed for your programme. Available options: " + ", ".join(options))
    if target == student["elective"]:
        raise ValueError("That is already your current elective.")

    term_start = date.fromisoformat(CATALOG["term_start_date"])
    requested_date = (datetime.fromisoformat(request_created_at).date()
                      if request_created_at else date.today())
    days_since_start = (requested_date - term_start).days
    if days_since_start < 0:
        raise ValueError(f"Elective changes open on {term_start.isoformat()}.")
    if days_since_start >= 14:
        raise ValueError("The 14-day elective-change window has closed. Contact the Programme Office for an exception review.")

    pending = db.query(
        "SELECT approval_id FROM approvals WHERE student_id=? AND action='change_elective' AND status='PENDING'",
        (student_id,))
    if any(row["approval_id"] != approval_id for row in pending):
        raise ValueError("You already have a pending elective-change request. Wait for the Programme Office to decide it.")

    approved = db.query(
        "SELECT approval_id FROM approvals WHERE student_id=? AND action='change_elective' "
        "AND status='APPROVED' AND date(created_at)>=?",
        (student_id, term_start.isoformat()))
    if approved:
        raise ValueError("Only one elective change is allowed per term, and one has already been approved.")

    return target


def eligibility_summary(student_id, new_elective, request_created_at=None, approval_id=None):
    student = db.get_student(student_id)
    if not student:
        return {"eligible": False, "checks": [], "message": "Student record not found."}
    try:
        target = match_elective(student["programme"], new_elective)
        if not target:
            validate_elective_change(student_id, new_elective, request_created_at)
    except ValueError as exc:
        return {"eligible": False, "checks": [], "message": str(exc)}

    checks = ["Course is listed for your programme", "Target differs from your current elective"]
    term_start = date.fromisoformat(CATALOG["term_start_date"])
    requested_date = (datetime.fromisoformat(request_created_at).date()
                      if request_created_at else date.today())
    days_since_start = (requested_date - term_start).days
    if days_since_start < 0:
        return {"eligible": False, "checks": checks, "message": f"Elective changes open on {term_start.isoformat()}."}
    if days_since_start >= 14:
        return {"eligible": False, "checks": checks, "message": "The 14-day elective-change window has closed. Contact the Programme Office for an exception review."}
    checks.append("Request is within the 14-day term window")
    pending = db.query(
        "SELECT approval_id FROM approvals WHERE student_id=? AND action='change_elective' AND status='PENDING'",
        (student_id,))
    if any(row["approval_id"] != approval_id for row in pending):
        return {"eligible": False, "checks": checks, "message": "You already have a pending elective-change request."}
    checks.append("No other elective-change request is pending")
    approved = db.query(
        "SELECT approval_id FROM approvals WHERE student_id=? AND action='change_elective' "
        "AND status='APPROVED' AND date(created_at)>=?", (student_id, term_start.isoformat()))
    if approved:
        return {"eligible": False, "checks": checks, "message": "Only one elective change is allowed per term, and one has already been approved."}
    checks.append("No elective change has been approved this term")
    return {"eligible": True, "checks": checks, "message": "You meet the listed request conditions. Seat availability and prerequisites still need Programme Office review."}
