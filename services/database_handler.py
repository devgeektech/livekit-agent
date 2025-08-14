import requests

BASE_URL = "https://pocbackend.geektechies.com"

def fetch_session_data(uuid: str):
    url = f"{BASE_URL}/sessions/{uuid}/get_resume_and_jd"
    response = requests.get(url)
    
    if response.status_code == 200:
        data = response.json()
        return data["resume"], data["job_description"], data["language"]
    else:
        raise Exception(f"Error fetching data from {url}: {response.text}")



def save_session_message(session_uuid: str, role: str, message: str):
    url = f"{BASE_URL}/sessions/{session_uuid}/messages"
    payload = {
        "role": role,
        "message": message
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()  # Raises error for 4xx/5xx
        data = response.json()
        
        if data.get("status"):
            return True
        else:
            raise Exception(f"API Error: {data.get('error')}")
    
    except requests.RequestException as e:
        raise Exception(f"Request failed: {e}")


