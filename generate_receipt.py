from datetime import datetime
import os
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A5, landscape
from reportlab.pdfgen import canvas


def generate_paid_receipt(
    student_data, school_info, payment_info=None, output_dir="generated_receipts"
):
  """Generates an executive, digitally stamped Fee Clearance Receipt (A5 Landscape)

  with full support for Sibling Discount & Partial Payments.
  """
  os.makedirs(output_dir, exist_ok=True)

  roll = student_data["Roll_No"]
  student_name = str(student_data["Student_Name"])
  file_name = f"Receipt_{roll}_{student_name.replace(' ', '_')}.pdf"
  file_path = os.path.join(output_dir, file_name)

  # Extract financials
  tuition = int(student_data["Tuition_Fee"])
  exam = int(student_data["Exam_Fee"])
  arrears = int(student_data["Arrears"])
  gross_total = tuition + exam + arrears

  if payment_info is None:
    payment_info = {
        "payment_mode": "Cash Counter",
        "trx_id": f"TRX-{datetime.now().strftime('%Y%m%d%H%M')}-{roll}",
        "payment_date": datetime.now().strftime("%d-%b-%Y"),
        "discount": int(student_data.get("Discount", 0)),
        "paid_amount": int(student_data.get("Paid_Amount", gross_total)),
        "remaining_balance": int(student_data.get("Remaining_Balance", 0)),
        "is_partial": False,
    }

  discount = int(payment_info.get("discount", 0))
  paid_amount = int(payment_info.get("paid_amount", gross_total - discount))
  remaining_balance = int(payment_info.get("remaining_balance", 0))
  is_partial = remaining_balance > 0

  # Setup Canvas (A5 Landscape: 595.27 x 419.52 pt)
  c = canvas.Canvas(file_path, pagesize=landscape(A5))
  width, height = landscape(A5)

  margin = 18
  usable_w = width - (margin * 2)
  usable_h = height - (margin * 2)

  # Outer Borders
  c.setStrokeColor(HexColor("#0F766E" if not is_partial else "#D97706"))
  c.setLineWidth(2)
  c.roundRect(margin, margin, usable_w, usable_h, 8, fill=0, stroke=1)

  c.setStrokeColor(HexColor("#CBD5E1"))
  c.setLineWidth(0.6)
  c.roundRect(
      margin + 4, margin + 4, usable_w - 8, usable_h - 8, 6, fill=0, stroke=1
  )

  # Header Banner
  c.setFillColor(HexColor("#0F172A"))
  c.roundRect(
      margin + 6, height - margin - 58, usable_w - 12, 52, 5, fill=1, stroke=0
  )

  logo_path = school_info.get("logo_path")
  if logo_path and os.path.exists(logo_path):
    c.drawImage(
        logo_path,
        margin + 16,
        height - margin - 52,
        width=40,
        height=40,
        preserveAspectRatio=True,
        mask="auto",
    )
    header_x = margin + 68
    align_center = False
  else:
    header_x = width / 2
    align_center = True

  c.setFillColor(HexColor("#FFFFFF"))
  c.setFont("Helvetica-Bold", 14)
  if align_center:
    c.drawCentredString(
        header_x,
        height - margin - 26,
        str(school_info.get("school_name", "")).upper(),
    )
  else:
    c.drawString(
        header_x,
        height - margin - 26,
        str(school_info.get("school_name", "")).upper(),
    )

  c.setFillColor(HexColor("#94A3B8"))
  c.setFont("Helvetica", 8.5)
  receipt_title = (
      "Partial Fee Settlement Acknowledgment"
      if is_partial
      else "Official Fee Clearance Receipt"
  )
  if align_center:
    c.drawCentredString(
        header_x,
        height - margin - 42,
        f"{school_info.get('campus', '')}  |  {receipt_title}",
    )
  else:
    c.drawString(
        header_x,
        height - margin - 42,
        f"{school_info.get('campus', '')}  |  {receipt_title}",
    )

  # Reference Bar
  cur_y = height - margin - 75
  c.setFillColor(HexColor("#F8FAFC"))
  c.roundRect(margin + 6, cur_y - 8, usable_w - 12, 22, 4, fill=1, stroke=0)

  c.setFillColor(HexColor("#334155"))
  c.setFont("Helvetica-Bold", 8.5)
  c.drawString(
      margin + 14, cur_y, f"Receipt No: REC-2026-{roll}"
  )
  c.drawString(
      margin + 180, cur_y, f"Date: {payment_info.get('payment_date')}"
  )
  c.drawRightString(
      width - margin - 14,
      cur_y,
      f"Billing Period: {school_info.get('month', 'October 2026')}",
  )

  # Student Details
  cur_y -= 26
  c.setFont("Helvetica-Bold", 8.5)
  c.setFillColor(HexColor("#0F172A"))
  c.drawString(margin + 14, cur_y, "Student Name:")
  c.setFont("Helvetica", 8.5)
  c.drawString(margin + 85, cur_y, student_name)

  c.setFont("Helvetica-Bold", 8.5)
  c.drawString(margin + 260, cur_y, "Roll No:")
  c.setFont("Helvetica", 8.5)
  c.drawString(margin + 310, cur_y, str(roll))

  cur_y -= 16
  c.setFont("Helvetica-Bold", 8.5)
  c.drawString(margin + 14, cur_y, "Father Name:")
  c.setFont("Helvetica", 8.5)
  c.drawString(margin + 85, cur_y, str(student_data["Father_Name"]))

  c.setFont("Helvetica-Bold", 8.5)
  c.drawString(margin + 260, cur_y, "Class:")
  c.setFont("Helvetica", 8.5)
  c.drawString(margin + 310, cur_y, str(student_data["Class"]))

  cur_y -= 12
  c.setStrokeColor(HexColor("#E2E8F0"))
  c.line(margin + 6, cur_y, width - margin - 6, cur_y)

  # Particulars Table Header
  cur_y -= 18
  c.setFillColor(HexColor("#0F766E" if not is_partial else "#0F172A"))
  c.roundRect(margin + 6, cur_y - 4, usable_w - 12, 18, 3, fill=1, stroke=0)

  c.setFillColor(HexColor("#FFFFFF"))
  c.setFont("Helvetica-Bold", 8)
  c.drawString(margin + 16, cur_y + 1, "Particulars / Fee Breakdown")
  c.drawRightString(width - margin - 16, cur_y + 1, "Amount (PKR)")

  # Particulars Rows
  rows = [
      ("Tuition Fee", tuition),
      ("Examination Fee", exam),
      ("Previous Arrears Cleared", arrears),
  ]
  if discount > 0:
    rows.append(("Sibling / Special Concession Discount", -discount))

  c.setFillColor(HexColor("#1E293B"))
  for label, amt in rows:
    cur_y -= 16
    c.setFont("Helvetica", 8)
    c.drawString(margin + 16, cur_y, label)
    amt_str = f"-{abs(amt):,}" if amt < 0 else f"{amt:,}"
    c.drawRightString(width - margin - 16, cur_y, amt_str)

  # Net Received Box
  cur_y -= 22
  box_bg = HexColor("#ECFDF5" if not is_partial else "#FEF3C7")
  box_border = HexColor("#10B981" if not is_partial else "#F59E0B")
  c.setFillColor(box_bg)
  c.setStrokeColor(box_border)
  c.roundRect(
      margin + 6, cur_y - 6, usable_w - 12, 22, 4, fill=1, stroke=1
  )

  c.setFillColor(HexColor("#065F46" if not is_partial else "#92400E"))
  c.setFont("Helvetica-Bold", 9)
  c.drawString(margin + 16, cur_y, "AMOUNT SETTLED & RECEIVED:")
  c.drawRightString(
      width - margin - 16, cur_y, f"PKR {paid_amount:,}"
  )

  # Remaining Balance Notice if Partial
  if is_partial:
    cur_y -= 16
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(HexColor("#DC2626"))
    c.drawString(
        margin + 16,
        cur_y,
        f"⚠️ REMAINING OUTSTANDING DUES (Carried Forward): PKR"
        f" {remaining_balance:,}",
    )

  # Payment Metadata
  cur_y -= 22
  c.setFont("Helvetica-Bold", 7.5)
  c.setFillColor(HexColor("#475569"))
  c.drawString(
      margin + 14, cur_y, f"Payment Method: {payment_info.get('payment_mode')}"
  )
  cur_y -= 12
  c.drawString(
      margin + 14, cur_y, f"Bank Reference / TRX ID: {payment_info.get('trx_id')}"
  )

  # Digital Seal / Stamp
  stamp_cx = width - margin - 110
  stamp_cy = cur_y + 16

  stamp_color = HexColor("#059669" if not is_partial else "#D97706")
  c.setStrokeColor(stamp_color)
  c.setLineWidth(1.6)
  c.roundRect(stamp_cx - 55, stamp_cy - 22, 110, 44, 6, fill=0, stroke=1)
  c.setLineWidth(0.8)
  c.roundRect(stamp_cx - 51, stamp_cy - 18, 102, 36, 4, fill=0, stroke=1)

  c.setFillColor(stamp_color)
  c.setFont("Helvetica-Bold", 10 if not is_partial else 9)
  c.drawCentredString(
      stamp_cx,
      stamp_cy + 3,
      "PAID & VERIFIED" if not is_partial else "PARTIALLY PAID",
  )
  c.setFont("Helvetica-Bold", 6.2)
  c.drawCentredString(stamp_cx, stamp_cy - 8, "OFFICIAL INSTITUTIONAL SEAL")

  # Footer note
  cur_y -= 24
  c.setFont("Helvetica-Oblique", 6.8)
  c.setFillColor(HexColor("#64748B"))
  c.drawString(
      margin + 14,
      cur_y,
      "This is a system-generated computer receipt and does not require a"
      " physical signature. Accounts Clearance Verified.",
  )

  c.save()
  return file_path