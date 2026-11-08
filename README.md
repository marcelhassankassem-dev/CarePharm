# CarePharm

A web app for a small independent pharmacy and its patients. Built with Django and SQLite for the
EECE 430 project at AUB.

## Run with Docker

```bash
docker build -t carepharm .
docker run -p 8000:8000 carepharm
```

Then open http://localhost:8000/admin/.

## Run without Docker

Uses Python 3.12.

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

## Run the tests

```bash
python manage.py test
```

## Demo logins

The password for every demo user is `carepharm123`. These accounts exist only for the demo.

| Username | Who | Role |
|---|---|---|
| `nadim` | Owner and pharmacist, can open the Django admin | Owner |
| `rana` | Pharmacist | Pharmacist |
| `karim` | Assistant | Assistant |
| `samira` | Patient | Patient |
| `maya` | Patient | Patient |
| `omars_father` | Patient | Patient |
| `omar` | Caregiver, with no patient record of his own | Patient |
Ahmad is a demo patient with no login.
