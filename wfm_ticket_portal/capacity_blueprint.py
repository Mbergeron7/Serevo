# Blueprint — what we need to build:

# 1. NEW FLASK ROUTES in wfm_ticket_portal.py:
#    GET  /capacity                  -> capacity control panel page
#    POST /capacity/refresh_forecast -> pull forecast from PW API -> write to Google Sheet
#    POST /capacity/refresh_requirements -> pull requirements from PW API -> write to Google Sheet  
#    POST /capacity/refresh_employees -> pull employees from PW API -> write to Google Sheet
#    GET  /capacity/plan             -> read cached sheets, compute plan, render page
#    GET  /api/capacity_status       -> return last refresh timestamps

# 2. NEW GOOGLE SHEET TABS (user creates these blank):
#    "FORECAST RAW"     - timestamp col B, LOB cols C+
#    "REQUIREMENTS RAW" - timestamp col B, LOB cols C+
#    "EMPLOYEES"        - employee roster

# 3. NEW TEMPLATES:
#    capacity_panel.html  - control panel with refresh buttons + status
#    capacity_plan.html   - the monthly capacity plan view

# WORKLOAD IDs from VBA (hardcoded - no need to call API for these):
WORKLOADS = {
    "I.T Support":                    "e2d2558b-0050-4719-8040-f55d6ad10c25",
    "MoveBuddy":                      "c3184f43-b095-4b13-a5be-371c48b83ead",
    "PS Care Combined":               "e13311bb-714d-498c-a27b-dc6d9bc7576d",
    "PS Care EN":                     "6913778e-ce1d-4ddb-97d8-93044d65f626",
    "PS Care FR":                     "63ee18a5-8eeb-4fd8-952b-ba9166db3fdd",
    "PS Case Manager":                "286243f3-6059-4eb4-99fb-286476921064",
    "PS Sales Combined":              "42248eab-5f8f-4b1e-a3ac-f2c6fe79e898",
    "PS Sales EN":                    "1aaeee54-85ef-447d-a2b9-f09a785806c7",
    "PS Sales FR":                    "99f9cec2-844d-48fa-84c8-b4af5605896e",
    "SS Case Management CC Combined": "450d20b5-1105-4316-9082-f3ca4b5f9cdf",
    "SS Case Management CC EN":       "978881de-68c2-4f63-b2dd-b1892471a29c",
    "SS Case Management CC FR":       "0e45cee2-7eff-4787-afda-00fee763ad17",
    "SS Case Management Combined":    "1c09003e-aba4-4c8e-bb25-7b8a5e583aaa",
    "SS Case Management Combined EN": "254aef98-e3b4-4c3d-9519-2c555194be0e",
    "SS Case Management Combined FR": "511cc324-dc8a-4e2f-8a50-52f1f2ae1c81",
    "SS Sales Combined":              "455a57fb-6664-4907-ba52-0fea4d123d3e",
    "SS Sales EN":                    "8cbe2728-a958-4677-930a-2e2554668004",
    "SS Sales FR":                    "d0a075a5-5a73-46b0-8c63-206dc6c0b275",
    "Web Leads PS Combined":          "da69b74f-3b0f-460a-b87a-576e5008d2a7",
    "Web Leads PS EN":                "9f56e267-68de-492b-a2c9-ef219feed864",
    "Web Leads PS FR":                "4d94afbe-8d92-4f04-a17b-f0843ec3beed",
    "Web Leads SS Combined":          "99db45c7-155e-484a-909c-cdb69a0581b8",
    "Web Leads SS EN":                "5f31de17-ba2e-4e11-88d0-548a1b4669ed",
    "Web Leads SS FR":                "e95ff578-25e9-40b3-956f-0efd434c85ce",
    "Web Leads SS Inbound Combined":  "26fc920a-1181-4b62-a71f-b43b104d8c2a",
    "Web Leads SS Inbound EN":        "8e61f24b-1fd6-4815-adbf-e560736d41af",
    "Web Leads SS Inbound FR":        "39221bbe-3524-44bb-b348-86e0c5ffe1e0",
}

# API structure:
# Forecast response:
# { "data": { "intervalDuration": "PT30M", 
#             "forecasts": { "auto": { "offered": { "values": [...] },
#                                      "averageHandlingTime": { "values": [...] } } } } }
#
# Requirements response (per planning unit per day):
# [ { "activity_id": "...", "raster": 1800, "values": [...] }, ... ]
# OR { "requirements": [ ... ] }
#
# Employees response:
# { "employees": [ { "id":..., "firstName":..., "lastName":..., 
#                    "planningUnit":..., "status":..., ... } ] }

print("Blueprint OK")
