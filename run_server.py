import os
os.environ['GEMINI_API_KEY'] = 'AQ.Ab8RN8r74E2q-8bWArzGj_3w6mHqLGVx1S8e5gJ1hH7k8CJ4F2Z1n3oPqRsTuVwXyZ0123'
os.environ['GEMINI_MODEL'] = 'gemini-3.7-flash'

import uvicorn
from main import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
