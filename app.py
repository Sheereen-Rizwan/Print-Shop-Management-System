from flask import Flask, render_template, request, redirect, url_for, flash, g
import sqlite3

app = Flask(__name__)
app.secret_key = "printshop123"
DATABASE = "database.db"

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db:
        db.close()

def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rv = cur.fetchall()
    return rv[0] if one else rv

def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid

def get_setting(key, default=None):
    row = query("SELECT setting_value FROM settings WHERE setting_key = ?", (key,), one=True)
    return row['setting_value'] if row else default

def get_price(item_name):
    row = query("SELECT amount FROM prices WHERE item_name = ?", (item_name,), one=True)
    return row['amount'] if row else 0

def calculate_job_price(paper_size, print_type, pages, copies, binding):
    key = f"{paper_size}_{print_type}"   
    unit_price = get_price(key)
    total = unit_price * pages * copies
    if binding:
        total += get_price('Binding')
    return total

def safe_float(value, field_name):
    try:
        val = float(value)
        if val <= 0:
            raise ValueError
        return val
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a positive number.")

def safe_int(value, field_name, min_val=1):
    try:
        val = int(value)
        if val < min_val:
            raise ValueError
        return val
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a whole number of at least {min_val}.")

@app.route('/')
def dashboard():
    stats = query("""
        SELECT
            (SELECT COUNT(*) FROM students) AS total_students,
            (SELECT COALESCE(SUM(amount),0) FROM transactions
             WHERE amount > 0
               AND DATE(created_at) = DATE('now','localtime')) AS charges_today,
            (SELECT ABS(COALESCE(SUM(amount),0)) FROM transactions
             WHERE amount < 0
               AND DATE(created_at) = DATE('now','localtime')) AS payments_today,
            (SELECT COALESCE(SUM(amount),0) FROM transactions) AS total_pending
    """, one=True)

    top_debtors = query("""
        SELECT s.student_id, s.name, b.batch_name, st.stream_name,
               COALESCE(SUM(t.amount),0) AS balance
        FROM students s
        JOIN batch_streams bs ON bs.batch_stream_id = s.batch_stream_id
        JOIN batches b        ON b.batch_id = bs.batch_id
        JOIN streams st       ON st.stream_id = bs.stream_id
        LEFT JOIN transactions t ON t.student_id = s.student_id
        GROUP BY s.student_id
        HAVING balance > 0
        ORDER BY balance DESC
        LIMIT 5
    """)

    recent_jobs = query("""
        SELECT pj.*, s.name AS student_name,
               COALESCE(b.batch_name, b2.batch_name) AS batch_name,
               COALESCE(st.stream_name, st2.stream_name) AS stream_name,
               sg.group_name
        FROM print_jobs pj
        LEFT JOIN students s              ON s.student_id = pj.student_id
        LEFT JOIN batch_streams bs2       ON bs2.batch_stream_id = s.batch_stream_id
        LEFT JOIN batches b2              ON b2.batch_id = bs2.batch_id
        LEFT JOIN streams st2             ON st2.stream_id = bs2.stream_id
        LEFT JOIN subject_groups sg       ON sg.subject_group_id = pj.subject_group_id
        LEFT JOIN batch_streams bs        ON bs.batch_stream_id = sg.batch_stream_id
        LEFT JOIN batches b               ON b.batch_id = bs.batch_id
        LEFT JOIN streams st              ON st.stream_id = bs.stream_id
        ORDER BY (pj.status = 'pending') DESC, pj.created_at DESC
        LIMIT 8
    """)

    batch_summary = query("""
        SELECT b.batch_name, st.stream_name, sg.group_name,
               COUNT(DISTINCT s.student_id) AS student_count,
               COALESCE(SUM(t.amount), 0) AS pending_balance
        FROM subject_groups sg
        JOIN batch_streams bs ON bs.batch_stream_id = sg.batch_stream_id
        JOIN batches b        ON b.batch_id = bs.batch_id
        JOIN streams st       ON st.stream_id = bs.stream_id
        LEFT JOIN students s     ON s.subject_group_id = sg.subject_group_id
        LEFT JOIN transactions t ON t.student_id = s.student_id
        GROUP BY sg.subject_group_id
        ORDER BY b.year DESC, st.stream_name, sg.group_name
    """)

    return render_template('dashboard.html', stats=stats, top_debtors=top_debtors,
                           recent_jobs=recent_jobs, batch_summary=batch_summary)

@app.route('/students')
def students():
    groups = query("""
        SELECT bs.batch_stream_id, sg.subject_group_id,
               b.batch_name, st.stream_name, sg.group_name,
               COUNT(s.student_id) AS student_count
        FROM batch_streams bs
        JOIN batches b        ON b.batch_id  = bs.batch_id
        JOIN streams st       ON st.stream_id = bs.stream_id
        JOIN subject_groups sg ON sg.batch_stream_id = bs.batch_stream_id
        LEFT JOIN students s  ON s.subject_group_id = sg.subject_group_id
        GROUP BY sg.subject_group_id
        ORDER BY b.year DESC, st.stream_name, sg.group_name
    """)
    return render_template('students.html', groups=groups)


@app.route('/students/group/<int:subject_group_id>')
def students_in_group(subject_group_id):
    group_info = query("""
        SELECT b.batch_name, st.stream_name, sg.group_name
        FROM subject_groups sg
        JOIN batch_streams bs ON bs.batch_stream_id = sg.batch_stream_id
        JOIN batches b        ON b.batch_id  = bs.batch_id
        JOIN streams st       ON st.stream_id = bs.stream_id
        WHERE sg.subject_group_id = ?
    """, (subject_group_id,), one=True)

    student_list = query("""
        SELECT s.student_id, s.name, s.phone, s.index_number,
               COALESCE(SUM(t.amount), 0) AS balance
        FROM students s
        LEFT JOIN transactions t ON t.student_id = s.student_id
        WHERE s.subject_group_id = ?
        GROUP BY s.student_id
        ORDER BY s.name
    """, (subject_group_id,))

    return render_template('students_table.html', students=student_list, group_info=group_info)

@app.route('/students/add', methods=['GET', 'POST'])
def add_student():
    subject_groups = query("""
        SELECT sg.subject_group_id, sg.group_name,
               b.batch_name, st.stream_name
        FROM subject_groups sg
        JOIN batch_streams bs ON bs.batch_stream_id = sg.batch_stream_id
        JOIN batches b        ON b.batch_id         = bs.batch_id
        JOIN streams st       ON st.stream_id       = bs.stream_id
        ORDER BY b.year DESC, st.stream_name, sg.group_name
    """)
    if request.method == 'POST':
        name         = request.form['name'].strip()
        phone        = request.form.get('phone', '').strip() or None
        index_number = request.form.get('index_number', '').strip() or None
        sgid         = int(request.form['subject_group_id'])

        sg_row = query("SELECT batch_stream_id FROM subject_groups WHERE subject_group_id = ?",
                        (sgid,), one=True)
        bsid = sg_row['batch_stream_id']

        execute("""INSERT INTO students (name, phone, index_number, batch_stream_id, subject_group_id)
                   VALUES (?,?,?,?,?)""",
                (name, phone, index_number, bsid, sgid))
        flash(f"Student '{name}' added!", "success")
        return redirect(url_for('students'))
    return render_template('add_student.html', subject_groups=subject_groups)

@app.route('/students/<int:sid>/delete', methods=['POST'])
def delete_student(sid):
    execute("DELETE FROM students WHERE student_id = ?", (sid,))
    flash("Student deleted.", "info")
    return redirect(url_for('students'))

@app.route('/students/<int:sid>')
def student_profile(sid):
    student = query("""
        SELECT s.student_id, s.name, s.phone,
               b.batch_name, st.stream_name,
               COALESCE(SUM(t.amount), 0) AS balance
        FROM students s
        JOIN batch_streams bs ON bs.batch_stream_id = s.batch_stream_id
        JOIN batches b        ON b.batch_id         = bs.batch_id
        JOIN streams st       ON st.stream_id       = bs.stream_id
        LEFT JOIN transactions t ON t.student_id    = s.student_id
        WHERE s.student_id = ?
        GROUP BY s.student_id
    """, (sid,), one=True)

    transactions = query("""
        SELECT * FROM transactions
        WHERE student_id = ?
        ORDER BY created_at DESC
    """, (sid,))

    return render_template('profile.html', student=student, transactions=transactions)

@app.route('/students/<int:sid>/charge', methods=['GET', 'POST'])
def add_charge(sid):
    student = query("SELECT * FROM students WHERE student_id = ?", (sid,), one=True)
    if request.method == 'POST':
        desc = request.form['description'].strip()
        try:
            if not desc:
                raise ValueError("Description is required.")
            amount = safe_float(request.form['amount'], "Amount")
            execute("INSERT INTO transactions (student_id, type, description, amount) VALUES (?,?,?,?)",
                    (sid, 'charge', desc, amount))
            flash(f"Charge of Rs. {amount:.2f} added.", "success")
            return redirect(url_for('student_profile', sid=sid))
        except ValueError as e:
            flash(str(e), "danger")
    return render_template('add_charge.html', student=student)

@app.route('/students/<int:sid>/payment', methods=['GET', 'POST'])
def add_payment(sid):
    student = query("""
        SELECT s.*, COALESCE(SUM(t.amount),0) AS balance
        FROM students s LEFT JOIN transactions t ON t.student_id = s.student_id
        WHERE s.student_id = ? GROUP BY s.student_id
    """, (sid,), one=True)
    if request.method == 'POST':
        try:
            amount = safe_float(request.form['amount'], "Amount")
            desc = request.form.get('description', 'Cash payment').strip() or 'Cash payment'
            execute("INSERT INTO transactions (student_id, type, description, amount) VALUES (?,?,?,?)",
                    (sid, 'payment', desc, -amount))
            flash(f"Payment of Rs. {amount:.2f} recorded.", "success")
            return redirect(url_for('student_profile', sid=sid))
        except ValueError as e:
            flash(str(e), "danger")
    return render_template('add_payment.html', student=student)

@app.route('/batch-charge', methods=['GET', 'POST'])
def batch_charge():
    subject_groups = query("""
        SELECT sg.subject_group_id, sg.group_name, b.batch_name, st.stream_name
        FROM subject_groups sg
        JOIN batch_streams bs ON bs.batch_stream_id = sg.batch_stream_id
        JOIN batches b        ON b.batch_id         = bs.batch_id
        JOIN streams st       ON st.stream_id       = bs.stream_id
        ORDER BY b.year DESC, st.stream_name, sg.group_name
    """)
    if request.method == 'POST':
        try:
            sgid = int(request.form['subject_group_id'])
            desc = request.form['description'].strip()
            if not desc:
                raise ValueError("Description is required.")
            amount = safe_float(request.form['amount'], "Amount")

            students_in_group = query(
                "SELECT student_id FROM students WHERE subject_group_id = ?", (sgid,))

            if not students_in_group:
                flash("No students found in that group.", "warning")
            else:
                db = get_db()
                db.executemany(
                    "INSERT INTO transactions (student_id, type, description, amount) VALUES (?,?,?,?)",
                    [(row['student_id'], 'charge', desc, amount) for row in students_in_group]
                )
                db.commit()
                flash(f"Batch charge applied to {len(students_in_group)} students.", "success")
                return redirect(url_for('dashboard'))
        except ValueError as e:
            flash(str(e), "danger")

    return render_template('batch_charge.html', subject_groups=subject_groups)

@app.route('/transactions')
def all_transactions():
    txns = query("""
        SELECT t.*, s.name AS student_name, b.batch_name, st.stream_name
        FROM transactions t
        JOIN students s        ON s.student_id = t.student_id
        JOIN batch_streams bs  ON bs.batch_stream_id = s.batch_stream_id
        JOIN batches b         ON b.batch_id = bs.batch_id
        JOIN streams st        ON st.stream_id = bs.stream_id
        ORDER BY t.created_at DESC
        LIMIT 200
    """)
    return render_template('transactions.html', transactions=txns)

@app.route('/prices')
def prices():
    price_list = query("SELECT * FROM prices ORDER BY price_id")
    return render_template('prices.html', prices=price_list)

@app.route('/prices/update', methods=['POST'])
def update_prices():
    for key in request.form:
        if key.startswith('price_'):
            price_id = int(key.replace('price_', ''))
            amount = float(request.form[key])
            execute("UPDATE prices SET amount = ? WHERE price_id = ?", (amount, price_id))
    flash("Prices updated.", "success")
    return redirect(url_for('prices'))

@app.route('/settings')
def settings():
    auto_charge = get_setting('auto_charge_print_jobs', 'off')
    return render_template('settings.html', auto_charge=auto_charge)

@app.route('/settings/update', methods=['POST'])
def update_settings():
    value = 'on' if request.form.get('auto_charge_print_jobs') == 'on' else 'off'
    execute("UPDATE settings SET setting_value = ? WHERE setting_key = 'auto_charge_print_jobs'", (value,))
    flash(f"Auto-charge turned {value}.", "success")
    return redirect(url_for('settings'))

@app.route('/print-jobs')
def print_jobs():
    pending = query("""
        SELECT pj.*, s.name AS student_name,
               COALESCE(b.batch_name, b2.batch_name) AS batch_name,
               COALESCE(st.stream_name, st2.stream_name) AS stream_name,
               sg.group_name
        FROM print_jobs pj
        LEFT JOIN students s              ON s.student_id = pj.student_id
        LEFT JOIN batch_streams bs2       ON bs2.batch_stream_id = s.batch_stream_id
        LEFT JOIN batches b2              ON b2.batch_id = bs2.batch_id
        LEFT JOIN streams st2             ON st2.stream_id = bs2.stream_id
        LEFT JOIN subject_groups sg       ON sg.subject_group_id = pj.subject_group_id
        LEFT JOIN batch_streams bs        ON bs.batch_stream_id = sg.batch_stream_id
        LEFT JOIN batches b               ON b.batch_id = bs.batch_id
        LEFT JOIN streams st              ON st.stream_id = bs.stream_id
        WHERE pj.status = 'pending'
        ORDER BY pj.created_at
    """)
    done = query("""
        SELECT pj.*, s.name AS student_name,
               COALESCE(b.batch_name, b2.batch_name) AS batch_name,
               COALESCE(st.stream_name, st2.stream_name) AS stream_name,
               sg.group_name
        FROM print_jobs pj
        LEFT JOIN students s              ON s.student_id = pj.student_id
        LEFT JOIN batch_streams bs2       ON bs2.batch_stream_id = s.batch_stream_id
        LEFT JOIN batches b2              ON b2.batch_id = bs2.batch_id
        LEFT JOIN streams st2             ON st2.stream_id = bs2.stream_id
        LEFT JOIN subject_groups sg       ON sg.subject_group_id = pj.subject_group_id
        LEFT JOIN batch_streams bs        ON bs.batch_stream_id = sg.batch_stream_id
        LEFT JOIN batches b               ON b.batch_id = bs.batch_id
        LEFT JOIN streams st              ON st.stream_id = bs.stream_id
        WHERE pj.status = 'done'
        ORDER BY pj.created_at DESC
        LIMIT 20
    """)
    return render_template('print_jobs.html', pending=pending, done=done)


@app.route('/print-jobs/add', methods=['GET', 'POST'])
def add_print_job():
    students_list = query("""
        SELECT s.student_id, s.name, b.batch_name, st.stream_name
        FROM students s
        JOIN batch_streams bs ON bs.batch_stream_id = s.batch_stream_id
        JOIN batches b        ON b.batch_id         = bs.batch_id
        JOIN streams st       ON st.stream_id       = bs.stream_id
        ORDER BY s.name
    """)
    subject_groups = query("""
        SELECT sg.subject_group_id, sg.group_name, b.batch_name, st.stream_name
        FROM subject_groups sg
        JOIN batch_streams bs ON bs.batch_stream_id = sg.batch_stream_id
        JOIN batches b        ON b.batch_id         = bs.batch_id
        JOIN streams st       ON st.stream_id       = bs.stream_id
        ORDER BY b.year DESC, st.stream_name, sg.group_name
    """)
    if request.method == 'POST':
        try:
            target_type = request.form['target_type']
            file_name   = request.form['file_name'].strip()
            if not file_name:
                raise ValueError("File name is required.")
            pages   = safe_int(request.form['pages'], "Pages")
            copies  = safe_int(request.form.get('copies', 1), "Copies")
            print_type = request.form['print_type']
            paper_size = request.form['paper_size']
            binding = 1 if request.form.get('binding') == 'on' else 0

            if target_type == 'student':
                sid = int(request.form['student_id'])
                execute("""INSERT INTO print_jobs
                           (student_id, subject_group_id, file_name, pages, print_type, paper_size, binding, copies)
                           VALUES (?, NULL, ?, ?, ?, ?, ?, ?)""",
                        (sid, file_name, pages, print_type, paper_size, binding, copies))
            else:
                sgid = int(request.form['subject_group_id'])
                execute("""INSERT INTO print_jobs
                           (student_id, subject_group_id, file_name, pages, print_type, paper_size, binding, copies)
                           VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)""",
                        (sgid, file_name, pages, print_type, paper_size, binding, copies))

            flash("Print job added.", "success")
            return redirect(url_for('print_jobs'))
        except ValueError as e:
            flash(str(e), "danger")

    return render_template('add_print_job.html', students=students_list, subject_groups=subject_groups)


@app.route('/print-jobs/<int:job_id>/done', methods=['POST'])
def complete_print_job(job_id):
    job = query("SELECT * FROM print_jobs WHERE job_id = ?", (job_id,), one=True)
    execute("UPDATE print_jobs SET status = 'done' WHERE job_id = ?", (job_id,))

    auto_charge = get_setting('auto_charge_print_jobs', 'off')
    if auto_charge == 'on':
        price = calculate_job_price(job['paper_size'], job['print_type'], job['pages'], job['copies'], job['binding'])
        desc = f"Print: {job['file_name']} ({job['pages']}pg x{job['copies']}, {job['paper_size']}-{job['print_type']}{', bound' if job['binding'] else ''})"

        if job['student_id']:
            execute("INSERT INTO transactions (student_id, type, description, amount) VALUES (?,?,?,?)",
                    (job['student_id'], 'charge', desc, price))
            flash(f"Job marked done. Rs. {price:.2f} charged.", "success")
        else:
            students_in_group = query("SELECT student_id FROM students WHERE subject_group_id = ?",
                                       (job['subject_group_id'],))
            db = get_db()
            db.executemany(
                "INSERT INTO transactions (student_id, type, description, amount) VALUES (?,?,?,?)",
                [(row['student_id'], 'charge', desc, price) for row in students_in_group]
            )
            db.commit()
            flash(f"Job marked done. Rs. {price:.2f} charged to {len(students_in_group)} students.", "success")
    else:
        flash("Job marked done. (Auto-charge is off — remember to charge manually.)", "info")

    return redirect(url_for('print_jobs'))

@app.route('/search')
def search_students():
    q = request.args.get('q', '').strip()
    results = []
    if q:
        results = query("""
            SELECT s.student_id, s.name, s.index_number, s.phone,
                   b.batch_name, st.stream_name, sg.group_name,
                   COALESCE(SUM(t.amount), 0) AS balance
            FROM students s
            JOIN subject_groups sg ON sg.subject_group_id = s.subject_group_id
            JOIN batch_streams bs  ON bs.batch_stream_id = sg.batch_stream_id
            JOIN batches b         ON b.batch_id = bs.batch_id
            JOIN streams st        ON st.stream_id = bs.stream_id
            LEFT JOIN transactions t ON t.student_id = s.student_id
            WHERE s.name LIKE ? OR s.index_number LIKE ? OR CAST(s.student_id AS TEXT) LIKE ?
            GROUP BY s.student_id
            ORDER BY s.name
        """, (f"%{q}%", f"%{q}%", f"%{q}%"))
    return render_template('search_results.html', results=results, q=q)

@app.route('/students/<int:sid>/edit', methods=['GET', 'POST'])
def edit_student(sid):
    student = query("SELECT * FROM students WHERE student_id = ?", (sid,), one=True)
    subject_groups = query("""
        SELECT sg.subject_group_id, sg.group_name, b.batch_name, st.stream_name
        FROM subject_groups sg
        JOIN batch_streams bs ON bs.batch_stream_id = sg.batch_stream_id
        JOIN batches b        ON b.batch_id         = bs.batch_id
        JOIN streams st       ON st.stream_id       = bs.stream_id
        ORDER BY b.year DESC, st.stream_name, sg.group_name
    """)

    if request.method == 'POST':
        name         = request.form['name'].strip()
        phone        = request.form.get('phone', '').strip() or None
        index_number = request.form.get('index_number', '').strip() or None
        sgid         = int(request.form['subject_group_id'])

        sg_row = query("SELECT batch_stream_id FROM subject_groups WHERE subject_group_id = ?",
                        (sgid,), one=True)
        bsid = sg_row['batch_stream_id']

        execute("""UPDATE students
                   SET name = ?, phone = ?, index_number = ?, subject_group_id = ?, batch_stream_id = ?
                   WHERE student_id = ?""",
                (name, phone, index_number, sgid, bsid, sid))
        flash(f"{name}'s details updated.", "success")
        return redirect(url_for('student_profile', sid=sid))

    return render_template('edit_student.html', student=student, subject_groups=subject_groups)

@app.route('/transactions/<int:tid>/delete', methods=['POST'])
def delete_transaction(tid):
    txn = query("SELECT * FROM transactions WHERE transaction_id = ?", (tid,), one=True)
    if not txn:
        flash("Transaction not found.", "danger")
        return redirect(url_for('all_transactions'))
    sid = txn['student_id']
    execute("DELETE FROM transactions WHERE transaction_id = ?", (tid,))
    flash("Transaction deleted.", "info")
    return redirect(request.referrer or url_for('student_profile', sid=sid))

@app.route('/print-jobs/<int:job_id>/delete', methods=['POST'])
def delete_print_job(job_id):
    execute("DELETE FROM print_jobs WHERE job_id = ?", (job_id,))
    flash("Print job deleted.", "info")
    return redirect(url_for('print_jobs'))

if __name__ == '__main__':
    app.run(debug=True)