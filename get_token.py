from core.token_manager import get_access_token
print("Getting Upstox token — browser will open...")
t = get_access_token()
print("SUCCESS. Token starts with:", t[:30])
input("Press Enter to close.")
