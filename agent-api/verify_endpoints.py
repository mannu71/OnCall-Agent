import urllib.request
import json
import sys

BASE_URL = "http://localhost:8000/api/v1/crawler"

def check_endpoint(url, method="GET", data=None):
    print(f"\n-> Testing {method} {url} ...")
    req = urllib.request.Request(url, method=method)
    if data is not None:
        req.add_header('Content-Type', 'application/json')
        jsondata = json.dumps(data).encode('utf-8')
        req.data = jsondata
        
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = response.read().decode('utf-8')
            parsed = json.loads(res_data)
            print("  [OK] Success!")
            return parsed
    except Exception as e:
        print(f"  [ERROR] Failed: {e}")
        return None

def main():
    print("============================================================")
    print("Verification Script: Testing Custom Graph API Endpoints")
    print("============================================================")
    
    # 1. Test GET /crawler/repos
    repos_res = check_endpoint(f"{BASE_URL}/repos")
    if not repos_res:
        print("Could not connect to FastAPI server. Ensure the container is active.")
        sys.exit(1)
        
    repos = repos_res.get("repos", [])
    if not repos:
        print("No repositories have been indexed yet. Cannot test file-specific endpoints.")
        print("Please run indexFlow for a repository first.")
        sys.exit(0)
        
    target_repo = repos[0]["repo_name"]
    print(f"\nSelected Target Repository: '{target_repo}'")
    
    # 2. Test GET /crawler/repos/{repo}/files
    files_url = f"{BASE_URL}/repos/{target_repo}/files?limit=5"
    files_res = check_endpoint(files_url)
    
    if not files_res or not files_res.get("files"):
        print("No files indexed for this repository. Cannot test symbol queries.")
        sys.exit(0)
        
    first_file = files_res["files"][0]["file"]
    print(f"Sample File Path: '{first_file}'")
    
    # 3. Test POST /crawler/repos/{repo}/diff-impact
    impact_url = f"{BASE_URL}/repos/{target_repo}/diff-impact"
    payload = {
        "files": [first_file],
        "symbols": []
    }
    check_endpoint(impact_url, method="POST", data=payload)
    
    print("\n============================================================")
    print("Verification Complete!")
    print("============================================================")

if __name__ == "__main__":
    main()
