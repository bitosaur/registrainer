# Registrainer

A GUI-based tool to manage container registries, built with FastAPI and Bootstrap. It allows users to browse repositories, view images and tags, display SHAs on hover, and delete images with confirmation.

## Features
- Add and manage secure/insecure registries.
- List repositories and tags.
- Display image SHAs on hover.
- Delete images and stale tags.
- Save registry details in a config file (`registrainer.json`).

## Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/registrainer.git
   cd registrainer
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the application:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```
5. Open `http://localhost:8000` in a browser.

## License
MIT License