import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "18151"))
    uvicorn.run("legacy_status:app", host="127.0.0.1", port=port, reload=False)
