# Registainer

A GUI-based tool to manage container registries, built with FastAPI and Tailwind CSS. It allows users to browse repositories, view images and tags, display SHAs on hover, and delete images with confirmation.

## Features
- Add and manage secure/insecure registries.
- List repositories and tags.
- Display image SHAs on hover.
- Delete images with confirmation.
- Save registry details in a config file.

## Installation
```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000