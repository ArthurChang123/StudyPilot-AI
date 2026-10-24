
import sqlite3
import streamlit as st
import re

from datetime import datetime

DB_NAME = "studypilot.db"


st.set_page_config(
    page_title="StudyPilot AI",
    page_icon="📚"
)


# ---------- Database ----------

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS mistakes (
                question TEXT PRIMARY KEY,
                answer TEXT NOT NULL,
                times_wrong INTEGER DEFAULT 1,
                last_missed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(mistakes)"
            ).fetchall()
        }

        if "status" not in columns:
            conn.execute("""
                ALTER TABLE mistakes
                ADD COLUMN status TEXT NOT NULL
                DEFAULT 'Needs Review'
            """)

        if "next_review_at" not in columns:
            conn.execute("""
                ALTER TABLE mistakes
                ADD COLUMN next_review_at TEXT
            """)

        if "correct_streak" not in columns:
            conn.execute("""
                ALTER TABLE mistakes
                ADD COLUMN correct_streak INTEGER NOT NULL
                DEFAULT 0
            """)

        # Existing mistakes become eligible for review.
        conn.execute("""
            UPDATE mistakes
            SET next_review_at = CURRENT_TIMESTAMP
            WHERE next_review_at IS NULL
              AND status = 'Needs Review'
        """)


def save_attempt(score, total):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            """
            INSERT INTO quiz_attempts (score, total)
            VALUES (?, ?)
            """,
            (score, total)
        )


def save_mistake(question, answer):
    # A wrong answer resets the review cycle.
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            INSERT INTO mistakes (
                question,
                answer,
                times_wrong,
                status,
                next_review_at,
                correct_streak,
                last_missed_at
            )
            VALUES (
                ?, ?, 1, 'Needs Review',
                datetime('now', '+1 day'),
                0,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT(question) DO UPDATE SET
                answer = excluded.answer,
                times_wrong = mistakes.times_wrong + 1,
                status = 'Needs Review',
                next_review_at = datetime('now', '+1 day'),
                correct_streak = 0,
                last_missed_at = CURRENT_TIMESTAMP
        """, (question, answer))


def record_correct_answer(question):
    # Only update questions already in the mistake notebook.
    with sqlite3.connect(DB_NAME) as conn:
        row = conn.execute("""
            SELECT correct_streak
            FROM mistakes
            WHERE question = ?
        """, (question,)).fetchone()

        if row is None:
            return

        new_streak = row[0] + 1

        if new_streak >= 3:
            conn.execute("""
                UPDATE mistakes
                SET correct_streak = ?,
                    status = 'Mastered',
                    next_review_at = NULL
                WHERE question = ?
            """, (new_streak, question))

        else:
            # First consecutive correct answer: 3 days.
            # Second consecutive correct answer: 7 days.
            days_until_review = 3 if new_streak == 1 else 7

            conn.execute("""
                UPDATE mistakes
                SET correct_streak = ?,
                    status = 'Needs Review',
                    next_review_at = datetime(
                        'now',
                        ?
                    )
                WHERE question = ?
            """, (
                new_streak,
                f"+{days_until_review} days",
                question
            ))


def get_history():
    with sqlite3.connect(DB_NAME) as conn:
        return conn.execute("""
            SELECT created_at, score, total
            FROM quiz_attempts
            ORDER BY id DESC
        """).fetchall()


def get_mistakes(needs_review_only=False, due_only=False):
    with sqlite3.connect(DB_NAME) as conn:
        query = """
            SELECT
                question,
                answer,
                times_wrong,
                status,
                next_review_at,
                correct_streak
            FROM mistakes
        """

        conditions = []

        if needs_review_only:
            conditions.append("status = 'Needs Review'")

        if due_only:
            conditions.append(
                "next_review_at IS NOT NULL "
                "AND next_review_at <= CURRENT_TIMESTAMP"
            )

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY times_wrong DESC"

        return conn.execute(query).fetchall()


init_db()


# ---------- Question generation ----------

def generate_questions(notes):
    questions = []

    for line in notes.splitlines():
        sentence = line.strip().rstrip(" .!?")

        if not sentence:
            continue

        # Rule 1: Symbols with descriptions and units.
        detailed_unit_match = re.match(
            r"(.+?)\s+is\s+the\s+(.+?)\s+measured in\s+(.+)",
            sentence,
            re.IGNORECASE
        )

        if detailed_unit_match:
            symbol = detailed_unit_match.group(1).strip()
            description = detailed_unit_match.group(2).strip()
            unit = detailed_unit_match.group(3).strip().rstrip(".")

            questions.append({
                "question": (
                    f"In what unit is {symbol} "
                    f"(the {description}) measured?"
                ),
                "answer": unit,
                "explanation": sentence
            })
            continue

        # Rule 2: Laws and principles.
        law_match = re.match(
            r"(.+?)\s+states that\s+(.+)",
            sentence,
            re.IGNORECASE
        )

        if law_match:
            law = law_match.group(1).strip()
            answer = law_match.group(2).strip().rstrip(".")

            questions.append({
                "question": f"What does {law} state?",
                "answer": answer,
                "explanation": sentence
            })
            continue

        # Rule 3: Measurement units.
        unit_match = re.match(
            r"(.+?)\s+is measured in\s+(.+)",
            sentence,
            re.IGNORECASE
        )

        if unit_match:
            quantity = unit_match.group(1).strip()
            unit = unit_match.group(2).strip().rstrip(".")

            questions.append({
                "question": f"In what unit is {quantity} measured?",
                "answer": unit,
                "explanation": sentence
            })
            continue

        # Rule 4: Fill-in-the-blank fallback.
        words = sentence.split()

        if len(words) >= 4:
            answer = words[-1].strip(".,!?;:")

            questions.append({
                "question": (
                    "Complete the sentence: "
                    + " ".join(words[:-1])
                    + " _____"
                ),
                "answer": answer,
                "explanation": sentence
            })

    return questions[:10]


def normalize_answer(text):
    """Ignore capitalization, spaces, and punctuation."""
    return re.sub(r"[^a-z0-9]", "", text.casefold())


# ---------- Main page ----------

st.title("StudyPilot AI")
st.write("Turn your notes into practice questions.")


# ---------- Review dashboard ----------

all_active_mistakes = get_mistakes(needs_review_only=True)
due_mistakes = get_mistakes(
    needs_review_only=True,
    due_only=True
)
all_mistakes = get_mistakes()

st.subheader("📅 Your Review Plan")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Need Review", len(all_active_mistakes))

with col2:
    st.metric("Due Today", len(due_mistakes))

with col3:
    mastered_count = sum(
        1 for mistake in all_mistakes
        if mistake[3] == "Mastered"
    )
    st.metric("Mastered", mastered_count)


# ---------- Generate quiz ----------

notes = st.text_area(
    "Paste your study notes here:",
    height=200,
    placeholder="Enter your notes, one sentence per line..."
)

if st.button("Generate Quiz"):
    if not notes.strip():
        st.warning("Please enter your notes first.")
    else:
        new_questions = generate_questions(notes)

        if not new_questions:
            st.warning("Please enter longer sentences.")
        else:
            st.session_state["questions"] = new_questions
            st.session_state["quiz_version"] = (
                st.session_state.get("quiz_version", 0) + 1
            )
            st.session_state.pop("submitted_version", None)
            st.success(
                f"Generated {len(new_questions)} questions!"
            )


# ---------- Practice due mistakes ----------

if st.button("Practice Due Mistakes"):
    saved_mistakes = get_mistakes(
        needs_review_only=True,
        due_only=True
    )

    if not saved_mistakes:
        st.info(
            "No mistakes are due for review right now. "
            "Check back when your next review is due."
        )
    else:
        st.session_state["questions"] = [
            {
                "question": question,
                "answer": answer,
                "explanation": (
                    f"You previously missed this question "
                    f"{times_wrong} time(s). "
                    f"Consecutive correct answers: {correct_streak}. "
                    "Review the original notes."
                )
            }
            for (
                question,
                answer,
                times_wrong,
                status,
                next_review_at,
                correct_streak
            ) in saved_mistakes[:10]
        ]

        st.session_state["quiz_version"] = (
            st.session_state.get("quiz_version", 0) + 1
        )
        st.session_state.pop("submitted_version", None)

        st.success(
            f"Loaded {len(st.session_state['questions'])} "
            "questions due for review!"
        )


# ---------- Quiz and grading ----------

if "questions" in st.session_state:
    questions = st.session_state["questions"]
    version = st.session_state["quiz_version"]

    with st.form(f"quiz_form_{version}"):
        answers = []

        for i, question in enumerate(questions):
            answer = st.text_input(
                question["question"],
                key=f"answer_{version}_{i}"
            )
            answers.append(answer)

        submitted = st.form_submit_button("Check Answers")

    if submitted:
        score = sum(
            normalize_answer(user_answer)
            == normalize_answer(question["answer"])
            for user_answer, question in zip(answers, questions)
        )

        save_attempt(score, len(questions))

        for user_answer, question in zip(answers, questions):
            if (
                normalize_answer(user_answer)
                != normalize_answer(question["answer"])
            ):
                save_mistake(
                    question["question"],
                    question["answer"]
                )
            else:
                record_correct_answer(question["question"])

        st.session_state["score"] = score
        st.session_state["submitted_version"] = version

    if st.session_state.get("submitted_version") == version:
        score = st.session_state["score"]

        st.subheader(f"Your Score: {score}/{len(questions)}")
        st.progress(score / len(questions))

        for i, question in enumerate(questions):
            user_answer = st.session_state.get(
                f"answer_{version}_{i}", ""
            )

            if (
                normalize_answer(user_answer)
                == normalize_answer(question["answer"])
            ):
                st.success(f"Question {i + 1}: Correct!")
            else:
                st.error(f"Question {i + 1}: Incorrect!")

            st.write(f"Correct answer: {question['answer']}")
            st.write(f"Explanation: {question['explanation']}")
            st.divider()


# ---------- Permanent mistake notebook ----------

st.subheader("📚 Mistake Notebook")

mistakes = get_mistakes()

if mistakes:
    for i, (
        question,
        answer,
        times_wrong,
        status,
        next_review_at,
        correct_streak
    ) in enumerate(mistakes, 1):
        st.write(f"**{i}. {question}**")
        st.write(f"Correct answer: {answer}")
        st.write(f"Times missed: {times_wrong}")
        st.write(f"Consecutive correct answers: {correct_streak}")

        if status == "Mastered":
            st.success("Status: Mastered")
            st.write("No review currently scheduled.")
        else:
            st.warning("Status: Needs Review")
            st.write(f"Next review: {next_review_at}")

        st.divider()
else:
    st.write("No mistakes recorded yet.")


# ---------- Permanent quiz history ----------

st.subheader("📈 Quiz History")

history = get_history()

if history:
    for i, (created_at, score, total) in enumerate(history, 1):
        st.write(
            f"Attempt {i}: {score}/{total} "
            f"(recorded at {created_at})"
        )

    total_score = sum(row[1] for row in history)
    total_questions = sum(row[2] for row in history)

    if total_questions:
        st.metric(
            "Overall Accuracy",
            f"{total_score / total_questions * 100:.1f}%"
        )
else:
    st.write("Complete your first quiz to see your history.")
