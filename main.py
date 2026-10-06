
import os
from datetime import datetime, timedelta

import resend

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

from database import get_connection, create_tables


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
RESEND_FROM_EMAIL = os.getenv(
    "RESEND_FROM_EMAIL",
    "onboarding@resend.dev"
)

if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

app = FastAPI(title="Classroom Attendance System")

create_tables()


# ============================================================
# MODELS
# ============================================================

class LoginRequest(BaseModel):
    username: str
    password: str


class StartAttendanceRequest(BaseModel):
    duration_minutes: int


class MarkAttendanceRequest(BaseModel):
    student_id: str
    status: str


class PermissionRequest(BaseModel):
    device_name: str


class PermissionApprovalRequest(BaseModel):
    request_id: int
    approved: bool


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():
    return {
        "status": "success",
        "message": "Attendance System Backend is running!"
    }


# ============================================================
# ADMIN LOGIN
# ============================================================

@app.post("/admin/login")
def admin_login(data: LoginRequest):

    if data.username == "rsk" and data.password == "rsk123":
        return {
            "status": "success",
            "message": "Login successful"
        }

    raise HTTPException(
        status_code=401,
        detail="Invalid username or password"
    )


# ============================================================
# STUDENTS
# ============================================================

@app.get("/students")
def get_students():

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            SELECT id, student_id, name, fingerprint_id
            FROM students
            ORDER BY id
        """)

        students = cursor.fetchall()

        return {
            "status": "success",
            "students": [dict(student) for student in students]
        }

    finally:
        cursor.close()
        connection.close()


# ============================================================
# ACTIVE SESSION
# ============================================================

def get_active_session(connection):

    cursor = connection.cursor()

    try:
        cursor.execute("""
            SELECT *
            FROM attendance_sessions
            WHERE status = 'active'
            ORDER BY id DESC
            LIMIT 1
        """)

        return cursor.fetchone()

    finally:
        cursor.close()


# ============================================================
# SEND ATTENDANCE EMAIL USING RESEND
# ============================================================

def send_attendance_email(session_id):

    try:

        if not RESEND_API_KEY:
            print("Email error: RESEND_API_KEY missing")
            return False

        if not ADMIN_EMAIL:
            print("Email error: ADMIN_EMAIL missing")
            return False

        if not RESEND_FROM_EMAIL:
            print("Email error: RESEND_FROM_EMAIL missing")
            return False

        connection = get_connection()
        cursor = connection.cursor()

        # Get session
        cursor.execute("""
            SELECT *
            FROM attendance_sessions
            WHERE id = %s
        """, (session_id,))

        session = cursor.fetchone()

        # Get attendance records
        cursor.execute("""
            SELECT
                attendance.student_id,
                students.name,
                attendance.status,
                attendance.time
            FROM attendance
            LEFT JOIN students
                ON attendance.student_id = students.student_id
            WHERE attendance.session_id = %s
            ORDER BY students.id
        """, (session_id,))

        records = cursor.fetchall()

        cursor.close()
        connection.close()

        if not session:
            print("Email error: Attendance session not found")
            return False

        total = len(records)

        present = sum(
            1
            for r in records
            if r["status"].lower() == "present"
        )

        absent = sum(
            1
            for r in records
            if r["status"].lower() == "absent"
        )

        # Build student attendance list
        student_lines = ""

        for record in records:

            student_lines += (
                f"{record['student_id']} - "
                f"{record['name'] or 'Unknown'} - "
                f"{record['status'].upper()}\n"
            )

        body = f"""
CLASSROOM ATTENDANCE REPORT
============================

Date: {session['date']}
Start Time: {session['start_time']}
End Time: {session['end_time']}

Total Students : {total}
Present         : {present}
Absent          : {absent}

--------------------------------
STUDENT ATTENDANCE
--------------------------------

{student_lines}

--------------------------------

This attendance report was generated automatically
by the Classroom Attendance System.
"""

        # Send using Resend HTTPS API
        response = resend.Emails.send({
            "from": RESEND_FROM_EMAIL,
            "to": [ADMIN_EMAIL],
            "subject": f"Classroom Attendance Report - {session['date']}",
            "text": body
        })

        print("Attendance email sent successfully.")
        print("Resend response:", response)

        return True

    except Exception as error:

        print("Email sending failed:", error)

        return False


# ============================================================
# COMPLETE SESSION
# ============================================================

def complete_session(connection, session_id):

    cursor = connection.cursor()

    cursor.execute("""
        SELECT *
        FROM attendance_sessions
        WHERE id = %s
    """, (session_id,))

    session = cursor.fetchone()

    if not session:
        cursor.close()
        return None

    cursor.execute("""
        SELECT student_id
        FROM students
    """)

    students = cursor.fetchall()

    cursor.execute("""
        SELECT student_id
        FROM attendance
        WHERE session_id = %s
    """, (session_id,))

    marked_students = cursor.fetchall()

    marked_ids = {
        student["student_id"]
        for student in marked_students
    }

    current_time = datetime.now().strftime("%H:%M:%S")

    # Automatically mark unmarked students as Absent
    for student in students:

        student_id = student["student_id"]

        if student_id not in marked_ids:

            cursor.execute("""
                INSERT INTO attendance
                (
                    session_id,
                    student_id,
                    date,
                    time,
                    status
                )
                VALUES (%s, %s, %s, %s, %s)
            """, (
                session_id,
                student_id,
                session["date"],
                current_time,
                "Absent"
            ))

    # Complete session
    cursor.execute("""
        UPDATE attendance_sessions
        SET status = 'completed',
            end_time = %s
        WHERE id = %s
    """, (
        current_time,
        session_id
    ))

    # Close permission requests
    cursor.execute("""
        UPDATE attendance_permissions
        SET status = 'closed'
        WHERE session_id = %s
        AND status IN ('pending', 'approved')
    """, (session_id,))

    connection.commit()

    cursor.close()

    # Send email after database commit
    email_sent = send_attendance_email(session_id)

    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            COUNT(*) AS total,
            COALESCE(
                SUM(
                    CASE
                        WHEN status = 'Present'
                        THEN 1
                        ELSE 0
                    END
                ), 0
            ) AS present,
            COALESCE(
                SUM(
                    CASE
                        WHEN status = 'Absent'
                        THEN 1
                        ELSE 0
                    END
                ), 0
            ) AS absent
        FROM attendance
        WHERE session_id = %s
    """, (session_id,))

    result = cursor.fetchone()

    cursor.close()

    return {
        "session_id": session_id,
        "total": int(result["total"]),
        "present": int(result["present"]),
        "absent": int(result["absent"]),
        "email_sent": email_sent
    }


# ============================================================
# START ATTENDANCE
# ============================================================

@app.post("/attendance/start")
def start_attendance(data: StartAttendanceRequest):

    connection = get_connection()

    try:

        active_session = get_active_session(connection)

        if active_session:
            raise HTTPException(
                status_code=400,
                detail="Attendance is already running"
            )

        if data.duration_minutes <= 0:
            raise HTTPException(
                status_code=400,
                detail="Duration must be greater than 0"
            )

        now = datetime.now()

        end_time = now + timedelta(
            minutes=data.duration_minutes
        )

        date_value = now.strftime("%Y-%m-%d")
        start_time_value = now.strftime("%H:%M:%S")
        end_time_value = end_time.strftime("%H:%M:%S")

        cursor = connection.cursor()

        cursor.execute("""
            INSERT INTO attendance_sessions
            (
                date,
                start_time,
                end_time,
                status
            )
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (
            date_value,
            start_time_value,
            end_time_value,
            "active"
        ))

        session_id = cursor.fetchone()["id"]

        connection.commit()
        cursor.close()

        return {
            "status": "success",
            "message": "Attendance started",
            "session_id": session_id,
            "date": date_value,
            "start_time": start_time_value,
            "end_time": end_time_value
        }

    finally:
        connection.close()


# ============================================================
# SESSION STATUS
# ============================================================

@app.get("/attendance/session")
def attendance_session():

    connection = get_connection()

    try:

        session = get_active_session(connection)

        if session:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) AS total_marked,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN status = 'Present'
                                THEN 1
                                ELSE 0
                            END
                        ), 0
                    ) AS present
                FROM attendance
                WHERE session_id = %s
            """, (session["id"],))

            result = cursor.fetchone()

            cursor.execute("""
                SELECT COUNT(*) AS count
                FROM students
            """)

            total_students = cursor.fetchone()["count"]

            cursor.close()

            marked = int(result["total_marked"])
            present = int(result["present"])
            absent = marked - present

            return {
                "active": True,
                "session_id": session["id"],
                "date": session["date"],
                "start_time": session["start_time"],
                "end_time": session["end_time"],
                "total": int(total_students),
                "present": present,
                "absent": absent,
                "waiting": int(total_students) - marked
            }

        return {
            "active": False,
            "session_id": None,
            "total": 0,
            "present": 0,
            "absent": 0,
            "waiting": 0
        }

    finally:
        connection.close()


# ============================================================
# PHONE 2 REQUEST PERMISSION
# ============================================================

@app.post("/attendance/request")
def request_permission(data: PermissionRequest):

    connection = get_connection()

    try:

        session = get_active_session(connection)

        if not session:
            raise HTTPException(
                status_code=400,
                detail="No active attendance session"
            )

        cursor = connection.cursor()

        cursor.execute("""
            SELECT *
            FROM attendance_permissions
            WHERE session_id = %s
            AND device_name = %s
            AND status IN ('pending', 'approved')
            ORDER BY id DESC
            LIMIT 1
        """, (
            session["id"],
            data.device_name
        ))

        existing = cursor.fetchone()

        if existing:

            cursor.close()

            return {
                "status": "success",
                "request_id": existing["id"],
                "permission": existing["status"]
            }

        cursor.execute("""
            INSERT INTO attendance_permissions
            (
                session_id,
                device_name,
                status
            )
            VALUES (%s, %s, %s)
            RETURNING id
        """, (
            session["id"],
            data.device_name,
            "pending"
        ))

        request_id = cursor.fetchone()["id"]

        connection.commit()
        cursor.close()

        return {
            "status": "success",
            "request_id": request_id,
            "permission": "pending",
            "message": "Permission request sent"
        }

    finally:
        connection.close()


# ============================================================
# ADMIN CHECKS REQUESTS
# ============================================================

@app.get("/attendance/requests")
def get_permission_requests():

    connection = get_connection()

    try:

        session = get_active_session(connection)

        if not session:
            return {
                "status": "success",
                "requests": []
            }

        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                id,
                session_id,
                device_name,
                status,
                created_at
            FROM attendance_permissions
            WHERE session_id = %s
            ORDER BY id DESC
        """, (session["id"],))

        requests = cursor.fetchall()

        cursor.close()

        return {
            "status": "success",
            "requests": [
                dict(request)
                for request in requests
            ]
        }

    finally:
        connection.close()


# ============================================================
# ADMIN APPROVE / REJECT
# ============================================================

@app.post("/attendance/approve")
def approve_permission(
    data: PermissionApprovalRequest
):

    connection = get_connection()

    try:

        new_status = (
            "approved"
            if data.approved
            else "rejected"
        )

        cursor = connection.cursor()

        cursor.execute("""
            SELECT *
            FROM attendance_permissions
            WHERE id = %s
        """, (data.request_id,))

        request = cursor.fetchone()

        if not request:

            cursor.close()

            raise HTTPException(
                status_code=404,
                detail="Permission request not found"
            )

        cursor.execute("""
            UPDATE attendance_permissions
            SET status = %s
            WHERE id = %s
        """, (
            new_status,
            data.request_id
        ))

        connection.commit()
        cursor.close()

        return {
            "status": "success",
            "request_id": data.request_id,
            "permission": new_status
        }

    finally:
        connection.close()


# ============================================================
# PHONE 2 CHECKS PERMISSION
# ============================================================

@app.get("/attendance/permission/{request_id}")
def check_permission(request_id: int):

    connection = get_connection()

    try:

        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                id,
                session_id,
                device_name,
                status
            FROM attendance_permissions
            WHERE id = %s
        """, (request_id,))

        request = cursor.fetchone()

        cursor.close()

        if not request:

            raise HTTPException(
                status_code=404,
                detail="Permission request not found"
            )

        return {
            "status": "success",
            "request_id": request["id"],
            "session_id": request["session_id"],
            "device_name": request["device_name"],
            "permission": request["status"]
        }

    finally:
        connection.close()


# ============================================================
# MARK ATTENDANCE
# ============================================================

@app.post("/attendance/mark")
def mark_attendance(data: MarkAttendanceRequest):

    connection = get_connection()

    try:

        session = get_active_session(connection)

        if not session:
            raise HTTPException(
                status_code=400,
                detail="No active attendance session"
            )

        if data.status not in ["Present", "Absent"]:
            raise HTTPException(
                status_code=400,
                detail="Status must be Present or Absent"
            )

        cursor = connection.cursor()

        cursor.execute("""
            SELECT *
            FROM students
            WHERE student_id = %s
        """, (
            data.student_id,
        ))

        student = cursor.fetchone()

        if not student:

            cursor.close()

            raise HTTPException(
                status_code=404,
                detail="Student not found"
            )

        cursor.execute("""
            SELECT *
            FROM attendance
            WHERE session_id = %s
            AND student_id = %s
        """, (
            session["id"],
            data.student_id
        ))

        existing = cursor.fetchone()

        current_time = datetime.now().strftime(
            "%H:%M:%S"
        )

        if existing:

            cursor.execute("""
                UPDATE attendance
                SET
                    status = %s,
                    time = %s
                WHERE session_id = %s
                AND student_id = %s
            """, (
                data.status,
                current_time,
                session["id"],
                data.student_id
            ))

        else:

            cursor.execute("""
                INSERT INTO attendance
                (
                    session_id,
                    student_id,
                    date,
                    time,
                    status
                )
                VALUES (%s, %s, %s, %s, %s)
            """, (
                session["id"],
                data.student_id,
                session["date"],
                current_time,
                data.status
            ))

        connection.commit()
        cursor.close()

        return {
            "status": "success",
            "student_id": data.student_id,
            "student_name": student["name"],
            "attendance": data.status
        }

    finally:
        connection.close()


# ============================================================
# ATTENDANCE RECORDS
# ============================================================

@app.get("/attendance/records")
def attendance_records():

    connection = get_connection()

    try:

        cursor = connection.cursor()

        cursor.execute("""
            SELECT *
            FROM attendance_sessions
            WHERE status = 'completed'
            ORDER BY id DESC
            LIMIT 1
        """)

        session = cursor.fetchone()

        if not session:

            cursor.close()

            return {
                "status": "success",
                "records": []
            }

        cursor.execute("""
            SELECT
                attendance.student_id,
                students.name,
                attendance.date,
                attendance.time,
                attendance.status
            FROM attendance
            LEFT JOIN students
                ON attendance.student_id = students.student_id
            WHERE attendance.session_id = %s
            ORDER BY students.id
        """, (
            session["id"],
        ))

        records = cursor.fetchall()

        cursor.close()

        return {
            "status": "success",
            "session_id": session["id"],
            "date": session["date"],
            "start_time": session["start_time"],
            "end_time": session["end_time"],
            "records": [
                dict(record)
                for record in records
            ]
        }

    finally:
        connection.close()


# ============================================================
# CLOSE ATTENDANCE
# ============================================================

@app.post("/attendance/close")
def close_attendance():

    connection = get_connection()

    try:

        session = get_active_session(connection)

        if not session:

            raise HTTPException(
                status_code=400,
                detail="No active attendance session"
            )

        result = complete_session(
            connection,
            session["id"]
        )

        return {
            "status": "success",
            "message": "Attendance completed",
            **result
        }

    finally:
        connection.close()

