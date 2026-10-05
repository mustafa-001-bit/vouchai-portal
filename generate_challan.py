import os
from datetime import datetime
import pandas as pd
import qrcode
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

QR_TEMP_DIR = "temp_qr_codes"
os.makedirs(QR_TEMP_DIR, exist_ok=True)


def generate_qr_code(data_str, file_name):
  qr_path = os.path.join(QR_TEMP_DIR, file_name)
  qr = qrcode.QRCode(version=1, box_size=3, border=1)
  qr.add_data(data_str)
  qr.make(fit=True)
  img = qr.make_image(fill_color="#0F172A", back_color="white")
  img.save(qr_path)
  return qr_path


def generate_single_student_challan(student_data, school_info, output_dir="generated_challans"):
  os.makedirs(output_dir, exist_ok=True)

  roll = student_data["Roll_No"]
  student_name = str(student_data["Student_Name"])
  file_name = f"Challan_{roll}_{student_name.replace(' ', '_')}.pdf"
  file_path = os.path.join(output_dir, file_name)

  # Financials across 4 Heads
  tuition = int(student_data.get("Tuition_Fee", 0))
  exam = int(student_data.get("Exam_Fee", 0))
  arrears = int(student_data.get("Arrears", 0))
  other_fee = int(student_data.get("Other_Fee", 0))
  discount = int(student_data.get("Discount", 0))

  total_gross = tuition + exam + arrears + other_fee
  total_due = max(0, total_gross - discount)
  late_fine = int(school_info.get("late_fine", 300))
  after_due = total_due + late_fine

  # Dynamic 4 Fee Head Labels
  head_1 = school_info.get("fee_head_1", "Tuition Fee")
  head_2 = school_info.get("fee_head_2", "Examination Fee")
  head_3 = school_info.get("fee_head_3", "Previous Arrears")
  head_4 = school_info.get("fee_head_4", "Stationery / Other Charges")

  # 5 to 6 Instructions for Blank Area
  raw_instructions = school_info.get("challan_instructions", [])
  instructions = [inst for inst in raw_instructions if inst and str(inst).strip()]
  if not instructions:
    instructions = [
        "1. Fee must be deposited on or before the due date.",
        "2. Payment accepted via 1Bill, Mobile Banking & Partner Branches.",
        "3. Late fee surcharge applies strictly after the due date.",
        "4. Fee once paid is non-refundable and non-transferable.",
        "5. Keep this stamped student voucher for institutional records.",
    ]

  # Generate 1Bill QR Code
  qr_data = f"1BILL-PAY:RO-{roll}:AMT-{total_due}:EXP-{student_data.get('Due_Date', '10-Oct-2026')}"
  qr_file = f"qr_{roll}.png"
  qr_image_path = generate_qr_code(qr_data, qr_file)

  logo_path = school_info.get("logo_path")
  has_logo = bool(logo_path and os.path.exists(logo_path))

  c = canvas.Canvas(file_path, pagesize=landscape(A4))
  page_w, page_h = landscape(A4)

  copies = [
      ("BANK COPY", "#1E293B"),
      ("SCHOOL COPY", "#0F766E"),
      ("ACCOUNTS COPY", "#4338CA"),
      ("STUDENT COPY", "#334155"),
  ]

  margin_x = 14
  margin_y = 14
  usable_w = page_w - (margin_x * 2)
  col_w = usable_w / 4.0
  col_h = page_h - (margin_y * 2)

  for i, (copy_title, header_bg) in enumerate(copies):
    col_x = margin_x + (i * col_w)

    # Outer border of copy
    c.setStrokeColor(HexColor("#CBD5E1"))
    c.setLineWidth(0.8)
    c.roundRect(col_x + 3, margin_y, col_w - 6, col_h, 4, fill=0, stroke=1)

    if i > 0:
      c.setStrokeColor(HexColor("#94A3B8"))
      c.setLineWidth(0.6)
      c.setDash(2, 3)
      c.line(col_x, margin_y + 10, col_x, page_h - margin_y - 10)
      c.setDash()

    # Copy Top Header Bar
    c.setFillColor(HexColor(header_bg))
    c.roundRect(col_x + 3, page_h - margin_y - 22, col_w - 6, 22, 3, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont("Helvetica-Bold", 8.5)
    c.drawCentredString(col_x + (col_w / 2), page_h - margin_y - 15, copy_title)

    # Institution Branding
    cur_y = page_h - margin_y - 32
    if has_logo:
      try:
        c.drawImage(
            logo_path,
            col_x + 8,
            cur_y - 22,
            width=22,
            height=22,
            preserveAspectRatio=True,
            mask="auto",
        )
      except Exception:
        c.drawImage(
            logo_path,
            col_x + 8,
            cur_y - 22,
            width=22,
            height=22,
            preserveAspectRatio=True,
        )

      c.setFillColor(HexColor("#0F172A"))
      c.setFont("Helvetica-Bold", 7.5)
      c.drawString(col_x + 34, cur_y - 10, str(school_info.get("school_name", ""))[:28].upper())
      c.setFillColor(HexColor("#64748B"))
      c.setFont("Helvetica", 6.2)
      c.drawString(col_x + 34, cur_y - 20, str(school_info.get("campus", ""))[:30])
    else:
      c.setFillColor(HexColor("#0F172A"))
      c.setFont("Helvetica-Bold", 8.2)
      c.drawCentredString(
          col_x + (col_w / 2), cur_y - 10, str(school_info.get("school_name", ""))[:32].upper()
      )
      c.setFillColor(HexColor("#64748B"))
      c.setFont("Helvetica", 6.5)
      c.drawCentredString(
          col_x + (col_w / 2), cur_y - 20, str(school_info.get("campus", ""))[:36]
      )

    # Bank / Billing Details Box
    cur_y -= 38
    c.setFillColor(HexColor("#F8FAFC"))
    c.setStrokeColor(HexColor("#E2E8F0"))
    c.roundRect(col_x + 6, cur_y - 32, col_w - 12, 32, 3, fill=1, stroke=1)

    c.setFillColor(HexColor("#334155"))
    c.setFont("Helvetica-Bold", 6.8)
    c.drawString(col_x + 10, cur_y - 10, f"Bank: {school_info.get('bank_name', 'Meezan Bank')[:26]}")
    c.setFont("Helvetica", 6.5)
    c.drawString(
        col_x + 10, cur_y - 19, f"IBAN: {school_info.get('iban', 'PK36MEZN0001020104829101')[:26]}"
    )
    c.drawString(
        col_x + 10, cur_y - 28, f"Billing Month: {school_info.get('month', 'October 2026')}"
    )

    # Student Particulars
    cur_y -= 44
    student_meta = [
        ("Challan No:", f"CH-2026-{roll}"),
        ("Roll Number:", str(roll)),
        ("Student Name:", student_name[:20]),
        ("Father Name:", str(student_data.get("Father_Name", ""))[:20]),
        ("Class & Sec:", str(student_data.get("Class", ""))),
        ("Due Date:", str(student_data.get("Due_Date", ""))),
    ]
    for label, val in student_meta:
      c.setFont("Helvetica-Bold", 6.8)
      c.setFillColor(HexColor("#475569"))
      c.drawString(col_x + 8, cur_y, label)
      c.setFont("Helvetica", 6.8)
      c.setFillColor(HexColor("#0F172A"))
      c.drawRightString(col_x + col_w - 10, cur_y, val)
      cur_y -= 12

    # Fee Breakdown Table Header
    cur_y -= 4
    c.setFillColor(HexColor("#0F172A"))
    c.rect(col_x + 6, cur_y - 2, col_w - 12, 14, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont("Helvetica-Bold", 6.8)
    c.drawString(col_x + 10, cur_y + 2, "Particulars / Fee Heads")
    c.drawRightString(col_x + col_w - 10, cur_y + 2, "Amount (PKR)")

    # 4 Dynamic Fee Heads
    items = [
        (head_1, tuition),
        (head_2, exam),
        (head_3, arrears),
        (head_4, other_fee),
    ]
    if discount > 0:
      items.append(("Sibling / Special Concession", -discount))

    c.setFillColor(HexColor("#1E293B"))
    for lbl, amt in items:
      cur_y -= 12.5
      c.setFont("Helvetica", 6.5)
      c.drawString(col_x + 10, cur_y, lbl[:24])
      amt_s = f"-{abs(amt):,}" if amt < 0 else f"{amt:,}"
      c.drawRightString(col_x + col_w - 10, cur_y, amt_s)

    # Total Box
    cur_y -= 16
    c.setFillColor(HexColor("#ECFDF5"))
    c.setStrokeColor(HexColor("#10B981"))
    c.roundRect(col_x + 6, cur_y - 3, col_w - 12, 16, 3, fill=1, stroke=1)
    c.setFillColor(HexColor("#065F46"))
    c.setFont("Helvetica-Bold", 7.2)
    c.drawString(col_x + 10, cur_y + 2, "TOTAL WITHIN DUE DATE:")
    c.drawRightString(col_x + col_w - 10, cur_y + 2, f"PKR {total_due:,}")

    cur_y -= 13
    c.setFont("Helvetica", 6.2)
    c.setFillColor(HexColor("#DC2626"))
    c.drawString(col_x + 10, cur_y, f"Amount After Due Date (+{late_fine}): PKR {after_due:,}")

    # QR Code Block
    cur_y -= 52
    if os.path.exists(qr_image_path):
      c.drawImage(qr_image_path, col_x + 8, cur_y, width=46, height=46, preserveAspectRatio=True)

    c.setFont("Helvetica-Bold", 6.5)
    c.setFillColor(HexColor("#0F172A"))
    c.drawString(col_x + 60, cur_y + 32, "Scan to Pay via 1Bill")
    c.setFont("Helvetica", 5.8)
    c.setFillColor(HexColor("#64748B"))
    c.drawString(col_x + 60, cur_y + 23, "Easypaisa, JazzCash &")
    c.drawString(col_x + 60, cur_y + 14, "all Mobile Banking Apps.")
    c.drawString(col_x + 60, cur_y + 5, f"1Bill ID: 100{roll}")

    # INSTITUTIONAL INSTRUCTIONS (FILLS BLANK AREA PROFICIENTLY WITH UP TO 6 LINES)
    box_height = 68
    cur_y -= (box_height + 8)
    c.setFillColor(HexColor("#F1F5F9"))
    c.setStrokeColor(HexColor("#E2E8F0"))
    c.roundRect(col_x + 6, cur_y, col_w - 12, box_height, 3, fill=1, stroke=1)

    c.setFillColor(HexColor("#0F172A"))
    c.setFont("Helvetica-Bold", 6.2)
    c.drawString(col_x + 10, cur_y + box_height - 11, "TERMS & INSTRUCTIONS:")

    c.setFont("Helvetica", 5.1)
    c.setFillColor(HexColor("#475569"))
    note_y = cur_y + box_height - 21
    for note in instructions[:6]:
      c.drawString(col_x + 10, note_y, f"• {str(note).strip()[:43]}")
      note_y -= 7.8

    # Signatures at bottom
    c.setStrokeColor(HexColor("#CBD5E1"))
    c.line(col_x + 10, margin_y + 24, col_x + 75, margin_y + 24)
    c.line(col_x + col_w - 75, margin_y + 24, col_x + col_w - 10, margin_y + 24)

    c.setFont("Helvetica", 5.8)
    c.setFillColor(HexColor("#64748B"))
    c.drawCentredString(col_x + 42, margin_y + 14, "Depositor Sign")
    c.drawCentredString(col_x + col_w - 42, margin_y + 14, "Authorized Cashier")

  c.save()
  return file_path


def generate_all_challans(excel_path, school_info, output_dir="generated_challans"):
  df = pd.read_excel(excel_path)
  for _, row in df.iterrows():
    generate_single_student_challan(row, school_info, output_dir=output_dir)