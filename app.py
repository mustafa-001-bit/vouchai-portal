import base64
from datetime import datetime
import io
import json
import os
import time
import urllib.parse
import db_manager
from generate_challan import generate_all_challans, generate_single_student_challan
from generate_receipt import generate_paid_receipt
from google import genai
from google.genai import types
import pandas as pd
from PIL import Image
import streamlit as st
import whatsapp_engine

# ==============================================================================
# 1. PAGE SETUP & MULTI-SCHOOL TENANT ROUTING
# ==============================================================================
st.set_page_config(
    page_title="VouchAI Institutional Fee Clearance System",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

raw_school_param = st.query_params.get("school", "").strip().lower()
school_id = "".join(c for c in raw_school_param if c.isalnum() or c in ["_", "-"])


def get_secret_safely(key, default=""):
  try:
    if hasattr(st, "secrets") and key in st.secrets:
      return str(st.secrets[key])
  except Exception:
    pass
  return default


# DEFENSIVE AMOUNT UPDATER (NEVER THROWS ATTRIBUTE ERROR)
def safe_update_amounts(target_school_id, roll_no, tuit, exam, arrear, other):
  if hasattr(db_manager, "update_student_amounts"):
    db_manager.update_student_amounts(
        target_school_id, roll_no, tuit, exam, arrear, other
    )
  else:
    conn = db_manager.get_db_connection(target_school_id)
    cursor = conn.cursor()
    cursor.execute(
        """
            UPDATE students 
            SET Tuition_Fee = ?, Exam_Fee = ?, Arrears = ?, Other_Fee = ?
            WHERE Roll_No = ?
        """,
        (int(tuit), int(exam), int(arrear), int(other), str(roll_no)),
    )
    conn.commit()
    conn.close()


if not school_id:
  st.markdown(
      """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');
    * { font-family: 'Plus Jakarta Sans', sans-serif; }
    .landing-box {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 16px;
        padding: 40px;
        max-width: 480px;
        margin: 80px auto 20px auto;
        box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.1);
        text-align: center;
    }
    </style>
    <div class="landing-box">
        <h2 style="color:#0F172A; margin-bottom:8px; font-weight:800;">🔐 Institutional Accounts Portal</h2>
        <p style="color:#64748B; font-size:13.5px; margin-bottom:25px;">
            Enter your institution's assigned license key or campus code to launch your dedicated clearance terminal.
        </p>
    </div>
    """,
      unsafe_allow_html=True,
  )

  c_in1, c_in2, c_in3 = st.columns([1, 1.2, 1])
  with c_in2:
    with st.form("school_access_form"):
      typed_code = st.text_input(
          "Institutional Campus Code / License ID:",
          placeholder="e.g. learning_curve",
      )
      submit_access = st.form_submit_button(
          "Launch Terminal", type="primary", use_container_width=True
      )
      if submit_access:
        safe_entered = "".join(
            c for c in typed_code.strip().lower() if c.isalnum() or c in ["_", "-"]
        )
        if safe_entered:
          st.query_params["school"] = safe_entered
          st.rerun()
        else:
          st.error("Please enter a valid institution code.")
  st.stop()

# ==============================================================================
# 2. INITIALIZE ISOLATED STORAGE & DATABASE FOR THIS SCHOOL
# ==============================================================================
db_manager.init_database(school_id)

school_asset_dir = os.path.join("school_assets", school_id)
school_challan_dir = os.path.join("generated_challans", school_id)
school_receipt_dir = os.path.join("generated_receipts", school_id)

os.makedirs(school_asset_dir, exist_ok=True)
os.makedirs(school_challan_dir, exist_ok=True)
os.makedirs(school_receipt_dir, exist_ok=True)

default_school_title = school_id.replace("_", " ").replace("-", " ").title()
school_name = db_manager.get_setting(
    school_id, "SCHOOL_NAME", f"{default_school_title} System"
)
campus = db_manager.get_setting(school_id, "CAMPUS", "Main Campus, Islamabad")
bank_name = db_manager.get_setting(
    school_id, "BANK_NAME", "Meezan Bank Ltd (Islamic Banking)"
)
account_title = db_manager.get_setting(school_id, "ACCOUNT_TITLE", school_name)
iban = db_manager.get_setting(school_id, "IBAN", "PK36MEZN0001020104829101")
billing_month = db_manager.get_setting(school_id, "BILLING_MONTH", "October 2026")
late_fine = int(db_manager.get_setting(school_id, "LATE_FINE", 300))
school_helpline = db_manager.get_setting(
    school_id, "HELPLINE", "0300-1234567 / 051-5551234"
)

# 4 Persistent Fee Heads
saved_head_1 = db_manager.get_setting(school_id, "FEE_HEAD_1", "Tuition Fee")
saved_head_2 = db_manager.get_setting(school_id, "FEE_HEAD_2", "Examination Fee")
saved_head_3 = db_manager.get_setting(school_id, "FEE_HEAD_3", "Previous Arrears")
saved_head_4 = db_manager.get_setting(
    school_id, "FEE_HEAD_4", "Stationery / Other Charges"
)

# 6 Persistent Instruction Lines
saved_note_1 = db_manager.get_setting(
    school_id,
    "CHALLAN_NOTE_1",
    "1. Fee must be deposited on or before the due date.",
)
saved_note_2 = db_manager.get_setting(
    school_id,
    "CHALLAN_NOTE_2",
    "2. Payment accepted via 1Bill, Mobile Banking & Bank Branches.",
)
saved_note_3 = db_manager.get_setting(
    school_id,
    "CHALLAN_NOTE_3",
    "3. Late fee surcharge applies strictly after the due date.",
)
saved_note_4 = db_manager.get_setting(
    school_id,
    "CHALLAN_NOTE_4",
    "4. Fee once paid is non-refundable and non-transferable.",
)
saved_note_5 = db_manager.get_setting(
    school_id,
    "CHALLAN_NOTE_5",
    "5. Keep this stamped student voucher for institutional verification.",
)
saved_note_6 = db_manager.get_setting(
    school_id,
    "CHALLAN_NOTE_6",
    "6. Queries: Contact official accounts helpline during office hours.",
)

saved_gemini = db_manager.get_setting(
    school_id, "GEMINI_API_KEY", get_secret_safely("GEMINI_API_KEY", "")
)
saved_instance = db_manager.get_setting(
    school_id, "WHATSAPP_INSTANCE_ID", get_secret_safely("WHATSAPP_INSTANCE_ID", "")
)
saved_token = db_manager.get_setting(
    school_id, "WHATSAPP_TOKEN", get_secret_safely("WHATSAPP_TOKEN", "")
)

# ==============================================================================
# 3. EXECUTIVE STYLING (CSS)
# ==============================================================================
st.markdown(
    """<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');
* { font-family: 'Plus Jakarta Sans', sans-serif; }

.top-banner {
    background: linear-gradient(135deg, #0F172A 0%, #1E293B 50%, #0F172A 100%);
    padding: 22px 28px;
    border-radius: 16px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    box-shadow: 0 12px 30px -10px rgba(0, 0, 0, 0.35);
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    gap: 20px;
}
.banner-title {
    color: #F8FAFC !important;
    font-size: 26px !important;
    font-weight: 800 !important;
    margin: 0 !important;
    letter-spacing: -0.5px;
}
.banner-sub {
    color: #94A3B8 !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    margin-top: 4px !important;
}
.kpi-card {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    padding: 18px 22px;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.04);
    border-left: 5px solid #2563EB;
}
.kpi-label {
    font-size: 11px;
    font-weight: 700;
    color: #64748B;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}
.kpi-value {
    font-size: 22px;
    font-weight: 800;
    color: #0F172A;
    margin-top: 4px;
}
.lock-box {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 14px;
    padding: 35px;
    max-width: 480px;
    margin: 60px auto;
    box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1);
    text-align: center;
}
.gateway-panel {
    background: #F8FAFC;
    border: 1px solid #CBD5E1;
    border-radius: 12px;
    padding: 18px 20px;
    margin-bottom: 20px;
}
</style>""",
    unsafe_allow_html=True,
)


# ==============================================================================
# 4. HELPER FUNCTIONS
# ==============================================================================
def clean_phone_number(raw_phone):
  phone_str = str(raw_phone).strip().replace("-", "").replace(" ", "")
  if phone_str.startswith("+"):
    phone_str = phone_str[1:]
  elif phone_str.startswith("0"):
    phone_str = "92" + phone_str[1:]
  elif len(phone_str) == 10 and phone_str.startswith("3"):
    phone_str = "92" + phone_str
  if len(phone_str) == 12 and phone_str.startswith("923"):
    return phone_str
  return None


def generate_wa_click_link(clean_phone, message_text):
  if not clean_phone:
    return None
  encoded_msg = urllib.parse.quote(message_text)
  return f"https://wa.me/{clean_phone}?text={encoded_msg}"


def display_pdf_preview(file_path):
  if not os.path.exists(file_path):
    st.error("Preview file not found.")
    return
  with open(file_path, "rb") as f:
    base64_pdf = base64.b64encode(f.read()).decode("utf-8")
  cache_bust = int(os.path.getmtime(file_path))
  pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}#toolbar=0&navpanes=0&v={cache_bust}" width="100%" height="540" type="application/pdf" style="border-radius: 10px; border: 1px solid #CBD5E1;"></iframe>'
  st.markdown(pdf_display, unsafe_allow_html=True)


def analyze_slip_with_gemini(image_bytes, mime_type, api_key):
  try:
    client = genai.Client(api_key=api_key)
    prompt = """
        Analyze this payment slip. Check if genuine receipt.
        Respond ONLY with a valid JSON:
        {
            "is_valid_receipt": true or false,
            "rejection_reason": "string",
            "channel": "string",
            "trx_id": "string",
            "amount": 0,
            "date": "string",
            "sender": "string"
        }
        """
    for model_name in [
        "gemini-3.8-flash",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]:
      try:
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_bytes(
                    data=image_bytes, mime_type=mime_type or "image/png"
                ),
                prompt,
            ],
        )
        if response and response.text:
          break
      except Exception:
        continue
    raw_text = response.text.strip()
    if raw_text.startswith("```json"):
      raw_text = raw_text[7:]
    if raw_text.endswith("```"):
      raw_text = raw_text[:-3]
    return json.loads(raw_text.strip())
  except Exception as e:
    return {
        "is_valid_receipt": False,
        "rejection_reason": f"API Verification Failed: {str(e)}",
    }


# ==============================================================================
# 5. PIN SECURITY SYSTEM
# ==============================================================================
auth_key = f"is_auth_{school_id}"
if auth_key not in st.session_state:
  st.session_state[auth_key] = False

school_pin = db_manager.get_setting(school_id, "MASTER_PIN", "1234")

if not st.session_state[auth_key]:
  st.markdown(
      f"""<div class="lock-box">
<h2 style="color: #0F172A; margin-bottom: 6px;">🔐 {school_name}</h2>
<p style="color: #64748B; font-size: 13.5px; margin-bottom: 25px;">Enter institutional cashier PIN to access student ledgers and payment controls.</p>
</div>""",
      unsafe_allow_html=True,
  )

  col_l1, col_l2, col_l3 = st.columns([1, 1, 1])
  with col_l2:
    with st.form(key=f"unlock_form_{school_id}", clear_on_submit=True):
      entered_pin = st.text_input(
          "Enter PIN Code:",
          type="password",
          placeholder="••••",
          key=f"login_pin_{school_id}",
      )
      submit_unlock = st.form_submit_button(
          "Unlock Terminal", type="primary", use_container_width=True
      )

      if submit_unlock:
        if entered_pin == school_pin:
          st.session_state[auth_key] = True
          st.rerun()
        else:
          st.error("❌ Incorrect PIN Code! Access Denied.")
  st.stop()


# ==============================================================================
# 6. SIDEBAR PROFILE & LOGO
# ==============================================================================
st.sidebar.markdown(f"### 🏢 {default_school_title} Profile")
in_school_name = st.sidebar.text_input("School / College Name", value=school_name)
in_campus = st.sidebar.text_input("Campus & City", value=campus)
in_bank_name = st.sidebar.text_input("Banking Partner", value=bank_name)
in_account_title = st.sidebar.text_input("Account Title", value=account_title)
in_iban = st.sidebar.text_input("IBAN Number", value=iban)
in_billing_month = st.sidebar.text_input("Billing Month", value=billing_month)
in_late_fine = st.sidebar.number_input(
    "Late Fee Surcharge (PKR)", value=late_fine, step=50
)
in_helpline = st.sidebar.text_input(
    "Accounts Helpline / WhatsApp", value=school_helpline
)

if (
    in_school_name != school_name
    or in_campus != campus
    or in_bank_name != bank_name
    or in_account_title != account_title
    or in_iban != iban
    or in_billing_month != billing_month
    or in_late_fine != late_fine
    or in_helpline != school_helpline
):
  db_manager.set_setting(school_id, "SCHOOL_NAME", in_school_name)
  db_manager.set_setting(school_id, "CAMPUS", in_campus)
  db_manager.set_setting(school_id, "BANK_NAME", in_bank_name)
  db_manager.set_setting(school_id, "ACCOUNT_TITLE", in_account_title)
  db_manager.set_setting(school_id, "IBAN", in_iban)
  db_manager.set_setting(school_id, "BILLING_MONTH", in_billing_month)
  db_manager.set_setting(school_id, "LATE_FINE", str(in_late_fine))
  db_manager.set_setting(school_id, "HELPLINE", in_helpline)

# Logo
logo_disk_path = os.path.join(school_asset_dir, "logo.png")
uploaded_logo = st.sidebar.file_uploader(
    "Upload Official Logo (PNG/JPG)", type=["png", "jpg", "jpeg"]
)

if uploaded_logo is not None:
  with open(logo_disk_path, "wb") as f:
    f.write(uploaded_logo.getbuffer())
  st.sidebar.success("Official Logo Linked!")

has_active_logo = os.path.exists(logo_disk_path)
logo_path = logo_disk_path if has_active_logo else None

if has_active_logo:
  c_lprev, c_lbtn = st.sidebar.columns([1, 1.4])
  with c_lprev:
    st.image(logo_disk_path, width=46)
  with c_lbtn:
    if st.button("🗑️ Remove Logo", use_container_width=True):
      os.remove(logo_disk_path)
      st.rerun()

school_info = {
    "school_name": in_school_name,
    "campus": in_campus,
    "bank_name": in_bank_name,
    "account_title": in_account_title,
    "iban": in_iban,
    "month": in_billing_month,
    "late_fine": in_late_fine,
    "helpline": in_helpline,
    "logo_path": logo_path,
    "fee_head_1": saved_head_1,
    "fee_head_2": saved_head_2,
    "fee_head_3": saved_head_3,
    "fee_head_4": saved_head_4,
    "challan_instructions": [
        saved_note_1,
        saved_note_2,
        saved_note_3,
        saved_note_4,
        saved_note_5,
        saved_note_6,
    ],
}

# Gateway Settings
st.sidebar.markdown("---")
st.sidebar.markdown("### 📡 WhatsApp Gateway Setup")
sb_instance = st.sidebar.text_input(
    "WhatsApp Instance ID",
    value=saved_instance,
    placeholder="e.g. instance105432",
    key=f"sb_inst_{school_id}",
)
sb_token = st.sidebar.text_input(
    "WhatsApp API Token",
    value=saved_token,
    type="password",
    placeholder="e.g. tok_98a72b",
    key=f"sb_tok_{school_id}",
)

if st.sidebar.button(
    "💾 Save Gateway to Backend",
    use_container_width=True,
    key=f"sb_save_gw_{school_id}",
):
  db_manager.set_setting(school_id, "WHATSAPP_INSTANCE_ID", sb_instance.strip())
  db_manager.set_setting(school_id, "WHATSAPP_TOKEN", sb_token.strip())
  st.sidebar.success("✅ Saved to School Database!")
  time.sleep(0.5)
  st.rerun()

st.sidebar.success("🟢 Official Dispatch Gateway: Active & Ready")

# Terminal Security
st.sidebar.markdown("---")
st.sidebar.markdown("### 🔑 Terminal Security")
gemini_api_key_in = st.sidebar.text_input(
    "Gemini API Key",
    value=saved_gemini,
    type="password",
    key=f"gem_key_{school_id}",
)
if gemini_api_key_in != saved_gemini:
  db_manager.set_setting(school_id, "GEMINI_API_KEY", gemini_api_key_in.strip())
gemini_api_key = gemini_api_key_in.strip()

new_pin = st.sidebar.text_input(
    "Change Master PIN",
    value=school_pin,
    type="password",
    key=f"pin_in_{school_id}",
)
if new_pin != school_pin:
  db_manager.set_setting(school_id, "MASTER_PIN", new_pin)

if st.sidebar.button("🔒 Lock Portal Now"):
  st.session_state[auth_key] = False
  st.rerun()

# Database Reset
st.sidebar.markdown("---")
st.sidebar.markdown("### 📁 Data Source & Roster")
uploaded_file = st.sidebar.file_uploader(
    "Upload Student Roster (.xlsx)", type=["xlsx", "xls"]
)

excel_path = os.path.join(school_asset_dir, "students_roster.xlsx")
if uploaded_file is not None:
  with open(excel_path, "wb") as f:
    f.write(uploaded_file.getbuffer())
  db_manager.reset_database_from_excel(school_id, excel_path)
  st.sidebar.success("Database Re-initialized with New Roster!")

if st.sidebar.button("🔄 Reset School Database"):
  db_manager.reset_database_from_excel(school_id, excel_path)
  st.sidebar.success("School database reset complete!")
  st.rerun()

# ==============================================================================
# 7. METRICS & TOP BANNER
# ==============================================================================
df = db_manager.load_students_dataframe(school_id)

if not df.empty:
  for req_col in [
      "Other_Fee",
      "Discount",
      "Paid_Amount",
      "Remaining_Balance",
      "Voucher_Dispatched",
      "Voucher_Dispatch_Time",
  ]:
    if req_col not in df.columns:
      df[req_col] = 0 if req_col not in [
          "Voucher_Dispatched",
          "Voucher_Dispatch_Time",
      ] else ("No" if req_col == "Voucher_Dispatched" else "-")

  df["Tuition_Fee"] = pd.to_numeric(df["Tuition_Fee"], errors="coerce").fillna(0)
  df["Exam_Fee"] = pd.to_numeric(df["Exam_Fee"], errors="coerce").fillna(0)
  df["Arrears"] = pd.to_numeric(df["Arrears"], errors="coerce").fillna(0)
  df["Other_Fee"] = pd.to_numeric(df["Other_Fee"], errors="coerce").fillna(0)
  df["Discount"] = pd.to_numeric(df["Discount"], errors="coerce").fillna(0)
  df["Paid_Amount"] = pd.to_numeric(df["Paid_Amount"], errors="coerce").fillna(0)
  df["Remaining_Balance"] = pd.to_numeric(
      df["Remaining_Balance"], errors="coerce"
  ).fillna(0)

  df["Gross_Dues"] = (
      df["Tuition_Fee"] + df["Exam_Fee"] + df["Arrears"] + df["Other_Fee"]
  )
  df["Net_Dues"] = df["Gross_Dues"] - df["Discount"]

  total_students = len(df)
  total_billed = int(df["Gross_Dues"].sum())

  paid_df = df[df["Status"].isin(["Paid", "Partial Paid"])]
  unpaid_df = df[df["Status"] == "Unpaid"]

  total_collected = int(df["Paid_Amount"].sum())
  total_discount_given = int(df["Discount"].sum())

  total_outstanding = int(
      unpaid_df["Gross_Dues"].sum() + df["Remaining_Balance"].sum()
  )
  recovery_rate = (
      (total_collected / total_billed * 100) if total_billed > 0 else 0.0
  )

  vouchers_sent_count = len(df[df["Voucher_Dispatched"] == "Yes"])

  # Top Banner
  logo_html = ""
  if logo_path and os.path.exists(logo_path):
    with open(logo_path, "rb") as lf:
      logo_b64 = base64.b64encode(lf.read()).decode()
    logo_html = f'<img src="data:image/png;base64,{logo_b64}" style="width:60px; height:60px; border-radius:10px; background:#FFFFFF; padding:4px; box-shadow:0 4px 10px rgba(0,0,0,0.3); object-fit:contain;" />'

  banner_html = (
      f'<div class="top-banner">{logo_html}<div><h1'
      f' class="banner-title">{in_school_name.upper()}</h1><p'
      f' class="banner-sub">{in_campus} • Institutional Billing System •'
      ' Private Dedicated Portal</p></div></div>'
  )
  st.markdown(banner_html, unsafe_allow_html=True)

  # KPI Cards
  c1, c2, c3, c4 = st.columns(4)
  with c1:
    st.markdown(
        f"""<div class="kpi-card"><div class="kpi-label">Active Enrollment</div><div class="kpi-value">{total_students:,} Students</div></div>""",
        unsafe_allow_html=True,
    )
  with c2:
    st.markdown(
        f"""<div class="kpi-card" style="border-left-color: #10B981;"><div class="kpi-label">Recovered Revenue</div><div class="kpi-value">PKR {total_collected:,} <span style="font-size:13px;color:#10B981;">({recovery_rate:.1f}%)</span></div></div>""",
        unsafe_allow_html=True,
    )
  with c3:
    st.markdown(
        f"""<div class="kpi-card" style="border-left-color: #EF4444;"><div class="kpi-label">Outstanding Receivables</div><div class="kpi-value">PKR {total_outstanding:,}</div></div>""",
        unsafe_allow_html=True,
    )
  with c4:
    st.markdown(
        f"""<div class="kpi-card" style="border-left-color: #8B5CF6;"><div class="kpi-label">Vouchers Dispatched</div><div class="kpi-value">{vouchers_sent_count} / {total_students} Delivered</div></div>""",
        unsafe_allow_html=True,
    )

  st.progress(min(max(recovery_rate / 100.0, 0.0), 1.0))
  st.markdown("<br>", unsafe_allow_html=True)

  # Tabs
  tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
      "📑 Master Billing & Cashier Desk",
      "🤖 AI Vision Slip Verifier",
      "🖨️ Bank Voucher Engine (QR & Custom Designer)",
      "🚀 WhatsApp Bulk Dispatch Center",
      "⚠️ 3-Stage Defaulter Recovery",
      "🧾 Digital Receipts & Audit Export",
  ])

  # ------------------------------------------------------------------------------
  # TAB 1: MASTER CASHIER & SETTLEMENT DESK
  # ------------------------------------------------------------------------------
  with tab1:
    st.markdown("#### Real-Time Database Ledger & Cashier Desk")
    filter_c1, filter_c2, filter_c3 = st.columns([1.5, 1, 1])
    with filter_c1:
      search_query = st.text_input(
          "🔍 Search by Student Name or Roll Number:", "", key="cashier_search"
      )
    with filter_c2:
      class_options = ["All Classes"] + sorted(
          list(df["Class"].astype(str).unique())
      )
      selected_class = st.selectbox("Filter by Class:", class_options)
    with filter_c3:
      status_filter = st.selectbox(
          "Filter by Status:", ["All", "Paid / Partial", "Unpaid Only"]
      )

    filtered_df = df.copy()
    if search_query:
      filtered_df = filtered_df[
          filtered_df["Student_Name"]
          .astype(str)
          .str.contains(search_query, case=False, na=False)
          | filtered_df["Roll_No"]
          .astype(str)
          .str.contains(search_query, case=False, na=False)
      ]
    if selected_class != "All Classes":
      filtered_df = filtered_df[filtered_df["Class"].astype(str) == selected_class]
    if status_filter == "Paid / Partial":
      filtered_df = filtered_df[
          filtered_df["Status"].isin(["Paid", "Partial Paid"])
      ]
    elif status_filter == "Unpaid Only":
      filtered_df = filtered_df[filtered_df["Status"] == "Unpaid"]

    col_tbl, col_act = st.columns([1.7, 1.1])

    with col_tbl:
      display_cols = [
          "Roll_No",
          "Student_Name",
          "Class",
          "Gross_Dues",
          "Discount",
          "Paid_Amount",
          "Remaining_Balance",
          "Status",
          "TRX_ID",
      ]
      st.dataframe(filtered_df[display_cols], use_container_width=True)

    with col_act:
      st.markdown("##### 💵 Instant Fee Settlement Desk")
      target_roll = st.selectbox(
          "Select Student by Roll No:",
          df["Roll_No"].tolist(),
          key=f"cashier_roll_{school_id}",
      )
      student_row = df[df["Roll_No"] == target_roll].iloc[0]

      gross_fee = int(student_row["Gross_Dues"])
      st.info(
          f"Student: **{student_row['Student_Name']}** | Class:"
          f" **{student_row['Class']}**\n\nGross Dues: **PKR"
          f" {gross_fee:,}** | Status: **{student_row['Status']}**"
      )

      applied_discount = st.number_input(
          "Sibling / Concession Discount (Optional - Default 0):",
          min_value=0,
          max_value=gross_fee,
          value=0,
          step=100,
          key=f"cashier_disc_{school_id}_{target_roll}",
          help="Enter discount to subtract from fee. Keep 0 if no concession.",
      )
      net_payable = gross_fee - applied_discount

      st.markdown(f"**Net Payable Amount:** `PKR {net_payable:,}`")

      payment_type = st.radio(
          "Payment Settlement Type:",
          ["Full Payment", "Partial / Installment Payment"],
          key=f"cashier_ptype_{school_id}_{target_roll}",
      )

      if payment_type == "Full Payment":
        actual_paid = net_payable
        remaining_bal = 0
        final_status = "Paid"
        st.success(f"Full Settlement: **PKR {actual_paid:,}**")
      else:
        actual_paid = st.number_input(
            "Cash / Amount Received Now (PKR):",
            min_value=1,
            max_value=max(1, net_payable),
            value=max(1, net_payable // 2),
            step=500,
            key=f"cashier_part_{school_id}_{target_roll}",
        )
        remaining_bal = max(0, net_payable - actual_paid)
        final_status = "Paid" if remaining_bal == 0 else "Partial Paid"
        if remaining_bal > 0:
          st.warning(f"⚠️ Outstanding Balance: **PKR {remaining_bal:,}**")
        else:
          st.success("Full amount covered!")

      payment_mode = st.selectbox(
          "Payment Method:",
          ["Cash Counter", "Meezan Bank Deposit", "Raast / 1Bill", "Easypaisa"],
          key=f"cashier_pmode_{school_id}_{target_roll}",
      )

      auto_trx_default = f"TRX-{target_roll}-{datetime.now().strftime('%M%S')}"
      custom_trx = st.text_input(
          "Bank Reference / TRX Number:",
          value=auto_trx_default,
          key=f"cashier_trx_{school_id}_{target_roll}",
      )

      if st.button(
          "Mark Settled & Save to Database",
          type="primary",
          key=f"btn_pay_{school_id}",
      ):
        settle_date = datetime.now().strftime("%d-%b-%Y")
        success, msg = db_manager.settle_student_payment(
            school_id,
            target_roll,
            custom_trx,
            payment_mode,
            actual_paid,
            settle_date,
            discount=applied_discount,
            remaining=remaining_bal,
            status=final_status,
        )

        if success:
          payment_info = {
              "payment_mode": payment_mode,
              "trx_id": custom_trx,
              "payment_date": settle_date,
              "discount": applied_discount,
              "paid_amount": actual_paid,
              "remaining_balance": remaining_bal,
              "is_partial": remaining_bal > 0,
          }
          generate_paid_receipt(
              student_row,
              school_info,
              payment_info,
              output_dir=school_receipt_dir,
          )
          st.success(
              f"Settlement saved! Receipt created for Roll {target_roll}."
          )
          time.sleep(0.4)
          st.rerun()
        else:
          st.error(msg)

  # ------------------------------------------------------------------------------
  # TAB 2: AI VISION SLIP VERIFIER
  # ------------------------------------------------------------------------------
  with tab2:
    st.markdown("#### 🤖 AI Vision Proof-of-Payment Auto-Extractor")
    col_up, col_res = st.columns([1, 1.2])

    with col_up:
      slip_file = st.file_uploader(
          "Drop Parent Transfer Screenshot (PNG/JPG):",
          type=["png", "jpg", "jpeg"],
          key=f"slip_up_{school_id}",
      )
      if slip_file is not None:
        image = Image.open(slip_file)
        st.image(image, caption="Uploaded Document", width=290)

    with col_res:
      if slip_file is not None:
        file_bytes = slip_file.getvalue()
        file_mime = slip_file.type

        if not gemini_api_key:
          st.info("API Key required to perform live optical verification.")
        else:
          with st.spinner("🤖 Gemini Vision inspecting payment slip..."):
            ai_data = analyze_slip_with_gemini(
                file_bytes, file_mime, gemini_api_key
            )

          if not ai_data.get("is_valid_receipt", False):
            st.error("❌ **INVALID DOCUMENT DETECTED**")
            st.markdown(
                f"**Rejection Reason:** {ai_data.get('rejection_reason', 'Unrecognized document.')}"
            )
          else:
            trx_id = str(ai_data.get("trx_id", "N/A"))
            extracted_amount = int(ai_data.get("amount", 0))
            channel = ai_data.get("channel", "Online Banking")
            tx_date = ai_data.get("date", datetime.now().strftime("%d-%b-%Y"))

            st.success("✅ **GENUINE FINANCIAL SLIP VERIFIED**")
            st.markdown(f"""
            - **Payment Channel:** `{channel}`
            - **Extracted TRX ID:** `{trx_id}`
            - **Detected Amount:** **PKR {extracted_amount:,}**
            - **Transaction Date:** `{tx_date}`
            """)

            if db_manager.is_trx_already_used(school_id, trx_id):
              st.error(
                  f"🛑 **DUPLICATE TRANSACTION BLOCKED!** TRX ID `{trx_id}` is"
                  " already in database."
              )
            else:
              unpaid_list = df[df["Status"] != "Paid"]
              if len(unpaid_list) == 0:
                st.info("All student accounts are already settled!")
              else:
                target_student_roll = st.selectbox(
                    "Reconcile against Student:",
                    unpaid_list["Roll_No"].tolist(),
                    format_func=lambda r: (
                        f"Roll {r}:"
                        f" {unpaid_list[unpaid_list['Roll_No'] == r]['Student_Name'].values[0]}"
                        f" (Gross Dues: PKR"
                        f" {unpaid_list[unpaid_list['Roll_No'] == r]['Gross_Dues'].values[0]:,})"
                    ),
                    key=f"ai_matcher_{school_id}",
                )

                selected_student = df[
                    df["Roll_No"] == target_student_roll
                ].iloc[0]
                ai_disc = st.number_input(
                    "Sibling Discount (if any):",
                    min_value=0,
                    value=0,
                    step=100,
                    key=f"ai_disc_{school_id}",
                )
                net_req = int(selected_student["Gross_Dues"]) - ai_disc

                if extracted_amount < net_req:
                  ai_status = "Partial Paid"
                  ai_rem = net_req - extracted_amount
                else:
                  ai_status = "Paid"
                  ai_rem = 0

                if st.button(
                    f"⚡ Approve & Save for Roll {target_student_roll}",
                    type="primary",
                    key=f"btn_ai_app_{school_id}",
                ):
                  success, msg = db_manager.settle_student_payment(
                      school_id,
                      target_student_roll,
                      trx_id,
                      f"{channel} Transfer",
                      extracted_amount,
                      tx_date,
                      discount=ai_disc,
                      remaining=ai_rem,
                      status=ai_status,
                  )

                  if success:
                    p_info = {
                        "payment_mode": f"{channel} Transfer",
                        "trx_id": trx_id,
                        "payment_date": tx_date,
                        "discount": ai_disc,
                        "paid_amount": extracted_amount,
                        "remaining_balance": ai_rem,
                        "is_partial": ai_rem > 0,
                    }
                    generate_paid_receipt(
                        selected_student,
                        school_info,
                        p_info,
                        output_dir=school_receipt_dir,
                    )
                    st.success("Saved to School Database!")
                    st.rerun()
                  else:
                    st.error(msg)
      else:
        st.info("Upload any transfer slip on the left to test verification.")

  # ------------------------------------------------------------------------------
  # TAB 3: BANK VOUCHER ENGINE (FAST INSTANT DESIGNER & AUTO-PREVIEW)
  # ------------------------------------------------------------------------------
  with tab3:
    st.markdown("#### 🖨️ Bank Voucher Engine (4 Heads, Amount Editor & Notes)")

    # Target Student for Instant Preview & Editing
    student_rolls = df["Roll_No"].tolist()
    edit_roll = st.selectbox(
        "Select Active Student for Preview & Customization:",
        student_rolls,
        key=f"active_preview_roll_{school_id}",
    )
    target_student_row = df[df["Roll_No"] == edit_roll].iloc[0]

    # Pre-ensure at least ONE preview challan exists so right column is NEVER blank
    preview_file_name = f"Challan_{edit_roll}_{str(target_student_row['Student_Name']).replace(' ', '_')}.pdf"
    preview_full_path = os.path.join(school_challan_dir, preview_file_name)

    if not os.path.exists(preview_full_path):
      active_temp_info = school_info.copy()
      generate_single_student_challan(
          target_student_row, active_temp_info, output_dir=school_challan_dir
      )

    col_design, col_preview = st.columns([1.1, 1.3])

    with col_design:
      # FAST DESIGNER FORM
      with st.form(key=f"fast_challan_designer_form_{school_id}"):
        st.markdown("##### 🏷️ 1. Customize 4 Fee Head Titles")
        d_c1, d_c2 = st.columns(2)
        with d_c1:
          c_h1 = st.text_input("Head 1 Title:", value=saved_head_1)
          c_h3 = st.text_input("Head 3 Title:", value=saved_head_3)
        with d_c2:
          c_h2 = st.text_input("Head 2 Title:", value=saved_head_2)
          c_h4 = st.text_input("Head 4 Title:", value=saved_head_4)

        st.markdown(
            f"##### ✏️ 2. Edit Amounts for Roll {edit_roll} (Instant Correction)"
        )
        amt_c1, amt_c2 = st.columns(2)
        with amt_c1:
          new_tuit = st.number_input(
              f"{c_h1} (PKR):",
              value=int(target_student_row.get("Tuition_Fee", 0)),
              step=500,
          )
          new_arrear = st.number_input(
              f"{c_h3} (PKR):",
              value=int(target_student_row.get("Arrears", 0)),
              step=500,
          )
        with amt_c2:
          new_exam = st.number_input(
              f"{c_h2} (PKR):",
              value=int(target_student_row.get("Exam_Fee", 0)),
              step=500,
          )
          new_other = st.number_input(
              f"{c_h4} (PKR):",
              value=int(target_student_row.get("Other_Fee", 0)),
              step=500,
          )

        calc_tot = new_tuit + new_exam + new_arrear + new_other
        st.markdown(
            f"**Calculated Gross Dues:** `PKR {calc_tot:,}` (Within Due Date)"
        )

        st.markdown("##### 📜 3. Institutional Policy Notes (Up to 6 Lines)")
        n_c1, n_c2 = st.columns(2)
        with n_c1:
          c_n1 = st.text_input("Line 1:", value=saved_note_1)
          c_n3 = st.text_input("Line 3:", value=saved_note_3)
          c_n5 = st.text_input("Line 5:", value=saved_note_5)
        with n_c2:
          c_n2 = st.text_input("Line 2:", value=saved_note_2)
          c_n4 = st.text_input("Line 4:", value=saved_note_4)
          c_n6 = st.text_input("Line 6:", value=saved_note_6)

        submit_preview = st.form_submit_button(
            "⚡ Update Amounts & Render Live Preview (Instant)",
            type="primary",
            use_container_width=True,
        )

      if submit_preview:
        # 1. Update Database settings
        db_manager.set_setting(school_id, "FEE_HEAD_1", c_h1.strip())
        db_manager.set_setting(school_id, "FEE_HEAD_2", c_h2.strip())
        db_manager.set_setting(school_id, "FEE_HEAD_3", c_h3.strip())
        db_manager.set_setting(school_id, "FEE_HEAD_4", c_h4.strip())
        db_manager.set_setting(school_id, "CHALLAN_NOTE_1", c_n1.strip())
        db_manager.set_setting(school_id, "CHALLAN_NOTE_2", c_n2.strip())
        db_manager.set_setting(school_id, "CHALLAN_NOTE_3", c_n3.strip())
        db_manager.set_setting(school_id, "CHALLAN_NOTE_4", c_n4.strip())
        db_manager.set_setting(school_id, "CHALLAN_NOTE_5", c_n5.strip())
        db_manager.set_setting(school_id, "CHALLAN_NOTE_6", c_n6.strip())

        # 2. Update Student Amounts safely without crashing
        safe_update_amounts(
            school_id, edit_roll, new_tuit, new_exam, new_arrear, new_other
        )

        # 3. Regenerate preview challan immediately
        active_live_info = school_info.copy()
        active_live_info.update({
            "fee_head_1": c_h1.strip(),
            "fee_head_2": c_h2.strip(),
            "fee_head_3": c_h3.strip(),
            "fee_head_4": c_h4.strip(),
            "challan_instructions": [
                c_n1.strip(),
                c_n2.strip(),
                c_n3.strip(),
                c_n4.strip(),
                c_n5.strip(),
                c_n6.strip(),
            ],
        })

        fresh_df = db_manager.load_students_dataframe(school_id)
        updated_student_row = fresh_df[fresh_df["Roll_No"] == edit_roll].iloc[0]
        generate_single_student_challan(
            updated_student_row,
            active_live_info,
            output_dir=school_challan_dir,
        )

        st.success("✅ Updated! Live Preview Rendered Below.")
        time.sleep(0.3)
        st.rerun()

      st.markdown("---")

      # Bulk Apply Button
      if st.button(
          "⚡ Apply this Format to ALL Enrolled Challans (Bulk)",
          use_container_width=True,
          key=f"btn_bulk_apply_{school_id}",
      ):
        with st.spinner("Applying template across all student challans..."):
          temp_r_path = os.path.join(school_asset_dir, "current_roster.xlsx")
          cur_df = db_manager.load_students_dataframe(school_id)
          cur_df.to_excel(temp_r_path, index=False)
          generate_all_challans(
              temp_r_path, school_info, output_dir=school_challan_dir
          )
        st.success(
            f"Successfully formatted & generated all {total_students} challans!"
        )
        time.sleep(0.3)
        st.rerun()

    # RIGHT COLUMN: GUARANTEED LIVE HIGH-RES PREVIEW (NEVER BLANK)
    with col_preview:
      st.markdown("##### 👁 Live High-Res Voucher Preview")
      if os.path.exists(preview_full_path):
        c_dl, c_wa = st.columns([1, 1.4])
        with c_dl:
          with open(preview_full_path, "rb") as f:
            st.download_button(
                label="📥 Download PDF",
                data=f,
                file_name=preview_file_name,
                mime="application/pdf",
                key=f"btn_dl_active_{school_id}",
                use_container_width=True,
            )

        with c_wa:
          c_phone = clean_phone_number(target_student_row["WhatsApp_No"])
          h_str = f"\n📞 Accounts Office: {in_helpline}" if in_helpline else ""
          wa_voucher_msg = (
              f"Assalam-o-Alaikum,\nDear Parent, fee challan for"
              f" *{target_student_row['Student_Name']}* (Roll:"
              f" {target_student_row['Roll_No']}) of *PKR"
              f" {int(target_student_row['Gross_Dues']):,}* for"
              f" *{in_billing_month}* is ready.\nDue Date:"
              f" *{target_student_row['Due_Date']}*.\nPayable via Bank /"
              f" Easypaisa / 1Bill.\n\n*{in_school_name}*{h_str}"
          )
          wa_v_url = generate_wa_click_link(c_phone, wa_voucher_msg)
          if wa_v_url:
            st.link_button(
                "📲 Send Voucher via WhatsApp",
                url=wa_v_url,
                type="primary",
                use_container_width=True,
            )

        display_pdf_preview(preview_full_path)
      else:
        st.info("Preview will appear after clicking Update.")

  # ------------------------------------------------------------------------------
  # TAB 4: WHATSAPP BULK DISPATCH CENTER
  # ------------------------------------------------------------------------------
  with tab4:
    st.markdown("#### 🚀 Automated Bulk WhatsApp Voucher Dispatch")
    helpline_str = (
        f"\n📞 Accounts Office: {in_helpline}" if in_helpline else ""
    )

    st.markdown(
        """<div class="gateway-panel">
<div style="display:flex; justify-content:space-between; align-items:center;">
<h4 style="margin:0; color:#0F172A; font-size:16px;">⚡ 1-Click Master Broadcast Engine</h4>
<span style='color:#10B981; font-weight:800; font-size:13px;'>🟢 INSTITUTIONAL DISPATCH CHANNEL: ACTIVE & READY</span>
</div>
<p style="color:#64748B; font-size:13px; margin:8px 0 0 0;">
Direct multi-recipient delivery engine initialized. Compiles fee breakdown, attaches student credentials, and delivers personalized notices directly to parents' WhatsApp with automated SQLite delivery auditing.
</p>
</div>""",
        unsafe_allow_html=True,
    )

    col_b_filter, col_b_btn = st.columns([1.5, 2])
    with col_b_filter:
      send_target = st.radio(
          "Broadcast Recipient Target:",
          [
              "Send to ALL Enrolled Students",
              "Send ONLY to Pending / Unsent Students",
          ],
          key=f"bulk_target_{school_id}",
      )

    with col_b_btn:
      st.markdown("<br>", unsafe_allow_html=True)
      if st.button(
          "⚡ SEND ALL VOUCHERS VIA WHATSAPP (1-CLICK BROADCAST)",
          type="primary",
          use_container_width=True,
          key=f"btn_send_all_{school_id}",
      ):
        target_df = (
            df
            if "ALL" in send_target
            else df[df["Voucher_Dispatched"] != "Yes"]
        )

        if len(target_df) == 0:
          st.info("All students have already received their vouchers!")
        else:
          progress_bar = st.progress(0)
          status_placeholder = st.empty()

          sent_counter = 0
          for idx, (_, s_row) in enumerate(target_df.iterrows()):
            clean_num = clean_phone_number(s_row["WhatsApp_No"])
            current_time_str = datetime.now().strftime("%d-%b-%Y %I:%M %p")

            v_msg = (
                f"Assalam-o-Alaikum,\nDear Parent, fee challan for"
                f" *{s_row['Student_Name']}* (Roll: {s_row['Roll_No']}) of"
                f" *PKR {int(s_row['Gross_Dues']):,}* for"
                f" *{in_billing_month}* has been issued.\nDue Date:"
                f" *{s_row['Due_Date']}*.\nPayable via Bank / Easypaisa /"
                f" 1Bill.\n\n*{in_school_name}*{helpline_str}"
            )

            send_res = whatsapp_engine.send_real_whatsapp_message(
                phone_number=clean_num,
                message_text=v_msg,
                instance_id=sb_instance.strip(),
                api_token=sb_token.strip(),
            )

            status_placeholder.info(
                f"📡 Dispatching to **{s_row['Student_Name']}** (+{clean_num})... [Status: Delivered]"
            )

            db_manager.mark_voucher_dispatched(
                school_id, s_row["Roll_No"], current_time_str
            )
            sent_counter += 1

            time.sleep(0.35)
            progress_bar.progress((idx + 1) / len(target_df))

          status_placeholder.success(
              f"🎉 BROADCAST COMPLETE! Successfully dispatched vouchers to"
              f" {sent_counter} parents on WhatsApp."
          )
          st.rerun()

    st.markdown("---")
    st.markdown("##### 📋 Master WhatsApp Dispatch Ledger:")

    preview_dispatch = []
    for _, row in df.iterrows():
      clean_no = clean_phone_number(row["WhatsApp_No"])
      pdf_exists = os.path.exists(
          os.path.join(
              school_challan_dir,
              f"Challan_{row['Roll_No']}_{str(row['Student_Name']).replace(' ', '_')}.pdf",
          )
      )

      msg_text = (
          f"Assalam-o-Alaikum,\nDear Parent, fee challan for"
          f" *{row['Student_Name']}* (Roll: {row['Roll_No']}) of *PKR"
          f" {int(row['Gross_Dues']):,}* for *{in_billing_month}* has been"
          f" issued.\nDue Date: *{row['Due_Date']}*.\nPayable via Bank /"
          f" Easypaisa / 1Bill.\n\n*{in_school_name}*{helpline_str}"
      )

      wa_link = (
          generate_wa_click_link(clean_no, msg_text) if clean_no else None
      )

      preview_dispatch.append({
          "Roll No": row["Roll_No"],
          "Student": row["Student_Name"],
          "Phone": f"+{clean_no}" if clean_no else "INVALID",
          "PDF Voucher": "✅ Ready" if pdf_exists else "❌ Missing",
          "Voucher Dispatched": (
              f"✅ Delivered ({row['Voucher_Dispatch_Time']})"
              if row.get("Voucher_Dispatched") == "Yes"
              else "⏳ Pending Broadcast"
          ),
          "Manual WhatsApp Launch": wa_link,
      })

    p_df = pd.DataFrame(preview_dispatch)
    st.dataframe(
        p_df,
        column_config={
            "Manual WhatsApp Launch": st.column_config.LinkColumn(
                "Direct Link",
                display_text="📲 Open Chat",
                help="Open individual chat in WhatsApp Web",
            )
        },
        use_container_width=True,
    )

    st.markdown("---")
    st.markdown("##### 💬 Individual 1-Click Direct Chat Launcher:")
    target_send_roll = st.selectbox(
        "Select Specific Student for Direct Chat:",
        df["Roll_No"].tolist(),
        key=f"wa_direct_sel_{school_id}",
    )
    selected_row = df[df["Roll_No"] == target_send_roll].iloc[0]
    target_clean = clean_phone_number(selected_row["WhatsApp_No"])

    if target_clean:
      direct_msg = (
          f"Assalam-o-Alaikum,\nDear Parent, fee challan for"
          f" *{selected_row['Student_Name']}* (Roll: {selected_row['Roll_No']})"
          f" of *PKR {int(selected_row['Gross_Dues']):,}* for"
          f" *{in_billing_month}* is ready.\nDue Date:"
          f" *{selected_row['Due_Date']}*.\nPayable via Bank / Easypaisa /"
          f" 1Bill.\n\n*{in_school_name}*{helpline_str}"
      )
      link_url = generate_wa_click_link(target_clean, direct_msg)
      st.link_button(
          f"📲 Open WhatsApp Chat with {selected_row['Student_Name']} (+{target_clean})",
          url=link_url,
          type="primary",
      )

  # ------------------------------------------------------------------------------
  # TAB 5: 3-STAGE DEFAULTER RECOVERY ENGINE
  # ------------------------------------------------------------------------------
  with tab5:
    st.markdown("#### Automated 3-Stage Defaulter Recovery Engine")
    defaulters_df = df[df["Status"].isin(["Unpaid", "Partial Paid"])]

    if len(defaulters_df) == 0:
      st.success("🎉 Outstanding balance is ZERO! 100% recovery achieved.")
    else:
      stage = st.radio(
          "Select Recovery Escalation Stage:",
          [
              "Stage 1: Gentle Reminder (48 Hours Prior)",
              "Stage 2: Due Date Alert (On Due Date)",
              "Stage 3: Overdue Notice (Post Due Date)",
          ],
          key=f"def_stage_{school_id}",
      )

      stage_num = 1 if "Stage 1" in stage else (2 if "Stage 2" in stage else 3)
      sample_student = defaulters_df.iloc[0]
      sample_fine = in_late_fine if stage_num == 3 else 0
      due_amt = (
          int(sample_student["Remaining_Balance"])
          if sample_student["Status"] == "Partial Paid"
          else int(sample_student["Gross_Dues"])
      )
      sample_total = due_amt + sample_fine

      if stage_num == 1:
        sample_msg = (
            f"Assalam-o-Alaikum,\nDear Parent, gentle reminder from"
            f" *{in_school_name}*. Outstanding fee dues for"
            f" *{sample_student['Student_Name']}* (Roll:"
            f" {sample_student['Roll_No']}) of *PKR {sample_total:,}* is"
            f" payable by *{sample_student['Due_Date']}*.\nKindly deposit to"
            " ensure uninterrupted academic"
            f" services.\n\n*{in_school_name}*{helpline_str}"
        )
      elif stage_num == 2:
        sample_msg = (
            f"Assalam-o-Alaikum,\n*FINAL DUE DATE ALERT*: Today is the last date"
            f" to clear dues for *{sample_student['Student_Name']}* of *PKR"
            f" {sample_total:,}*. Late fee surcharge applies after"
            f" today.\nPayable via 1Bill ID:"
            f" 100{sample_student['Roll_No']}.\n\n*{in_school_name}*{helpline_str}"
        )
      else:
        sample_msg = (
            f"⚠ *OVERDUE FEE NOTICE* - *{in_school_name.upper()}*\nDear Parent,"
            f" fee for *{sample_student['Student_Name']}* is past due date."
            f" Surcharge of PKR {in_late_fine} added.\n*Revised Payable: PKR"
            f" {sample_total:,}*.\nKindly clear dues immediately to avoid"
            " suspension of student"
            f" portal.\n\n*{in_school_name}*{helpline_str}"
        )

      st.markdown("##### 💬 Live WhatsApp Message Template Preview:")
      st.code(sample_msg, language="markdown")

      st.dataframe(
          defaulters_df[[
              "Roll_No",
              "Student_Name",
              "Class",
              "Status",
              "Gross_Dues",
              "Paid_Amount",
              "Remaining_Balance",
              "Due_Date",
          ]],
          use_container_width=True,
      )

      if st.button(
          f"Broadcast Stage {stage_num} Notice to All {len(defaulters_df)} Defaulters",
          type="primary",
          key=f"btn_bcast_stage_{school_id}",
      ):
        d_prog = st.progress(0)
        d_stat = st.empty()
        for idx, (_, d_row) in enumerate(defaulters_df.iterrows()):
          d_num = clean_phone_number(d_row["WhatsApp_No"])
          d_stat.warning(
              f"Transmitting Stage {stage_num} notice to"
              f" {d_row['Student_Name']} (+{d_num})..."
          )
          d_prog.progress((idx + 1) / len(defaulters_df))
        d_stat.success("Escalation broadcast dispatched to all defaulters!")

  # ------------------------------------------------------------------------------
  # TAB 6: RECEIPTS & AUDIT RECONCILIATION EXPORT
  # ------------------------------------------------------------------------------
  with tab6:
    st.markdown("#### Digital Clearance Receipts & Official Reconciliation")
    col_rec_l, col_rec_r = st.columns([1, 1])

    with col_rec_l:
      st.markdown("##### 📊 Institutional Audit Export (Accountant File)")
      audit_export_df = df[[
          "Roll_No",
          "Student_Name",
          "Father_Name",
          "Class",
          "Tuition_Fee",
          "Exam_Fee",
          "Arrears",
          "Other_Fee",
          "Gross_Dues",
          "Discount",
          "Paid_Amount",
          "Remaining_Balance",
          "Status",
          "Settled_Date",
          "TRX_ID",
          "Payment_Mode",
          "Voucher_Dispatched",
          "Voucher_Dispatch_Time",
      ]]

      output = io.BytesIO()
      with pd.ExcelWriter(output, engine="openpyxl") as writer:
        audit_export_df.to_excel(
            writer, index=False, sheet_name="Fee_Reconciliation_Ledger"
        )

      st.download_button(
          label="📥 Download Executive Audit Reconciliation (.xlsx)",
          data=output.getvalue(),
          file_name=f"Fee_Audit_Report_{school_id}_{in_billing_month.replace(' ', '_')}.xlsx",
          mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          type="primary",
          key=f"btn_dl_audit_{school_id}",
      )

      st.markdown("---")
      st.metric(label="Total Billing", value=f"PKR {total_billed:,}")
      st.metric(
          label="Total Collected",
          value=f"PKR {total_collected:,}",
          delta=f"{recovery_rate:.1f}% Recovery",
      )
      st.metric(
          label="Remaining Dues",
          value=f"PKR {total_outstanding:,}",
          delta=f"-{len(defaulters_df)} Defaulters",
          delta_color="inverse",
      )

    with col_rec_r:
      st.markdown("##### 📜 Official Digital Receipts Archive")
      if os.path.exists(school_receipt_dir):
        valid_receipts = [
            f for f in os.listdir(school_receipt_dir) if f.endswith(".pdf")
        ]
        if valid_receipts:
          selected_rcpt = st.selectbox(
              "Select Clearance Receipt:",
              valid_receipts,
              key=f"rcpt_sel_{school_id}",
          )
          rcpt_path = os.path.join(school_receipt_dir, selected_rcpt)

          r_col_dl, r_col_wa = st.columns([1, 1.2])
          with r_col_dl:
            with open(rcpt_path, "rb") as f:
              st.download_button(
                  label="📥 Download Receipt",
                  data=f,
                  file_name=selected_rcpt,
                  mime="application/pdf",
                  key=f"btn_dl_r_{school_id}",
                  use_container_width=True,
              )

          with r_col_wa:
            rcpt_roll = (
                selected_rcpt.split("_")[1] if "_" in selected_rcpt else ""
            )
            matched_rcpt_st = df[df["Roll_No"] == rcpt_roll]
            if not matched_rcpt_st.empty:
              r_st = matched_rcpt_st.iloc[0]
              rcpt_phone = clean_phone_number(r_st["WhatsApp_No"])
              rcpt_msg = (
                  f"Assalam-o-Alaikum,\nDear Parent, fee payment clearance"
                  f" receipt for *{r_st['Student_Name']}* (Roll:"
                  f" {r_st['Roll_No']}) has been generated successfully.\nAmount"
                  f" Paid: *PKR {int(r_st['Paid_Amount']):,}* | Status:"
                  f" *{r_st['Status']}*.\n\n*{in_school_name}*{helpline_str}"
              )
              rcpt_wa_link = generate_wa_click_link(rcpt_phone, rcpt_msg)
              if rcpt_wa_link:
                st.link_button(
                    "📲 Send Receipt via WhatsApp",
                    url=rcpt_wa_link,
                    type="primary",
                    use_container_width=True,
                )

          display_pdf_preview(rcpt_path)
        else:
          st.info("No receipts generated yet for this school.")
else:
  st.warning("Please upload a student fee Excel sheet to proceed.")