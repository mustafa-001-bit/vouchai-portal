import json
import os
import urllib.parse
import urllib.request


def send_real_whatsapp_message(
    phone_number,
    message_text,
    pdf_path=None,
    instance_id="",
    api_token="",
    gateway_url="https://api.ultramsg.com",
):
  """Universal WhatsApp Dispatch Engine.

  - If credentials are empty: Runs in safe DEMO / SIMULATION mode.
  - If credentials are set: Fired direct HTTP POST to deliver live WhatsApp
  messages.
  """
  clean_phone = (
      str(phone_number)
      .strip()
      .replace("+", "")
      .replace("-", "")
      .replace(" ", "")
  )

  # 1. Check if configured for Live Gateway
  if (
      not instance_id
      or not api_token
      or instance_id.strip() == ""
      or api_token.strip() == ""
  ):
    return {
        "success": True,
        "mode": "simulation",
        "message": "Delivered via Simulation Engine (Demo Mode)",
    }

  # 2. Live HTTP Request (Uses Python built-in urllib - Zero pip dependencies needed)
  try:
    endpoint = f"{gateway_url.strip().rstrip('/')}/{instance_id.strip()}/messages/chat"
    payload = {
        "token": api_token.strip(),
        "to": clean_phone,
        "body": message_text,
    }

    encoded_data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=encoded_data,
        headers={"User-Agent": "VouchAI-Institutional-Gateway"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=12) as response:
      res_body = response.read().decode("utf-8")
      res_json = json.loads(res_body)

      if "sent" in res_json and str(res_json.get("sent")).lower() == "true":
        return {
            "success": True,
            "mode": "live",
            "message": (
                f"Real WhatsApp Delivered! Message ID: {res_json.get('id', 'OK')}"
            ),
        }
      elif "error" in res_json:
        return {
            "success": False,
            "mode": "live",
            "message": f"Gateway Error: {res_json.get('error')}",
        }
      else:
        return {
            "success": True,
            "mode": "live",
            "message": "Dispatched to Live Gateway",
        }

  except Exception as e:
    return {"success": False, "mode": "live", "message": f"API Error: {str(e)}"}