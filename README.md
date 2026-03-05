# rag-backend

FastAPI backend for the RAG (Retrieval Augmented Generation) system.

## Stack

- **FastAPI** + **Gunicorn/Uvicorn** for the API server
- **SQLAlchemy** + **PostgreSQL** for metadata storage
- **Qdrant** for vector storage
- **Firecrawl** for web scraping
- **LangChain** + **sentence-transformers** for RAG pipeline
- **Ollama** for local LLM inference

## Local Development

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Set env vars (copy from .env.example)
cp .env.example .env

uvicorn main:app --reload --port 8000
```

## Environment Variables

See `.env.example` for all required variables.

| Variable        | Description                  |
| --------------- | ---------------------------- |
| `DATABASE_URL`  | PostgreSQL connection string |
| `QDRANT_HOST`   | Qdrant service hostname      |
| `FIRECRAWL_URL` | Local Firecrawl API URL      |
| `OLLAMA_URL`    | Ollama LLM service URL       |

## Docker

```bash
docker build -t rag-backend .
docker run -p 8000:8000 --env-file .env rag-backend
```

## CI/CD

On every push to `main`, GitHub Actions automatically builds and pushes the Docker image to `ghcr.io/your-org/rag-backend:latest`.
