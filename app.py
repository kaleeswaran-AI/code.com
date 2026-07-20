from flask import Flask, render_template, request, redirect, session
import io
import sys
import sqlite3
import threading
import json

app = Flask(__name__)
app.secret_key = "secret123"

# ================= DATABASE =================

conn = sqlite3.connect("exam.db", check_same_thread=False)
cursor = conn.cursor()

# USERS TABLE
cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
    username TEXT PRIMARY KEY,
    password TEXT,
    status TEXT DEFAULT 'active'
)
""")

# ADD STATUS COLUMN IF OLD TABLE EXISTS
try:
    cursor.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
except:
    pass

# PROGRESS TABLE
cursor.execute("""
CREATE TABLE IF NOT EXISTS progress(
    username TEXT,
    question_id INTEGER
)
""")
# USER CODE TABLE
cursor.execute("""
CREATE TABLE IF NOT EXISTS user_code(
    username TEXT,
    question_id INTEGER,
    code TEXT,
    PRIMARY KEY(username, question_id)
)
""")

conn.commit()

conn.commit()

# ================= LOAD QUESTIONS =================

with open("questions.json", "r") as f:
    questions = json.load(f)

# ================= LOGIN PAGE =================

@app.route('/')
def home():
    return render_template("login.html")

# ================= REGISTER =================

@app.route('/register', methods=['POST'])
def register():

    username = request.form['username']
    password = request.form['password']

    try:
        cursor.execute("""
        INSERT INTO users(username,password,status)
        VALUES(?,?,?)
        """, (username, password, "active"))

        conn.commit()

        return redirect("/")

    except:
        return "User already exists"

# ================= LOGIN =================

@app.route('/login', methods=['POST'])
def login():

    username = request.form['username']
    password = request.form['password']

    cursor.execute("""
    SELECT username,password,status
    FROM users
    WHERE username=? AND password=?
    """, (username, password))

    user = cursor.fetchone()

    if user:

        # COMPLETED CHECK
        if user[2] == "completed":
            return "🎓 Course already completed. Access blocked."

        session['user'] = username

        return redirect("/exam")

    else:
        return "Invalid Login"

# ================= QUESTIONS PAGE =================

@app.route('/exam')
def exam():

    if 'user' not in session:
        return redirect("/")

    # COMPLETED QUESTIONS
    cursor.execute("""
    SELECT question_id
    FROM progress
    WHERE username=?
    """, (session['user'],))

    completed = [row[0] for row in cursor.fetchall()]

    total = len(questions)

    percent = int((len(completed) / total) * 100)

    # NEXT UNSOLVED QUESTION
    next_q = None

    for i in range(total):
        if i not in completed:
            next_q = i
            break

    # COURSE COMPLETION
    if percent == 100:

        cursor.execute("""
        UPDATE users
        SET status='completed'
        WHERE username=?
        """, (session['user'],))

        conn.commit()

        return redirect("/certificate")

    return render_template(
        "questions.html",
        questions=questions,
        completed=completed,
        percent=percent,
        next_q=next_q
    )

# ================= OPEN QUESTION =================

@app.route('/question/<int:q_id>')
def question(q_id):
    if q_id < 0 or q_id >= len(questions):
        return "Question not found"

    if 'user' not in session:
        return redirect("/")

    q = questions[q_id]

    session['tests'] = q["tests"]
    session['q_id'] = q_id
    cursor.execute("""
    SELECT code
    FROM user_code
    WHERE username=? AND question_id=?
    """, (session['user'], q_id))

    row = cursor.fetchone()

    saved_code = ""

    if row:
        saved_code = row[0]

    print("=================================")
    print("Username :", session['user'])
    print("Question :", q_id)
    print("Database Row :", row)
    print("Saved Code :", repr(saved_code))
    print("=================================")
    return render_template(
    "exam.html",
    question=q["question"],
    difficulty=q["difficulty"],
    q_id=q_id,
    username=session['user'],
    saved_code=saved_code
)

# ================= RUN BUTTON =================

@app.route('/run', methods=['POST'])
def run():

    code = request.form['code']
    user_input = request.form['input']

    old_stdout = sys.stdout
    old_stdin = sys.stdin

    sys.stdout = mystdout = io.StringIO()

    
    sys.stdin = io.StringIO(str(user_input))
    blocked = [
    "import os",
    "import shutil",
    "import subprocess",
    "__import__",
    "open(",
    "eval(",
    "exec("]
    for word in blocked:
        if word in code:
            return "Restricted command used."


    try:

        exec(code)

        output = mystdout.getvalue()

    except Exception as e:

        output = "Error:\n" + str(e)

    finally:

        sys.stdout = old_stdout
        sys.stdin = old_stdin

    return output

# ================= EXECUTE USER CODE =================

def check_output(output, expected):

    try:

        output = output.replace("\r", "").strip()
        expected = expected.replace("\r", "").strip()

        # Numeric comparison
        try:
            return abs(float(output) - float(expected)) < 0.01
        except:
            pass

        # Ignore extra spaces
        output = " ".join(output.split())
        expected = " ".join(expected.split())

        return output == expected

    except:
        return False


def run_user_code(code, test_input, output_holder):

    old_stdout = sys.stdout
    old_stdin = sys.stdin

    sys.stdout = mystdout = io.StringIO()
    sys.stdin = io.StringIO(str(test_input))

    try:

        exec(code)

        output = mystdout.getvalue()

        output = output.replace("\r", "").strip()

        output_holder.append(output)

    except Exception as e:

        output_holder.append("Error: " + str(e))

    finally:

        sys.stdout = old_stdout
        sys.stdin = old_stdin

   
# ================= SUBMIT =================

@app.route('/submit', methods=['POST'])
def submit():

    if 'user' not in session:
        return redirect("/")

    code = request.form['code']

    test_cases = session.get('tests', [])

    q_id = session.get('q_id')

    passed = 0

    results = []

    # RUN ALL TEST CASES
    for test_input, expected in test_cases:

        output_holder = []

        thread = threading.Thread(
            target=run_user_code,
            args=(code, test_input, output_holder)
        )

        thread.start()

        thread.join(2)

        # TIMEOUT
        if thread.is_alive():

            output = "Timeout"

        else:

            output = output_holder[0]

        

        # FINAL CHECK
        is_pass = check_output(output, expected)

        if is_pass:
            passed += 1

        results.append({
            "input": test_input,
            "expected": expected,
            "output": output,
            "pass": is_pass
        })

    # SAVE PROGRESS IF ALL PASSED
    if passed == len(test_cases):

        cursor.execute("""
        SELECT *
        FROM progress
        WHERE username=? AND question_id=?
        """, (session['user'], q_id))

        already_done = cursor.fetchone()

        if not already_done:

            cursor.execute("""
            INSERT INTO progress(username,question_id)
            VALUES(?,?)
            """, (session['user'], q_id))

            conn.commit()

    return render_template(
        "result.html",
        passed=passed,
        total=len(test_cases),
        results=results
    )

# ================= CERTIFICATE =================

@app.route('/certificate')
def certificate():

    if 'user' not in session:
        return redirect("/")

    return render_template(
        "certificate.html",
        name=session['user']
    )

# ================= SAVE USER CODE =================

@app.route('/save_code', methods=['POST'])
def save_code():

    if 'user' not in session:
        return "Login required"

    q_id = request.form['q_id']
    code = request.form['code']

    cursor.execute("""
    INSERT OR REPLACE INTO user_code(username, question_id, code)
    VALUES (?, ?, ?)
    """, (session['user'], q_id, code))

    conn.commit()

    return "Saved"

# ================= LOGOUT =================

@app.route('/logout')
def logout():

    session.clear()

    return redirect("/")
@app.route('/leaderboard')
def leaderboard():

    cursor.execute("""
    SELECT username,
    COUNT(question_id) as score
    FROM progress
    GROUP BY username
    ORDER BY score DESC
    """)

    scores = cursor.fetchall()

    return render_template(
        "leaderboard.html",
        scores=scores
    )

# ================= START =================

if __name__ == "__main__":
    app.run(debug=True)
