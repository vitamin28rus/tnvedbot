import sqlite3
from datetime import datetime

DB_NAME = "users.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_access BOOLEAN DEFAULT 0,
        trial_attempts INTEGER DEFAULT 2,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Новая таблица логирования запросов
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS queries (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        query_type  TEXT    NOT NULL,
        ts          DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()


def get_user(user_id, username):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute(
            "INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, username)
        )
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
    conn.close()
    return user


def update_access(user_id, full_access):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET full_access = ? WHERE user_id = ?", (full_access, user_id)
    )
    conn.commit()
    conn.close()


def decrement_trial(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET trial_attempts = trial_attempts - 1 WHERE user_id = ? AND trial_attempts > 0",
        (user_id,),
    )
    conn.commit()
    cursor.execute("SELECT trial_attempts FROM users WHERE user_id = ?", (user_id,))
    attempts = cursor.fetchone()[0]
    conn.close()
    return attempts


def check_and_update_trial(user_id, username):
    user = get_user(user_id, username)
    if user[2]:
        return True, user[3]  # есть полный доступ
    elif user[3] > 0:
        attempts_left = 2  #  decrement_trial(user_id)  вставить вместо "=2" для активации функции ограниченного доступа
        return True, attempts_left
    else:
        return False, 0


def log_query(user_id: int, query_type: str):
    """
    Вызвать при любом действии, которое мы хотим подсчитать.
    query_type ∈ {'code','duty','tree','explanations','examples'}
    """
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO queries (user_id, query_type) VALUES (?, ?)", (user_id, query_type)
    )
    conn.commit()
    conn.close()


# Функция‑утилита для подсчёта запросов за период
def count_queries(query_type: str, since: str = None) -> int:
    """
    Если since задан, это строка вида 'now','-1 day','-7 day' для SQLite date().
    Пример: count_queries('code', "-1 day")
    """
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    if since:
        cur.execute(
            f"""
            SELECT COUNT(*) 
            FROM queries 
            WHERE query_type = ? 
              AND ts >= date('now','{since}')
        """,
            (query_type,),
        )
    else:
        cur.execute("SELECT COUNT(*) FROM queries WHERE query_type = ?", (query_type,))

    count = cur.fetchone()[0]
    conn.close()
    return count


def get_analytics_data() -> dict:
    """
    Возвращает ключевые показатели для раздела «Аналитика»:
     - total_users: всего пользователей
     - new_users_24h: новых за 24 часа
     - active7: активных за 7 дней (по запросам)
     - code_total: запросов на подбор кода
     - duty_total: запросов пошлины
     - tree_total: запросов дерева
     - explanations_total: запросов пояснений
     - examples_total: запросов примеров
    """
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    # Пользователи
    total_users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    new_users_24h = cur.execute(
        "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now','-1 day')"
    ).fetchone()[0]

    # Активные юзеры по запросам за 7 дней
    active7 = cur.execute(
        "SELECT COUNT(DISTINCT user_id) FROM queries WHERE ts >= datetime('now','-7 day')"
    ).fetchone()[0]

    # Запросы по типам
    code_total = cur.execute(
        "SELECT COUNT(*) FROM queries WHERE query_type = 'code'"
    ).fetchone()[0]
    duty_total = cur.execute(
        "SELECT COUNT(*) FROM queries WHERE query_type = 'duty'"
    ).fetchone()[0]
    tree_total = cur.execute(
        "SELECT COUNT(*) FROM queries WHERE query_type = 'tree'"
    ).fetchone()[0]
    explanations_total = cur.execute(
        "SELECT COUNT(*) FROM queries WHERE query_type = 'explanations'"
    ).fetchone()[0]
    examples_total = cur.execute(
        "SELECT COUNT(*) FROM queries WHERE query_type = 'examples'"
    ).fetchone()[0]

    conn.close()
    return {
        "total_users": total_users,
        "new_users_24h": new_users_24h,
        "active7": active7,
        "code_total": code_total,
        "duty_total": duty_total,
        "tree_total": tree_total,
        "explanations_total": explanations_total,
        "examples_total": examples_total,
    }
