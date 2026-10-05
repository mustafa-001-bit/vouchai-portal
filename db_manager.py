import os
import sqlite3
from datetime import datetime
import pandas as pd

DB_FOLDER = "databases"
os.makedirs(DB_FOLDER, exist_ok=True)


def get_db_path(school_id="default"):
  """Returns the isolated SQLite database path for a specific school."""
  safe_id = "".join(
      c for c in str(school_id).lower() if c.isalnum() or c in ["_", "-"]
  )
  if not safe_id:
    safe_id = "default"
  return os.path.join(DB_FOLDER, f"{safe_id}.db")


def get_db_connection(school_id="default"):
  """Creates a SQLite connection for the target school."""
  conn = sqlite3.connect(get_db_path(school_id))
  conn.row_factory = sqlite3.Row
  return conn


def init_database(school_id="default", excel_path=None, *args, **kwargs):
  """Initializes isolated SQLite tables cleanly without hardcoded demo data."""
  if "school_id" in kwargs:
    school_id = kwargs["school_id"]
  elif args and len(args) > 0 and isinstance(args[0], str):
    school_id = args[0]

  conn = get_db_connection(school_id)
  cursor = conn.cursor()

  # 1. Master Students & Fee Ledger Table
  cursor.execute(
      """
        CREATE TABLE IF NOT EXISTS students (
            Roll_No TEXT PRIMARY KEY,
            Student_Name TEXT NOT NULL,
            Father_Name TEXT NOT NULL,
            Class TEXT NOT NULL,
            WhatsApp_No TEXT NOT NULL,
            Tuition_Fee INTEGER NOT NULL,
            Exam_Fee INTEGER NOT NULL,
            Arrears INTEGER NOT NULL,
            Other_Fee INTEGER DEFAULT 0,
            Due_Date TEXT NOT NULL,
            Status TEXT DEFAULT 'Unpaid',
            Settled_Date TEXT DEFAULT '-',
            TRX_ID TEXT DEFAULT '-',
            Payment_Mode TEXT DEFAULT '-',
            Discount INTEGER DEFAULT 0,
            Paid_Amount INTEGER DEFAULT 0,
            Remaining_Balance INTEGER DEFAULT 0,
            Voucher_Dispatched TEXT DEFAULT 'No',
            Voucher_Dispatch_Time TEXT DEFAULT '-'
        )
    """
  )

  # Auto-migration for schema updates
  cursor.execute("PRAGMA table_info(students)")
  existing_cols = [row[1] for row in cursor.fetchall()]
  for col_name, col_def in [
      ("Other_Fee", "INTEGER DEFAULT 0"),
      ("Discount", "INTEGER DEFAULT 0"),
      ("Paid_Amount", "INTEGER DEFAULT 0"),
      ("Remaining_Balance", "INTEGER DEFAULT 0"),
      ("Voucher_Dispatched", "TEXT DEFAULT 'No'"),
      ("Voucher_Dispatch_Time", "TEXT DEFAULT '-'"),
  ]:
    if col_name not in existing_cols:
      cursor.execute(f"ALTER TABLE students ADD COLUMN {col_name} {col_def}")

  # 2. Permanent Audit Log
  cursor.execute(
      """
        CREATE TABLE IF NOT EXISTS payment_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roll_no TEXT NOT NULL,
            trx_id TEXT UNIQUE NOT NULL,
            amount INTEGER NOT NULL,
            payment_mode TEXT NOT NULL,
            settled_date TEXT NOT NULL,
            discount INTEGER DEFAULT 0,
            remaining_balance INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
  )

  # 3. Persistent School Settings (Titles, Notes, Tokens, PIN)
  cursor.execute(
      """
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT
        )
    """
  )

  conn.commit()

  # Populate only if an Excel file is explicitly provided by the user
  if excel_path and os.path.exists(excel_path):
    cursor.execute("SELECT COUNT(*) FROM students")
    count = cursor.fetchone()[0]
    if count == 0:
      try:
        df = pd.read_excel(excel_path)
        for _, row in df.iterrows():
          cursor.execute(
              """
                        INSERT OR IGNORE INTO students (
                            Roll_No, Student_Name, Father_Name, Class, WhatsApp_No,
                            Tuition_Fee, Exam_Fee, Arrears, Other_Fee, Due_Date, Status, 
                            Settled_Date, TRX_ID, Payment_Mode, Discount, Paid_Amount, 
                            Remaining_Balance, Voucher_Dispatched, Voucher_Dispatch_Time
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Unpaid', '-', '-', '-', 0, 0, 0, 'No', '-')
                    """,
              (
                  str(row["Roll_No"]),
                  str(row["Student_Name"]),
                  str(row["Father_Name"]),
                  str(row["Class"]),
                  str(row["WhatsApp_No"]),
                  int(row.get("Tuition_Fee", 0)),
                  int(row.get("Exam_Fee", 0)),
                  int(row.get("Arrears", 0)),
                  int(row.get("Other_Fee", 0)),
                  str(row.get("Due_Date", datetime.now().strftime("%d-%b-%Y"))),
              ),
          )
        conn.commit()
      except Exception:
        pass

  conn.close()


def update_student_amounts(
    school_id, roll_no, tuition, exam, arrears, other_fee
):
  """Directly updates fee head amounts for a student in SQLite."""
  conn = get_db_connection(school_id)
  cursor = conn.cursor()
  cursor.execute(
      """
        UPDATE students 
        SET Tuition_Fee = ?,
            Exam_Fee = ?,
            Arrears = ?,
            Other_Fee = ?
        WHERE Roll_No = ?
    """,
      (int(tuition), int(exam), int(arrears), int(other_fee), str(roll_no)),
  )
  conn.commit()
  conn.close()


def set_setting(*args, **kwargs):
  """Universal setting writer: accepts (school_id, key, value) or (key, value)."""
  school_id, key, value = "default", "", ""
  if len(args) == 3:
    school_id, key, value = args[0], args[1], args[2]
  elif len(args) == 2:
    key, value = args[0], args[1]

  school_id = kwargs.get("school_id", school_id)
  key = kwargs.get("key", key)
  value = kwargs.get("value", value)

  conn = get_db_connection(school_id)
  cursor = conn.cursor()
  cursor.execute(
      """
        INSERT OR REPLACE INTO app_settings (setting_key, setting_value)
        VALUES (?, ?)
    """,
      (str(key), str(value)),
  )
  conn.commit()
  conn.close()


def get_setting(*args, **kwargs):
  """Universal setting reader: accepts (school_id, key, default) or (key, default)."""
  school_id, key, default = "default", "", ""
  if len(args) == 3:
    school_id, key, default = args[0], args[1], args[2]
  elif len(args) == 2:
    key, default = args[0], args[1]
  elif len(args) == 1:
    key = args[0]

  school_id = kwargs.get("school_id", school_id)
  key = kwargs.get("key", key)
  default = kwargs.get("default", default)

  conn = get_db_connection(school_id)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT setting_value FROM app_settings WHERE setting_key = ?", (str(key),)
  )
  row = cursor.fetchone()
  conn.close()
  return (
      row["setting_value"]
      if row and row["setting_value"] is not None
      else default
  )


def load_students_dataframe(school_id="default"):
  """Fetches students records strictly for the target school."""
  conn = get_db_connection(school_id)
  df = pd.read_sql_query("SELECT * FROM students ORDER BY Roll_No ASC", conn)
  conn.close()
  return df


def is_trx_already_used(school_id, trx_id):
  """Checks payment audit log for transaction replay attempts."""
  if not trx_id or trx_id == "-":
    return False
  conn = get_db_connection(school_id)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT id FROM payment_audit_log WHERE LOWER(trx_id) = LOWER(?)",
      (trx_id.strip(),),
  )
  record = cursor.fetchone()
  conn.close()
  return record is not None


def mark_voucher_dispatched(school_id, roll_no, dispatch_time):
  """Records timestamp when a voucher is delivered via WhatsApp."""
  conn = get_db_connection(school_id)
  cursor = conn.cursor()
  cursor.execute(
      """
        UPDATE students 
        SET Voucher_Dispatched = 'Yes',
            Voucher_Dispatch_Time = ?
        WHERE Roll_No = ?
    """,
      (dispatch_time, str(roll_no)),
  )
  conn.commit()
  conn.close()


def settle_student_payment(
    school_id,
    roll_no,
    trx_id,
    payment_mode,
    amount_paid,
    settled_date,
    discount=0,
    remaining=0,
    status="Paid",
):
  """Atomically records settlement and logs financial audit trail."""
  conn = get_db_connection(school_id)
  cursor = conn.cursor()
  try:
    cursor.execute(
        """
            UPDATE students 
            SET Status = ?,
                TRX_ID = ?,
                Payment_Mode = ?,
                Settled_Date = ?,
                Discount = ?,
                Paid_Amount = ?,
                Remaining_Balance = ?
            WHERE Roll_No = ?
        """,
        (
            status,
            trx_id,
            payment_mode,
            settled_date,
            int(discount),
            int(amount_paid),
            int(remaining),
            str(roll_no),
        ),
    )

    cursor.execute(
        """
            INSERT INTO payment_audit_log (roll_no, trx_id, amount, payment_mode, settled_date, discount, remaining_balance)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(roll_no),
            trx_id,
            int(amount_paid),
            payment_mode,
            settled_date,
            int(discount),
            int(remaining),
        ),
    )

    conn.commit()
    return True, "Payment settled successfully."
  except sqlite3.IntegrityError:
    conn.rollback()
    return (
        False,
        f"Duplicate Transaction! TRX ID '{trx_id}' is already recorded.",
    )
  except Exception as e:
    conn.rollback()
    return False, f"Database error: {str(e)}"
  finally:
    conn.close()


def reset_database_from_excel(school_id="default", excel_path=None):
  """Resets school's ledger and populates only if an Excel sheet is provided."""
  db_p = get_db_path(school_id)
  if os.path.exists(db_p):
    os.remove(db_p)
  init_database(school_id, excel_path)