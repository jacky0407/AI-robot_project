from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AI Chatbot Platform API")

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # TODO: restrict to frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Welcome to AI Chatbot Platform API"}

@app.get("/api/health")
def health_check():
    return {"status": "ok"}
