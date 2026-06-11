import requests
import json
import sseclient

def test_upload():
    # 1. Start a new conversation
    res = requests.post("http://localhost:8090/api/conversations", json={"model": "gemma3:4b"})
    cid = res.json()["id"]
    
    # 2. Upload and get SSE
    import sys
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "scan/BNP_1760-228-ЭМ1 л.5.pdf"
    with open(pdf_path, "rb") as f:
        resp = requests.post(
            f"http://localhost:8090/api/conversations/{cid}/chat",
            data={"question": "Извлечь весь текст с листа", "model": "gemma3:4b"},
            files={"file": f},
            stream=True
        )
        
    client = sseclient.SSEClient(resp)
    for event in client.events():
        if event.data == "[DONE]":
            break
        data = json.loads(event.data)
        if "text" in data:
            print(data["text"], end="", flush=True)

if __name__ == "__main__":
    test_upload()