import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")


def get_connection():
    if not SUPABASE_DB_URL:
        raise Exception("SUPABASE_DB_URL is missing from .env")

    return psycopg2.connect(
        SUPABASE_DB_URL,
        cursor_factory=RealDictCursor
    )


def create_tables():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id BIGSERIAL PRIMARY KEY,
            student_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            fingerprint_id INTEGER UNIQUE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance_sessions (
            id BIGSERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id BIGSERIAL PRIMARY KEY,
            session_id BIGINT
                REFERENCES attendance_sessions(id),
            student_id TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance_permissions (
            id BIGSERIAL PRIMARY KEY,
            session_id BIGINT NOT NULL
                REFERENCES attendance_sessions(id),
            device_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.commit()
    cursor.close()
    connection.close()